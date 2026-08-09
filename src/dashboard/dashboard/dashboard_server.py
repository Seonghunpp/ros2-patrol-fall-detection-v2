import datetime
import os
import secrets
import threading
import time
from functools import wraps

import mysql.connector
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, Response, jsonify, render_template, request, send_from_directory, session
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


app = Flask(__name__, static_url_path="")
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


# 낙상 캡처 이미지 저장 위치. static/ 밑에 두지 않는다 — static은 로그인 없이 누구나 접근 가능해서
# 병실 안이 찍힌 사진을 거기 두면 안 됨. /api/fall-log/<id>/capture 라우트로만 (로그인 필요) 서빙한다.
CAPTURE_DIR = os.environ.get("FALL_CAPTURE_DIR", os.path.expanduser("~/.dabom/captures"))
os.makedirs(CAPTURE_DIR, exist_ok=True)


def log_fall_detected(room):
    # 카메라 프레임이 없어도(꺼져있거나 일시적으로 끊겨도) 낙상이 있었다는 기록 자체는 남긴다.
    # capture_path만 NULL로 남고, 화면에서는 "캡처 이미지 없음"으로 표시된다.
    frame = latest_annotated_frame or latest_frame
    filename = None
    if frame is None:
        print("[dashboard] 카메라 프레임 없음: 캡처 이미지 없이 fall_log만 기록")
    else:
        filename = f"fall_{int(time.time() * 1000)}.jpg"
        try:
            with open(os.path.join(CAPTURE_DIR, filename), "wb") as f:
                f.write(frame)
        except OSError as e:
            print(f"[dashboard] 캡처 이미지 저장 실패: {e}")
            filename = None

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO fall_log (room_number, capture_path) VALUES (%s, %s)",
            (room, filename),
        )
        conn.commit()
        cursor.close()
        conn.close()
    except mysql.connector.Error as e:
        print(f"[dashboard] fall_log 기록 실패: {e}")


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
                log_fall_detected(state["current_room"])
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

    # 관리자가 '환자 관리'에 등록해 둔 환자(병실+이름 일치)만 연동 신청을 받는다
    patient_id = find_patient_id_by_name_room(conn, patient, room)
    if patient_id is None:
        conn.close()
        return jsonify({
            "ok": False,
            "error": "등록된 환자 정보와 일치하지 않습니다. 환자 성함과 병실 번호를 다시 확인해 주세요.",
        }), 400

    # 이미 보호자 계정이 연동된 환자면 또 신청받지 않는다
    check_cursor = conn.cursor()
    check_cursor.execute("SELECT user_id FROM patients WHERE id = %s", (patient_id,))
    already_linked = check_cursor.fetchone()[0] is not None
    check_cursor.close()
    if already_linked:
        conn.close()
        return jsonify({"ok": False, "error": "이미 보호자 계정이 연동된 환자입니다."}), 400

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
        "FROM guardian_applications WHERE status IN ('pending', 'approved') ORDER BY id DESC"
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

        patient_name = decrypt_field(app_row["patient_name"])
        existing_patient_id = find_patient_id_by_name_room(conn, patient_name, app_row["room_number"])
        if existing_patient_id:
            # 관리자가 나이·성별·병명·위험도를 먼저 등록해 둔 환자일 수 있으므로 그 값은 건드리지 않는다
            write_cursor.execute(
                "UPDATE patients SET phone = %s, guardian = %s, user_id = %s WHERE id = %s",
                (
                    encrypt_field(decrypt_field(app_row["phone"])),
                    encrypt_field(decrypt_field(app_row["applicant_name"])),
                    user_id,
                    existing_patient_id,
                ),
            )
        else:
            write_cursor.execute(
                "INSERT INTO patients (name, phone, room_number, guardian, user_id) VALUES (%s, %s, %s, %s, %s)",
                (
                    encrypt_field(patient_name),
                    encrypt_field(decrypt_field(app_row["phone"])),
                    app_row["room_number"],
                    encrypt_field(decrypt_field(app_row["applicant_name"])),
                    user_id,
                ),
            )
        write_cursor.execute(
            "UPDATE guardian_applications SET status = 'registered', user_id = %s WHERE id = %s",
            (user_id, app_row["id"]),
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
        SELECT u.id, u.username, p.name, p.room_number, p.guardian, ga.mapping_code AS code
        FROM users u
        LEFT JOIN patients p ON p.user_id = u.id
        LEFT JOIN guardian_applications ga ON ga.user_id = u.id
        WHERE u.role != 'admin'
        ORDER BY u.id
    """)
    accounts = cursor.fetchall()
    cursor.close()
    conn.close()
    for a in accounts:
        a["name"] = decrypt_field(a["name"])
        a["guardian"] = decrypt_field(a["guardian"])
    return jsonify({"ok": True, "accounts": accounts})


@app.route("/api/guardian-accounts/<int:user_id>/delete", methods=["POST"])
@login_required
def api_guardian_account_delete(user_id):
    conn = get_db()
    cursor = conn.cursor()
    try:
        # 계정만 지우고 환자 정보(patients)는 남긴다 — 환자 자체를 지우는 건 '환자 관리 → 퇴원 처리'의 역할
        cursor.execute("UPDATE patients SET user_id = NULL WHERE user_id = %s", (user_id,))
        # 계정이 사라지면 그 계정의 가입 신청/코드 기록도 더는 의미가 없으므로 같이 지운다
        cursor.execute("DELETE FROM guardian_applications WHERE user_id = %s", (user_id,))
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


# name은 암호화되어 있어 SQL로 직접 비교할 수 없으므로, 같은 병실의 행을 복호화해서 이름을 비교한다.
# 보호자 연동(코드 등록)과 관리자 환자 등록이 같은 환자를 각자 따로 만들지 않도록 이 함수로 먼저 찾는다.
def find_patient_id_by_name_room(conn, name, room):
    normalized = str(name).replace(" ", "")
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name FROM patients WHERE room_number = %s", (room,))
    rows = cursor.fetchall()
    cursor.close()
    for row in rows:
        if decrypt_field(row["name"]).replace(" ", "") == normalized:
            return row["id"]
    return None


@app.route("/api/patients", methods=["GET"])
@login_required
def api_patients_get():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, name, room_number, age, sex, disease, risk_level FROM patients ORDER BY room_number, id"
    )
    patients = cursor.fetchall()
    cursor.close()
    conn.close()
    for p in patients:
        p["name"] = decrypt_field(p["name"])
    return jsonify({"ok": True, "patients": patients})


# 병실+이름이 일치하는 환자가 이미 있으면(보호자 연동으로 먼저 생겨난 행일 수 있음) 나이·성별·병명·위험도만 채워 넣고,
# 없으면 새로 등록한다. 보호자 연동 신청에는 이름·병실만 담기므로 이 화면에서 나머지를 채우는 구조.
@app.route("/api/patients", methods=["POST"])
@login_required
def api_patients_add():
    body = request.get_json(silent=True) or {}
    room = str(body.get("room", "")).strip()
    name = str(body.get("name", "")).strip()
    sex = str(body.get("sex", "")).strip()
    disease = str(body.get("disease", "")).strip()
    risk_level = str(body.get("risk_level", "")).strip()
    try:
        age = int(body.get("age"))
    except (TypeError, ValueError):
        age = None

    if not room or not name:
        return jsonify({"ok": False, "error": "병실과 이름은 필수입니다."}), 400

    conn = get_db()
    try:
        patient_id = find_patient_id_by_name_room(conn, name, room)
        cursor = conn.cursor()
        if patient_id:
            cursor.execute(
                "UPDATE patients SET age = %s, sex = %s, disease = %s, risk_level = %s WHERE id = %s",
                (age, sex, disease, risk_level, patient_id),
            )
        else:
            cursor.execute(
                "INSERT INTO patients (name, room_number, age, sex, disease, risk_level) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (encrypt_field(name), room, age, sex, disease, risk_level),
            )
            patient_id = cursor.lastrowid
        conn.commit()
        cursor.close()
    except mysql.connector.Error:
        conn.rollback()
        return jsonify({"ok": False, "error": "저장 중 오류가 발생했습니다."}), 500
    finally:
        conn.close()

    return jsonify({"ok": True, "id": patient_id})


@app.route("/api/patients/<int:patient_id>/delete", methods=["POST"])
@login_required
def api_patient_delete(patient_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM patients WHERE id = %s", (patient_id,))
    row = cursor.fetchone()
    if row and row[0] is not None:
        cursor.close()
        conn.close()
        return jsonify({"ok": False, "error": "연동된 보호자 계정이 있어 삭제할 수 없습니다."}), 400
    # fall_log.patient_id가 이 환자를 참조하고 있으면 FK 제약 위반이 나므로, 기록은 남기고 연결만 끊는다
    cursor.execute("UPDATE fall_log SET patient_id = NULL WHERE patient_id = %s", (patient_id,))
    cursor.execute("DELETE FROM patients WHERE id = %s", (patient_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"ok": True})


# ===== 낙상 기록: fall_status_callback()이 감지 즉시 자동으로 fall_log에 남긴다 =====
# 관리자가 캡처 이미지를 보고 낙상 환자를 지정(확정)해야 보호자에게 노출된다.

@app.route("/api/fall-log")
@login_required
def api_fall_log_get():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    if session.get("role") == "admin":
        cursor.execute("""
            SELECT f.id, f.room_number, f.detected_at, f.patient_id, f.memo, f.done,
                   p.name AS patient_name
            FROM fall_log f
            LEFT JOIN patients p ON p.id = f.patient_id
            ORDER BY f.detected_at DESC
            LIMIT 100
        """)
        rows = cursor.fetchall()
    else:
        cursor.execute("SELECT id FROM patients WHERE user_id = %s", (session.get("user_id"),))
        prow = cursor.fetchone()
        rows = []
        if prow:
            cursor.execute("""
                SELECT f.id, f.room_number, f.detected_at, f.patient_id, f.memo, f.done,
                       p.name AS patient_name
                FROM fall_log f
                LEFT JOIN patients p ON p.id = f.patient_id
                WHERE f.patient_id = %s AND f.done = TRUE
                ORDER BY f.detected_at DESC
                LIMIT 50
            """, (prow["id"],))
            rows = cursor.fetchall()
    cursor.close()
    conn.close()

    for r in rows:
        if r.get("patient_name"):
            r["patient_name"] = decrypt_field(r["patient_name"])
        if r.get("detected_at"):
            r["detected_at"] = r["detected_at"].strftime("%Y-%m-%d %H:%M:%S")
    return jsonify({"ok": True, "logs": rows})


@app.route("/api/fall-log/<int:log_id>/capture")
@login_required
def api_fall_log_capture(log_id):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT capture_path FROM fall_log WHERE id = %s", (log_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row or not row["capture_path"]:
        return jsonify({"ok": False, "error": "저장된 캡처 이미지가 없습니다."}), 404
    return send_from_directory(CAPTURE_DIR, row["capture_path"])


@app.route("/api/fall-log/<int:log_id>/confirm", methods=["POST"])
@login_required
def api_fall_log_confirm(log_id):
    body = request.get_json(silent=True) or {}
    patient_id = body.get("patient_id")
    memo = str(body.get("memo", "")).strip()
    if not patient_id:
        return jsonify({"ok": False, "error": "낙상 환자를 선택해 주세요."}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE fall_log SET patient_id = %s, confirmed_by = %s, memo = %s, done = TRUE WHERE id = %s",
        (patient_id, session.get("user_id"), memo, log_id),
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"ok": True})


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


# ===== 관리자 통계 차트: fall_log/patrol_log 실데이터 집계 =====

KOREAN_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
STATS_RISK_RANK = {"매우 높음": 4, "높음": 3, "보통": 2, "낮음": 1}
STATS_RISK_CLASS = {"매우 높음": "critical", "높음": "high", "보통": "mid", "낮음": "low"}


@app.route("/api/stats/admin")
@login_required
def api_stats_admin():
    today = datetime.date.today()
    conn = get_db()
    cursor = conn.cursor()

    # 월별 낙상 발생 (최근 6개월)
    months = []
    y, m = today.year, today.month
    for _ in range(6):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()
    cursor.execute(
        "SELECT YEAR(detected_at), MONTH(detected_at), COUNT(*) FROM fall_log "
        "WHERE detected_at >= %s GROUP BY YEAR(detected_at), MONTH(detected_at)",
        (datetime.date(months[0][0], months[0][1], 1),),
    )
    monthly_counts = {(y, m): c for y, m, c in cursor.fetchall()}
    monthly_falls = [[f"{m}월", monthly_counts.get((y, m), 0)] for y, m in months]

    # 병실별 낙상 발생 · 위험도(그 병실에서 가장 높은 위험도로 막대 색을 정한다)
    # 등록된 병실은 낙상이 한 건도 없어도 0건 막대로 항상 표시한다 (그래야 그래프 모양이 유지됨)
    cursor.execute("SELECT room_number, COUNT(*) FROM fall_log GROUP BY room_number")
    fall_count_by_room = dict(cursor.fetchall())
    cursor.execute("SELECT room_number, risk_level FROM patients WHERE room_number IS NOT NULL")
    room_risk = {}
    all_rooms = set(fall_count_by_room.keys())   # 낙상이 실제 기록된 병실은 환자 등록 여부와 무관하게 포함
    for room, risk in cursor.fetchall():
        all_rooms.add(room)
        if not risk:
            continue
        best = room_risk.get(room)
        if best is None or STATS_RISK_RANK.get(risk, 0) > STATS_RISK_RANK.get(best, 0):
            room_risk[room] = risk
    room_falls = [
        [f"{room}호", fall_count_by_room.get(room, 0), STATS_RISK_CLASS.get(room_risk.get(room), "low")]
        for room in sorted(all_rooms)
    ]

    # 시간대별 낙상
    cursor.execute("SELECT HOUR(detected_at), COUNT(*) FROM fall_log GROUP BY HOUR(detected_at)")
    hour_counts = dict(cursor.fetchall())
    hour_buckets = [("새벽", range(0, 6)), ("오전", range(6, 12)), ("오후", range(12, 18)), ("야간", range(18, 24))]
    hourly_falls = [[label, sum(hour_counts.get(h, 0) for h in hours)] for label, hours in hour_buckets]

    # 일별 순찰 횟수 (최근 7일)
    start = today - datetime.timedelta(days=6)
    cursor.execute(
        "SELECT DATE(patrolled_at), COUNT(*) FROM patrol_log WHERE patrolled_at >= %s GROUP BY DATE(patrolled_at)",
        (start,),
    )
    patrol_counts = {d.isoformat(): c for d, c in cursor.fetchall()}
    daily_patrols = []
    for i in range(7):
        d = start + datetime.timedelta(days=i)
        daily_patrols.append([KOREAN_WEEKDAYS[d.weekday()], patrol_counts.get(d.isoformat(), 0)])

    # Home 탭 요약 카드: 전체 병실 · 위험 병실 · 오늘 낙상 · 이번 달 낙상
    cursor.execute("SELECT COUNT(DISTINCT room_number) FROM patients WHERE room_number IS NOT NULL")
    total_rooms = cursor.fetchone()[0]
    risk_rooms = sum(1 for r in room_risk.values() if r in ("높음", "매우 높음"))
    cursor.execute("SELECT COUNT(*) FROM fall_log WHERE DATE(detected_at) = CURDATE()")
    today_falls = cursor.fetchone()[0]
    month_falls = monthly_falls[-1][1]   # months 마지막 = 이번 달

    cursor.close()
    conn.close()
    return jsonify({
        "ok": True,
        "monthly_falls": monthly_falls,
        "room_falls": room_falls,
        "hourly_falls": hourly_falls,
        "daily_patrols": daily_patrols,
        "total_rooms": total_rooms,
        "risk_rooms": risk_rooms,
        "today_falls": today_falls,
        "month_falls": month_falls,
    })


@app.route("/api/stats/guardian")
@login_required
def api_stats_guardian():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, room_number FROM patients WHERE user_id = %s", (session.get("user_id"),))
    prow = cursor.fetchone()
    if not prow:
        cursor.close()
        conn.close()
        return jsonify({"ok": True, "monthly_falls": [], "hourly_patrols": [], "daily_patrols": []})
    patient_id, room_number = prow
    today = datetime.date.today()

    # 월별 낙상 발생 이력 (최근 6개월, 확정된 것만)
    months = []
    y, m = today.year, today.month
    for _ in range(6):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()
    cursor.execute(
        "SELECT YEAR(detected_at), MONTH(detected_at), COUNT(*) FROM fall_log "
        "WHERE patient_id = %s AND done = TRUE AND detected_at >= %s "
        "GROUP BY YEAR(detected_at), MONTH(detected_at)",
        (patient_id, datetime.date(months[0][0], months[0][1], 1)),
    )
    monthly_counts = {(y, m): c for y, m, c in cursor.fetchall()}
    monthly_falls = [[f"{m}월", monthly_counts.get((y, m), 0)] for y, m in months]

    # 시간대별 안심 순찰 완료 횟수 (내 병실, 전체 기간)
    cursor.execute(
        "SELECT HOUR(patrolled_at), COUNT(*) FROM patrol_log WHERE room_number = %s GROUP BY HOUR(patrolled_at)",
        (room_number,),
    )
    hour_counts = dict(cursor.fetchall())
    hour_buckets = [("새벽", range(0, 6)), ("오전", range(6, 12)), ("오후", range(12, 18)), ("야간", range(18, 24))]
    hourly_patrols = [[label, sum(hour_counts.get(h, 0) for h in hours)] for label, hours in hour_buckets]

    # 최근 7일 순찰 완료 횟수 (내 병실)
    start = today - datetime.timedelta(days=6)
    cursor.execute(
        "SELECT DATE(patrolled_at), COUNT(*) FROM patrol_log WHERE room_number = %s AND patrolled_at >= %s "
        "GROUP BY DATE(patrolled_at)",
        (room_number, start),
    )
    patrol_counts = {d.isoformat(): c for d, c in cursor.fetchall()}
    daily_patrols = []
    for i in range(7):
        d = start + datetime.timedelta(days=i)
        daily_patrols.append([KOREAN_WEEKDAYS[d.weekday()], patrol_counts.get(d.isoformat(), 0)])
    patrol_today = daily_patrols[-1][1]   # start+6일 = 오늘

    # 최근 30일 순찰 완료 횟수 (내 병실)
    start_30 = today - datetime.timedelta(days=29)
    cursor.execute(
        "SELECT COUNT(*) FROM patrol_log WHERE room_number = %s AND patrolled_at >= %s",
        (room_number, start_30),
    )
    patrol_30d = cursor.fetchone()[0]

    cursor.close()
    conn.close()
    return jsonify({
        "ok": True,
        "monthly_falls": monthly_falls,
        "hourly_patrols": hourly_patrols,
        "daily_patrols": daily_patrols,
        "patrol_today": patrol_today,
        "patrol_30d": patrol_30d,
    })


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
