import os
import secrets
import threading
import time
from functools import wraps

import mysql.connector
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, Response, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
    from sensor_msgs.msg import CompressedImage, BatteryState #배터리 스테이트 추가
    from std_msgs.msg import String, Int32MultiArray
    from nav_msgs.msg import Odometry
    from geometry_msgs.msg import Twist
    ROS_AVAILABLE = True
except Exception:
    ROS_AVAILABLE = False


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", "patrol_dashboard"),
}


def get_db():
    return mysql.connector.connect(**DB_CONFIG)


# ===== 개인정보 필드 암호화 (이름/전화번호/보호자명) =====
# FIELD_ENCRYPT_KEY가 없으면 서버를 못 켜게 해서, 암호화 없이 개인정보가 저장되는 걸 막는다
_encrypt_key = os.environ.get("FIELD_ENCRYPT_KEY")
if not _encrypt_key:
    raise RuntimeError(
        "FIELD_ENCRYPT_KEY 환경변수가 설정되지 않았습니다. "
        "python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
        "로 키를 만들어서 export FIELD_ENCRYPT_KEY=... 로 지정해 주세요."
    )
_fernet = Fernet(_encrypt_key.encode())


def encrypt_field(value):
    if value is None or value == "":
        return None
    return _fernet.encrypt(str(value).encode()).decode()


def decrypt_field(value):
    if value is None:
        return None
    try:
        return _fernet.decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return value  # 암호화 이전에 평문으로 들어간 값 등 — 그대로 반환


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return jsonify({"ok": False, "error": "로그인이 필요합니다"}), 401
        return view_func(*args, **kwargs)
    return wrapped


latest_frame = None
latest_annotated_frame = None
last_heartbeat = 0.0

NETWORK_TIMEOUT_SEC = 4.0

LINEAR_MOVING_THRESHOLD = 0.02
ANGULAR_MOVING_THRESHOLD = 0.05

CMD_VEL_TIMEOUT_SEC = 1.0

# 낙상 판정이 짧게 흔들려(FALL->PERSON->FALL) 같은 낙상이 중복 기록되지 않도록 하는 쿨다운
FALL_EVENT_COOLDOWN_SEC = 15.0
last_fall_event_time = 0.0

last_odom_linear = 0.0
last_odom_angular = 0.0
last_cmd_vel_linear = 0.0
last_cmd_vel_angular = 0.0
last_cmd_vel_time = 0.0

# 마커 ID -> 병실 번호 매핑. 실제 인쇄한 마커 ID에 맞게 값 수정
MARKER_TO_ROOM = {
    0: "101",
    1: "102",
    2: "103",
    3: "104",
}

# my_patrol의 rooms.yaml 방 이름(room1~4) -> 병실 번호 매핑
PATROL_ROOM_NAME_TO_NUMBER = {
    "room1": "101",
    "room2": "102",
    "room3": "103",
    "room4": "104",
}

state = {
    "current_room": None,
    "robot_status": "대기 중",
    "fall_status": "정상",
    "battery": "배터리 대기",
    "camera": "카메라 대기",
    "network": "네트워크 대기",
    "fall_alert_id": 0,
    "events": []
}


def add_event(text):
    now = time.strftime("%H:%M:%S")
    state["events"].insert(0, {"time": now, "text": text})
    state["events"] = state["events"][:10]


def log_patrol_complete(room):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO patrol_log (room_number) VALUES (%s)", (room,))
        conn.commit()
        cursor.close()
        conn.close()
    except mysql.connector.Error as e:
        print(f"[dashboard] patrol_log 기록 실패: {e}")


