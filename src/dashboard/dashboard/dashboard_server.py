import json
import os
import threading
import time
from functools import wraps

import mysql.connector
from flask import Flask, Response, jsonify, render_template, request, session
from werkzeug.security import check_password_hash

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
    session["role"] = user["role"]
    return jsonify({"ok": True, "username": user["username"], "role": user["role"]})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


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


# ===== 캘린더 일정: 서버 JSON 파일에 저장 (여러 브라우저가 같은 일정을 공유) =====
EVENTS_FILE = os.path.expanduser("~/dashboard_events.json")
events_lock = threading.Lock()


def load_events():
    # 반환 형식: { "YYYY-MM-DD": [ {"id": <int>, "text": <str>}, ... ], ... }
    if not os.path.exists(EVENTS_FILE):
        return {}
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_events(events):
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)


@app.route("/api/events", methods=["GET"])
@login_required
def api_events_get():
    with events_lock:
        return jsonify(load_events())


@app.route("/api/events", methods=["POST"])
@login_required
def api_events_add():
    body = request.get_json(silent=True) or {}
    date = str(body.get("date", "")).strip()
    text = str(body.get("text", "")).strip()
    if not date or not text:
        return jsonify({"ok": False, "error": "date와 text가 필요합니다"}), 400

    with events_lock:
        events = load_events()
        event = {"id": int(time.time() * 1000), "text": text}
        events.setdefault(date, []).append(event)
        save_events(events)
    return jsonify({"ok": True, "date": date, "event": event})


@app.route("/api/events/delete", methods=["POST"])
@login_required
def api_events_delete():
    body = request.get_json(silent=True) or {}
    date = str(body.get("date", "")).strip()
    event_id = body.get("id")

    with events_lock:
        events = load_events()
        if date in events:
            events[date] = [e for e in events[date] if e.get("id") != event_id]
            if not events[date]:
                del events[date]
            save_events(events)
    return jsonify({"ok": True})


# ===== 체크리스트 / 메모: 서버 JSON 파일에 저장 (여러 브라우저 공유) =====
NOTES_FILE = os.path.expanduser("~/dashboard_notes.json")
notes_lock = threading.Lock()


def load_notes():
    # 형식: { "checklist": [ {"id": <int>, "text": <str>, "done": <bool>} ], "memo": <str> }
    default = {"checklist": [], "memo": ""}
    if not os.path.exists(NOTES_FILE):
        return default
    try:
        with open(NOTES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default
        data.setdefault("checklist", [])
        data.setdefault("memo", "")
        return data
    except Exception:
        return default


def save_notes(notes):
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)


@app.route("/api/notes", methods=["GET"])
@login_required
def api_notes_get():
    with notes_lock:
        return jsonify(load_notes())


@app.route("/api/checklist/add", methods=["POST"])
@login_required
def api_checklist_add():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    if not text:
        return jsonify({"ok": False, "error": "text가 필요합니다"}), 400
    with notes_lock:
        notes = load_notes()
        item = {"id": int(time.time() * 1000), "text": text, "done": False}
        notes["checklist"].append(item)
        save_notes(notes)
    return jsonify({"ok": True, "item": item})


@app.route("/api/checklist/toggle", methods=["POST"])
@login_required
def api_checklist_toggle():
    body = request.get_json(silent=True) or {}
    item_id = body.get("id")
    with notes_lock:
        notes = load_notes()
        for it in notes["checklist"]:
            if it.get("id") == item_id:
                it["done"] = not it.get("done", False)
                break
        save_notes(notes)
    return jsonify({"ok": True})


@app.route("/api/checklist/delete", methods=["POST"])
@login_required
def api_checklist_delete():
    body = request.get_json(silent=True) or {}
    item_id = body.get("id")
    with notes_lock:
        notes = load_notes()
        notes["checklist"] = [it for it in notes["checklist"] if it.get("id") != item_id]
        save_notes(notes)
    return jsonify({"ok": True})


@app.route("/api/memo", methods=["POST"])
@login_required
def api_memo_save():
    body = request.get_json(silent=True) or {}
    memo = str(body.get("memo", ""))
    with notes_lock:
        notes = load_notes()
        notes["memo"] = memo
        save_notes(notes)
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