class DashboardBridge(Node):
    def __init__(self):
        super().__init__("dashboard_bridge")

        image_qos = QoSProfile(
            depth=1,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
        )

        self.create_subscription(
            CompressedImage,
            "/image_raw/compressed",
            self.image_callback,
            image_qos,
        )

        self.create_subscription(
            CompressedImage,
            "/image_annotated/compressed",
            self.annotated_image_callback,
            image_qos,
        )

        self.create_subscription(
            Int32MultiArray,
            "/room_marker",
            self.room_marker_callback,
            10
        )

        self.create_subscription(
            Odometry,
            "/odom",
            self.odom_callback,
            10
        )

        self.create_subscription(
            Twist,
            "/cmd_vel",
            self.cmd_vel_callback,
            10
        )

        self.create_subscription(
            String,
            "/fall_status",
            self.fall_status_callback,
            10
        )

        self.create_subscription(
            BatteryState,
            "/battery_state",
            self.battery_callback,
            10
        )

        self.create_subscription(
            String,
            "/patrol_complete",
            self.patrol_complete_callback,
            10
        )

        self.create_timer(1.0, self.check_network_callback)
        self.create_timer(1.0, self.check_movement_callback)

        self.get_logger().info("dashboard_bridge started")

    def check_network_callback(self):
        elapsed = time.time() - last_heartbeat
        old_network = state["network"]
        new_network = "네트워크 연결" if elapsed < NETWORK_TIMEOUT_SEC else "네트워크 대기"
        state["network"] = new_network

        if old_network != new_network:
            add_event(f"네트워크 상태 변경: {new_network}")

    def image_callback(self, msg):
        global latest_frame, last_heartbeat
        latest_frame = bytes(msg.data)
        last_heartbeat = time.time()
        state["camera"] = "카메라 정상"

    def annotated_image_callback(self, msg):
        global latest_annotated_frame
        latest_annotated_frame = bytes(msg.data)

    def room_marker_callback(self, msg):
        if not msg.data:
            return

        new_room = MARKER_TO_ROOM.get(msg.data[0])
        if new_room is None:
            return

        old_room = state["current_room"]
        state["current_room"] = new_room

        if old_room != new_room:
            add_event(f"로봇이 병실 {new_room}에 입장했습니다.")

    def patrol_complete_callback(self, msg):
        # my_patrol의 patrol_node가 한 병실 관찰(scan_for_fall)을 끝내고
        # 복도로 돌아가기 직전에 쏘는 신호. 이 시점이 "이 병실 순찰 완료".
        room_name = str(msg.data).strip()
        room_number = PATROL_ROOM_NAME_TO_NUMBER.get(room_name, room_name)
        log_patrol_complete(room_number)
        add_event(f"병실 {room_number} 순찰을 완료했습니다.")

    def odom_callback(self, msg):
        global last_heartbeat, last_odom_linear, last_odom_angular
        last_heartbeat = time.time()
        last_odom_linear = msg.twist.twist.linear.x
        last_odom_angular = msg.twist.twist.angular.z

    def cmd_vel_callback(self, msg):
        global last_heartbeat, last_cmd_vel_linear, last_cmd_vel_angular, last_cmd_vel_time
        last_heartbeat = time.time()
        last_cmd_vel_linear = msg.linear.x
        last_cmd_vel_angular = msg.angular.z
        last_cmd_vel_time = time.time()

    def check_movement_callback(self):
        odom_moving = (
            abs(last_odom_linear) > LINEAR_MOVING_THRESHOLD
            or abs(last_odom_angular) > ANGULAR_MOVING_THRESHOLD
        )
        cmd_vel_recent = (time.time() - last_cmd_vel_time) < CMD_VEL_TIMEOUT_SEC
        cmd_vel_moving = cmd_vel_recent and (
            abs(last_cmd_vel_linear) > LINEAR_MOVING_THRESHOLD
            or abs(last_cmd_vel_angular) > ANGULAR_MOVING_THRESHOLD
        )

        old_status = state["robot_status"]
        new_status = "이동 중" if (odom_moving or cmd_vel_moving) else "대기 중"
        state["robot_status"] = new_status

    def fall_status_callback(self, msg):
        global last_fall_event_time
        raw_status = str(msg.data).strip()
        old_status = state["fall_status"]
        new_status = "낙상 환자 발견" if raw_status == "FALL" else "정상"
        state["fall_status"] = new_status

        if new_status == "낙상 환자 발견" and old_status != "낙상 환자 발견":
            now = time.time()
            if now - last_fall_event_time >= FALL_EVENT_COOLDOWN_SEC:
                add_event(f"병실 {state['current_room']} 낙상 환자 발견")
                state["fall_alert_id"] += 1
            last_fall_event_time = now

    def battery_callback(self, msg):
        global last_heartbeat
        last_heartbeat = time.time()

        battery_percent = int(round(msg.percentage))
        state["battery"] = f"{battery_percent}%"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/video_feed")
def video_feed():
    def generate():
        while True:
            if latest_frame is not None:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" +
                    latest_frame +
                    b"\r\n"
                )
            time.sleep(0.03)

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/video_feed_yolo")
def video_feed_yolo():
    def generate():
        while True:
            if latest_annotated_frame is not None:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" +
                    latest_annotated_frame +
                    b"\r\n"
                )
            time.sleep(0.03)

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    remember = bool(body.get("remember"))

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if user is None or not check_password_hash(user["password_hash"], password):
        return jsonify({"ok": False, "error": "아이디 또는 비밀번호가 올바르지 않습니다."}), 401

    session.permanent = remember
    session["user"] = user["username"]
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    return jsonify({"ok": True, "username": user["username"], "role": user["role"]})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


def generate_mapping_code():
    return "KJ-" + secrets.token_hex(2).upper() + "-" + secrets.token_hex(2).upper()


# ===== 보호자 연동 신청 -> 승인(매핑코드) -> 코드로 계정 생성 =====

@app.route("/api/apply", methods=["POST"])
def api_apply():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()
    phone = str(body.get("phone", "")).strip()
    patient = str(body.get("patient", "")).strip()
    room = str(body.get("room", "")).strip()

    if not name or not phone or not patient or not room:
        return jsonify({"ok": False, "error": "모든 항목을 입력해 주세요."}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO guardian_applications (applicant_name, phone, patient_name, room_number) VALUES (%s, %s, %s, %s)",
        (encrypt_field(name), encrypt_field(phone), encrypt_field(patient), room),
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/applications")
@login_required
def api_applications():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, applicant_name, phone, patient_name, room_number, status, mapping_code, applied_at "
        "FROM guardian_applications ORDER BY id DESC"
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    applications = [{
        "id": r["id"],
        "name": decrypt_field(r["applicant_name"]),
        "phone": decrypt_field(r["phone"]),
        "patient": f"{decrypt_field(r['patient_name'])} ({r['room_number']}호)",
        "at": r["applied_at"].strftime("%Y-%m-%d %H:%M") if r["applied_at"] else "",
        "status": r["status"],
        "code": r["mapping_code"],
    } for r in rows]
    return jsonify({"ok": True, "applications": applications})


@app.route("/api/applications/<int:app_id>/approve", methods=["POST"])
@login_required
def api_application_approve(app_id):
    conn = get_db()
    cursor = conn.cursor()

    code = None
    for _ in range(20):
        candidate = generate_mapping_code()
        cursor.execute("SELECT id FROM guardian_applications WHERE mapping_code = %s", (candidate,))
        if cursor.fetchone() is None:
            code = candidate
            break
    if code is None:
        cursor.close()
        conn.close()
        return jsonify({"ok": False, "error": "매핑 코드를 만들지 못했습니다. 다시 시도해 주세요."}), 500

    cursor.execute(
        "UPDATE guardian_applications SET status = 'approved', mapping_code = %s WHERE id = %s AND status = 'pending'",
        (code, app_id),
    )
    conn.commit()
    updated = cursor.rowcount
    cursor.close()
    conn.close()

    if not updated:
        return jsonify({"ok": False, "error": "이미 처리된 신청입니다."}), 400
    return jsonify({"ok": True, "code": code})


@app.route("/api/applications/<int:app_id>/reject", methods=["POST"])
@login_required
def api_application_reject(app_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM guardian_applications WHERE id = %s AND status = 'pending'",
        (app_id,),
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/verify-code", methods=["POST"])
def api_verify_code():
    body = request.get_json(silent=True) or {}
    code = str(body.get("code", "")).strip().upper()
    if not code:
        return jsonify({"ok": False, "error": "매핑 코드를 입력해 주세요."}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT applicant_name, patient_name, room_number, status FROM guardian_applications WHERE mapping_code = %s",
        (code,),
    )
    app_row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not app_row or app_row["status"] == "rejected":
        return jsonify({"ok": False, "error": "승인된 코드를 찾을 수 없습니다. 문자로 받은 코드를 확인해 주세요."}), 404
    if app_row["status"] == "registered":
        return jsonify({"ok": False, "error": "이미 계정이 만들어진 코드입니다. 로그인해 주세요."}), 400

    return jsonify({
        "ok": True,
        "name": decrypt_field(app_row["applicant_name"]),
        "patient": f"{decrypt_field(app_row['patient_name'])} ({app_row['room_number']}호)",
    })


@app.route("/api/redeem", methods=["POST"])
def api_redeem():
    body = request.get_json(silent=True) or {}
    code = str(body.get("code", "")).strip().upper()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))

    if not code or not username or not password:
        return jsonify({"ok": False, "error": "필요한 값이 누락되었습니다."}), 400

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, applicant_name, phone, patient_name, room_number, status "
            "FROM guardian_applications WHERE mapping_code = %s",
            (code,),
        )
        app_row = cursor.fetchone()
        if not app_row or app_row["status"] != "approved":
            return jsonify({"ok": False, "error": "유효한 승인 코드가 아닙니다."}), 400

        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone() is not None:
            return jsonify({"ok": False, "error": "이미 사용 중인 아이디입니다."}), 400

        write_cursor = conn.cursor()
        write_cursor.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'user')",
            (username, generate_password_hash(password)),
        )
        user_id = write_cursor.lastrowid

        write_cursor.execute(
            "INSERT INTO patients (name, phone, room_number, guardian, user_id) VALUES (%s, %s, %s, %s, %s)",
            (
                encrypt_field(decrypt_field(app_row["patient_name"])),
                encrypt_field(decrypt_field(app_row["phone"])),
                app_row["room_number"],
                encrypt_field(decrypt_field(app_row["applicant_name"])),
                user_id,
            ),
        )
        write_cursor.execute(
            "DELETE FROM guardian_applications WHERE id = %s",
            (app_row["id"],),
        )
        conn.commit()
        write_cursor.close()
    except mysql.connector.Error:
        conn.rollback()
        return jsonify({"ok": False, "error": "가입 중 오류가 발생했습니다."}), 500
    finally:
        cursor.close()
        conn.close()

    patient_display = f"{decrypt_field(app_row['patient_name'])} ({app_row['room_number']}호)"
    return jsonify({"ok": True, "patient": patient_display})


@app.route("/api/session")
def api_session():
    if session.get("user"):
        return jsonify({"authenticated": True, "username": session["user"], "role": session.get("role")})
    return jsonify({"authenticated": False})


@app.route("/api/status")
@login_required
def api_status():
    # 분석 프레임을 한 번이라도 받은 적 있으면 계속 true (꺼져도 마지막 프레임 유지)
    state["yolo_signal"] = latest_annotated_frame is not None
    return jsonify(state)


@app.route("/api/guardian-accounts")
@login_required
def api_guardian_accounts():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT u.id, u.username, p.name, p.room_number
        FROM users u
        LEFT JOIN patients p ON p.user_id = u.id
        WHERE u.role != 'admin'
        ORDER BY u.id
    """)
    accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    for a in accounts:
        a["name"] = decrypt_field(a["name"])
    return jsonify({"ok": True, "accounts": accounts})


@app.route("/api/guardian-accounts/<int:user_id>/delete", methods=["POST"])
@login_required
def api_guardian_account_delete(user_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM patients WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM users WHERE id = %s AND role != 'admin'", (user_id,))
        conn.commit()
    except mysql.connector.Error:
        conn.rollback()
        return jsonify({"ok": False, "error": "삭제 중 오류가 발생했습니다."}), 500
    finally:
        cursor.close()
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/patrol-log")
@login_required
def api_patrol_log():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    if session.get("role") == "admin":
        cursor.execute(
            "SELECT room_number, patrolled_at FROM patrol_log ORDER BY patrolled_at DESC LIMIT 50"
        )
    else:
        cursor.execute(
            """
            SELECT pl.room_number, pl.patrolled_at
            FROM patrol_log pl
            JOIN patients p ON p.room_number = pl.room_number
            WHERE p.user_id = %s
            ORDER BY pl.patrolled_at DESC
            LIMIT 20
            """,
            (session.get("user_id"),),
        )
    logs = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify({"ok": True, "logs": logs})


@app.route("/api/my-patient")
@login_required
def api_my_patient():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT name, room_number, age, sex, disease, risk_level, phone, guardian FROM patients WHERE user_id = %s",
        (session.get("user_id"),),
    )
    patient = cursor.fetchone()
    cursor.close()
    conn.close()
    if patient:
        patient["name"] = decrypt_field(patient["name"])
        patient["phone"] = decrypt_field(patient["phone"])
        patient["guardian"] = decrypt_field(patient["guardian"])
    return jsonify({"ok": True, "patient": patient})


# ===== 캘린더 일정: DB(calendar_events)에 저장 (여러 브라우저가 같은 일정을 공유) =====

@app.route("/api/events", methods=["GET"])
@login_required
def api_events_get():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ce.id, ce.event_date, ce.text, u.username
        FROM calendar_events ce
        LEFT JOIN users u ON u.id = ce.created_by
        ORDER BY ce.event_date, ce.id
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    events = {}
    for event_id, event_date, text, created_by in rows:
        events.setdefault(event_date.isoformat(), []).append(
            {"id": event_id, "text": text, "created_by": created_by}
        )
    return jsonify(events)


@app.route("/api/events", methods=["POST"])
@login_required
def api_events_add():
    body = request.get_json(silent=True) or {}
    date = str(body.get("date", "")).strip()
    text = str(body.get("text", "")).strip()
    if not date or not text:
        return jsonify({"ok": False, "error": "date와 text가 필요합니다"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO calendar_events (event_date, text, created_by) VALUES (%s, %s, %s)",
        (date, text, session.get("user_id")),
    )
    conn.commit()
    event = {"id": cursor.lastrowid, "text": text, "created_by": session.get("user")}
    cursor.close()
    conn.close()
    return jsonify({"ok": True, "date": date, "event": event})


@app.route("/api/events/delete", methods=["POST"])
@login_required
def api_events_delete():
    body = request.get_json(silent=True) or {}
    event_id = body.get("id")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM calendar_events WHERE id = %s", (event_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"ok": True})


# ===== 체크리스트: DB(checklist)에 저장 (여러 브라우저 공유) =====

@app.route("/api/notes", methods=["GET"])
@login_required
def api_notes_get():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, text, done FROM checklist ORDER BY id")
    checklist = [{"id": r[0], "text": r[1], "done": bool(r[2])} for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return jsonify({"checklist": checklist})


@app.route("/api/checklist/add", methods=["POST"])
@login_required
def api_checklist_add():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    if not text:
        return jsonify({"ok": False, "error": "text가 필요합니다"}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO checklist (text, done) VALUES (%s, FALSE)", (text,))
    conn.commit()
    item = {"id": cursor.lastrowid, "text": text, "done": False}
    cursor.close()
    conn.close()
    return jsonify({"ok": True, "item": item})


@app.route("/api/checklist/toggle", methods=["POST"])
@login_required
def api_checklist_toggle():
    body = request.get_json(silent=True) or {}
    item_id = body.get("id")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE checklist SET done = NOT done WHERE id = %s", (item_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/checklist/delete", methods=["POST"])
@login_required
def api_checklist_delete():
    body = request.get_json(silent=True) or {}
    item_id = body.get("id")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM checklist WHERE id = %s", (item_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"ok": True})


def ros_spin():
    if not ROS_AVAILABLE:
        add_event("ROS2 모듈 없음: 웹 화면만 테스트 중")
        return

    rclpy.init()
    node = DashboardBridge()

    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


def main():
    add_event("대시보드 서버 시작")

    ros_thread = threading.Thread(target=ros_spin, daemon=True)
    ros_thread.start()

    app.run(host="0.0.0.0", port=5000, threaded=True)


if __name__ == "__main__":
    main()
