#!/usr/bin/env python3
"""병실 순찰 노드.

실행
    ros2 run my_patrol patrol

필요한 환경변수
    DB_PASSWORD          환자 정보를 읽을 MySQL 비밀번호 / 1234(각자 비밀번호) 
    FIELD_ENCRYPT_KEY    환자 이름 복호화용(없어도 순찰은 정상 동작, 로그에만 "환자")

--------------------------------------------------------------------------
상태 기계
--------------------------------------------------------------------------
    mode : IDLE | RUNNING | PAUSED
    task : PATROL | MANUAL          (RUNNING/PAUSED 일 때 무엇을 하던 중인가)

              ┌─ 순찰 시작 ─→ RUNNING(PATROL) ─ 일시정지 ─→ PAUSED(PATROL)
        IDLE ─┤                                                  │
              └─ 맵 클릭  ─→ RUNNING(MANUAL) ─ 일시정지 ─→ PAUSED(MANUAL)
                                   │                             │
                              도착·행동 완료              순찰시작 / 맵클릭
                                   ↓
                                 IDLE

    주행 중(RUNNING)에는 순찰 시작·맵 클릭을 받지 않는다. 먼저 일시정지해야 한다.
    거부할 때 그냥 무시하지 않고 Trigger 응답에 이유를 담아 돌려준다.

--------------------------------------------------------------------------
도착 후 행동은 '가는 이유'와 분리했다
--------------------------------------------------------------------------
    순찰로 갔든 맵을 찍어 갔든 도착하면 같은 처리를 한다.
        dock    -> 360도 회전(도킹 정렬) // 욜로 코드 추가시 변경
        room*   -> 마커 정렬 + 낙상 스캔 + /patrol_complete 발행
        standby -> 정지만
    끝난 뒤 PATROL 이면 다음 병실로 이어지고, MANUAL 이면 IDLE 로 돌아간다.
"""

import math
import os
import random
import time

import mysql.connector
import rclpy
import yaml
from ament_index_python.packages import get_package_share_directory
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool, Float32, Int32MultiArray, String
from std_srvs.srv import SetBool, Trigger
from tf2_ros import Buffer, TransformListener

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:            # 이름 복호화는 로그 표시용이라 없어도 순찰은 돈다
    Fernet = None
    InvalidToken = Exception


# =========================================================
# 환자 정보 (MySQL)
# =========================================================
# 환자 원본은 대시보드와 같은 DB 하나다. 예전에는 patients.yaml 을 따로 읽었는데,
# 웹에서 환자를 등록·수정해도 로봇이 모르는 문제가 있어서 DB로 합쳤다.
#
# 순찰 우선순위 점수(score)는 여기서 계산하지 않는다. 대시보드 서버가 환자를
# 등록·수정할 때 계산해 patients.score 에 저장하므로, 이 노드는 읽기만 한다.
# 계산식이 두 곳에 있으면 한쪽만 고쳤을 때 화면 순위와 실제 순찰 순서가 어긋난다.

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", "patrol_dashboard"),
}

# DB 병실 번호 <-> rooms.yaml 병실 이름
ROOM_KEY_MAP = {"101": "room1", "102": "room2", "103": "room3", "104": "room4"}

_FERNET = None
_ENCRYPT_KEY = os.environ.get("FIELD_ENCRYPT_KEY")
if Fernet is not None and _ENCRYPT_KEY:
    try:
        _FERNET = Fernet(_ENCRYPT_KEY.encode())
    except Exception:
        _FERNET = None


def decrypt_name(value):
    """환자 이름 복호화. 순찰 판단에는 안 쓰고 로그 표시용이다."""
    if value is None:
        return "-"

    if _FERNET is not None:
        try:
            return _FERNET.decrypt(value.encode()).decode()
        except (InvalidToken, ValueError, AttributeError):
            pass               # 키가 다르거나 평문으로 들어간 값

    # 키가 없거나 복호화에 실패한 경우. Fernet 토큰은 항상 "gAAAAA"로 시작하므로,
    # 암호문이면 로그에 그대로 찍지 않고 자리표시자로 바꾼다.
    text = str(value)
    return "환자" if text.startswith("gAAAAA") else text


# 마지막 DB 오류. 순찰할 병실이 없을 때 "환자가 없다"인지 "DB를 못 읽었다"인지
# 구분해서 안내하려고 남긴다 — 둘은 사용자가 해야 할 일이 완전히 다르다.
last_db_error = None


def load_patients_from_db():
    """재원 환자의 병실·점수·이름을 읽어온다. 실패하면 빈 목록."""
    global last_db_error

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as e:
        last_db_error = str(e)
        return []

    try:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT name, room_number, score FROM patients "
                "WHERE room_number IS NOT NULL"
            )
            rows = cursor.fetchall()
            last_db_error = None
            return rows
        finally:
            cursor.close()
    except mysql.connector.Error as e:
        last_db_error = str(e)
        return []
    finally:
        conn.close()


def calculate_room_priority():
    """병실별 우선순위 목록을 점수 내림차순으로 돌려준다.

    병실 점수 = 그 병실 환자 점수 중 '최댓값'.
    합계가 아닌 이유: 순찰 우선순위는 가장 위험한 환자 한 명이 정한다.
    저위험 환자가 여럿이라고 우선순위가 올라가면 안 된다.
    """
    rooms = {}

    for row in load_patients_from_db():
        room = str(row["room_number"])
        rooms.setdefault(room, []).append({
            "name": decrypt_name(row["name"]),
            "score": row["score"] or 0,
        })

    room_priority = []
    for room, patient_list in rooms.items():
        top = max(patient_list, key=lambda p: p["score"])
        room_priority.append({
            "room": room,
            "score": top["score"],
            "patient": top["name"],
        })

    room_priority.sort(key=lambda x: x["score"], reverse=True)
    return room_priority


class PatrolNode(Node):

    # ── 이동 ──
    PLACE_MATCH_DIST = 1.0    # 이 거리(m) 안에 등록 지점이 없으면 '위치 모름'
    ANGULAR_SPEED = 0.3       # 도킹 360도 회전 속도(rad/s)

    # ── 순찰 선택 ──
    RANK_WEIGHTS = (40, 30, 20, 10)   # 1~4순위 가중치. 그 뒤는 전부 10
    RETRY_SEC = 10.0                  # 순찰할 병실이 없을 때 재확인 주기

    # ── 마커 정렬 ──
    SEARCH_SPEED = 0.3        # 탐색 회전속도(rad/s)
    ALIGN_K = 0.6             # 정렬 비례계수
    MAX_TURN = 0.4            # 최대 회전(rad/s)
    MIN_TURN = 0.10           # 최소 회전(rad/s) — 모터 데드밴드 회피
    CENTER_TOL = 0.10         # 이 안에 들면 '중앙 정렬됨'(화면폭의 10%)
    SEEN_TIMEOUT = 0.4        # 이 시간(s) 안에 offset 오면 '마커 보임'
    ALIGN_TIMEOUT = 40.0      # 정렬 전체 제한시간(s)

    # ── 낙상 스캔 ──
    SCAN_HALF = 50.0          # 중앙 기준 좌/우 각도(deg)
    SCAN_SPEED = 0.2          # 스캔 회전속도(rad/s) — 느리게(YOLO 감지 시간 확보)
    HOLD_CLEAR_SEC = 10.0     # 낙상이 이만큼 연속으로 안 보여야 '사라짐'

    def __init__(self):
        super().__init__('patrol_node')

        # 서비스·구독이 블로킹 작업(마커 정렬·낙상 스캔) 중에도 처리되도록
        # 별도 콜백 그룹에 둔다. main()에서 MultiThreadedExecutor로 돌린다.
        self.cb_group = ReentrantCallbackGroup()

        # ── Nav2 (이 노드가 유일한 소유자) ──
        self.nav_client = ActionClient(
            self, NavigateToPose, '/navigate_to_pose',
            callback_group=self.cb_group)

        self.cmd_vel_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self.complete_pub = self.create_publisher(String, '/patrol_complete', 10)

        # 현재 위치를 모를 때만 참고할 TF (map -> base_footprint)
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        # ── waypoint ──
        self.config_path = os.path.join(
            get_package_share_directory('my_patrol'), 'config', 'rooms.yaml')
        self.waypoints = {}
        self.load_waypoints()

        # ── 마커 · 낙상 (aruco_id / fall_detection 노드와 통신) ──
        self.latest_ids = []
        self.latest_offset = 0.0
        self.offset_time = None
        self.yaw = None            # odom 기반 현재 yaw(rad) — 회전각 측정용
        self.fall = False          # /fall_detected 최신값
        self.fall_status = 'NO_PERSON'

        for msg_type, topic, cb in (
            (Int32MultiArray, '/room_marker', self._id_cb),
            (Float32, '/marker_offset', self._offset_cb),
            (Odometry, '/odom', self._odom_cb),
            (Bool, '/fall_detected', self._fall_cb),
            (String, '/fall_status', self._status_cb),
        ):
            self.create_subscription(
                msg_type, topic, cb, 10, callback_group=self.cb_group)

        self.aruco_cli = self.create_client(
            SetBool, 'aruco_enable', callback_group=self.cb_group)
        self.fall_cli = self.create_client(
            SetBool, 'fall_enable', callback_group=self.cb_group)

        # ── 서비스 ──
        # /resume_navigation 은 두지 않는다. 대시보드 버튼이 순찰 시작 / 일시정지
        # 둘뿐이라 부를 데가 없고, 멈춘 뒤에는 맵을 다시 찍는 편이 경로도 더 정확하다.
        services = [
            ('/start_patrol', self.start_patrol_callback),
            ('/pause_navigation', self.pause_navigation_callback),
            ('/go_to_dock', self.go_to_dock_callback),
            ('/go_to_standby', self.go_to_standby_callback),
            ('/go_to_room1', lambda rq, rs: self.go_to_room('room1', rs)),
            ('/go_to_room2', lambda rq, rs: self.go_to_room('room2', rs)),
            ('/go_to_room3', lambda rq, rs: self.go_to_room('room3', rs)),
            ('/go_to_room4', lambda rq, rs: self.go_to_room('room4', rs)),
        ]
        for name, cb in services:
            self.create_service(Trigger, name, cb, callback_group=self.cb_group)

        # ── 상태 ──
        self.mode = 'IDLE'          # IDLE | RUNNING | PAUSED
        self.task = None            # PATROL | MANUAL
        self.abort = False          # 블로킹 루프(정렬·스캔)를 빠져나오라는 신호

        self.pending_waypoints = []
        self.active_waypoint = None
        self.goal_handle = None
        self.current_mode = None    # 지금 향하는 목적지 키 (dock/standby/room1~4)
        self.current_place = None   # 마지막으로 도착한 목적지 키
        self.last_room = None       # 직전에 순찰을 마친 병실 번호 (연속 중복 방지)

        self.rotation_time = 0.0
        self.rotation_timer = None
        self.retry_timer = None
        self._pending_enables = []   # 완료 전 GC 되지 않게 붙잡아 두는 서비스 future

        # 두 감지 노드는 단독 테스트용으로 기본이 ON 이다. 켜둔 채 주행하면
        # YOLO가 쉬지 않고 돌아 CPU를 잡아먹어 주행이 끊긴다.
        # 병실에 도착해 관찰할 때만 켜고, 그 외에는 꺼둔다.
        self.set_enable(False)
        self.set_fall_enable(False)

        # 환경변수를 빠뜨리고 실행하는 일이 잦아서, 켜자마자 한 번 확인해 알려준다
        rooms = calculate_room_priority()
        if rooms:
            self.get_logger().info(
                f'DB 연결 OK — 순찰 대상 병실 {len(rooms)}개')
        elif last_db_error:
            self.get_logger().error(
                f'DB에 연결하지 못했습니다: {last_db_error}')
            self.get_logger().error(
                '  DB_PASSWORD 환경변수를 확인하세요. 예: export DB_PASSWORD=1234')
        else:
            self.get_logger().warn(
                'DB는 연결됐지만 재원 환자가 없습니다. 대시보드에서 환자를 등록해 주세요.')

        self.get_logger().info('Patrol node started (mode=IDLE)')

    # =====================================================
    # 마커 · 낙상 구독 콜백
    # =====================================================

    def _id_cb(self, msg):
        self.latest_ids = list(msg.data)

    def _offset_cb(self, msg):
        self.latest_offset = msg.data
        self.offset_time = time.time()

    def _odom_cb(self, msg):
        q = msg.pose.pose.orientation
        # 쿼터니언 -> yaw (z축 회전각)
        siny = 2.0 * (q.w * q.z + q.x * q.y)
        cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.yaw = math.atan2(siny, cosy)

    def _fall_cb(self, msg):
        self.fall = msg.data

    def _status_cb(self, msg):
        self.fall_status = msg.data

    # =====================================================
    # waypoint
    # =====================================================

    def load_waypoints(self):
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.waypoints = yaml.safe_load(f) or {}
        except OSError as e:
            self.get_logger().error(f'Waypoint file unreadable: {e}')
            self.waypoints = {}

    def locate_current_place(self):
        """지금 어느 등록 지점에 있는지 TF로 확인한다. 모르면 None.

        amcl_pose 가 아니라 TF를 읽는 이유: amcl_pose 는 로봇이 움직여야 발행돼서
        켜자마자 첫 명령을 주면 값이 아예 없다. TF는 정지 상태에서도 계속 나온다.

        가까운 지점이 하나도 없으면 억지로 고르지 않고 None을 돌려준다 —
        잘못된 병실 입구를 들르는 것보다 안 들르는 편이 낫다.
        """
        try:
            tf = self.tf_buffer.lookup_transform(
                'map', 'base_footprint', rclpy.time.Time())
        except Exception as e:  # noqa: BLE001
            self.get_logger().warn(f'TF lookup failed: {e}')
            return None

        robot_x = tf.transform.translation.x
        robot_y = tf.transform.translation.y

        nearest_room = None
        nearest_dist = None

        def check(point, room_name):
            nonlocal nearest_room, nearest_dist
            if not isinstance(point, dict) or 'x' not in point:
                return
            dist = math.hypot(
                float(point['x']) - robot_x, float(point['y']) - robot_y)
            if nearest_dist is None or dist < nearest_dist:
                nearest_dist = dist
                nearest_room = room_name

        rooms = self.waypoints.get('rooms') or {}
        for room_name, room in rooms.items():
            if not isinstance(room, dict):
                continue
            # hall(복도)은 병실 '안'이 아니므로 이름을 달지 않는다
            check(room.get('hall'), None)
            check(room.get('inside', room), room_name)

        check(self.waypoints.get('dock'), None)
        check(self.waypoints.get('standby'), None)

        if nearest_dist is None or nearest_dist > self.PLACE_MATCH_DIST:
            self.get_logger().warn(
                'Current place unknown (no registered waypoint nearby).')
            return None

        self.get_logger().info(
            f'Located by TF: {nearest_room or "hallway"} ({nearest_dist:.2f} m)')
        return nearest_room

    def exit_waypoints(self, target_name):
        """출발 병실 탈출 경유점.

        병실 안에서 목적지를 바로 찍으면 Nav2가 문을 비스듬히 빠져나가는 경로를
        짜서 ㄱ자로 꺾인다. 그래서 지금 있는 병실의 문 앞(hall)을 맨 앞에 붙인다.
        병실이 아닌 곳(dock/standby)에서 출발하거나 목적지가 지금 병실 자신이면
        나올 필요가 없다.
        """
        here = self.current_place
        if here is None:
            here = self.locate_current_place()
            self.current_place = here

        if here is None or here == target_name:
            return []

        room = (self.waypoints.get('rooms') or {}).get(here)
        if not isinstance(room, dict):
            return []

        hall = room.get('hall')
        if not hall:
            return []

        self.get_logger().info(f'Leaving {here} through its hall first.')
        return [hall]

    # =====================================================
    # 서비스 — 게이트
    # =====================================================

    def _reject_if_running(self, response):
        """주행 중에는 명령을 받지 않는다. 받을 수 있으면 None."""
        if self.mode == 'RUNNING':
            response.success = False
            response.message = '주행 중입니다. 먼저 일시정지해 주세요.'
            return response
        return None

    def start_patrol_callback(self, request, response):
        """순찰 시작 / 재개.

        로봇이 병실 안에 있으면 그 병실 관찰부터 다시 한다 (이미 들어와 있는데
        확인 없이 나가면 낙상 점검을 건너뛰게 된다). 그 외에는 새로 뽑는다 —
        병실 선택이 가중 랜덤이라 '가던 병실'을 기억할 이유가 없다.
        """
        rejected = self._reject_if_running(response)
        if rejected is not None:
            return rejected

        self._cancel_retry_timer()
        self.abort = False
        self.mode = 'RUNNING'
        self.task = 'PATROL'

        here = self.current_place or self.locate_current_place()
        if here and here in (self.waypoints.get('rooms') or {}):
            self.current_place = here
            self.current_mode = here
            self.get_logger().info(f'Resuming inside {here} — observing again.')
            response.success = True
            response.message = f'{here} 관찰을 다시 시작합니다.'
            self.on_arrived(here)
            return response

        if not self.start_next_patrol():
            self.mode = 'IDLE'
            self.task = None
            response.success = False
            response.message = self._no_room_reason()
            return response

        response.success = True
        response.message = '순찰을 시작합니다.'
        return response

    def pause_navigation_callback(self, request, response):
        if self.mode != 'RUNNING':
            response.success = False
            response.message = '이동 중이 아닙니다.'
            return response

        self.mode = 'PAUSED'
        self.abort = True          # 정렬·스캔 루프가 이걸 보고 빠져나온다

        if self.rotation_timer is not None:
            self.stop_rotation()

        if self.goal_handle is not None:
            self.goal_handle.cancel_goal_async()

        self.stop_robot()
        self.set_enable(False)
        self.set_fall_enable(False)

        # 경유점 사이 어딘가에서 멈췄으므로 현재 위치를 알 수 없다.
        # 다음 이동 때 TF로 다시 확인하게 둔다.
        self.pending_waypoints = []
        self.active_waypoint = None
        self.current_place = None

        self.get_logger().info('Paused.')
        response.success = True
        response.message = '이동을 일시정지했습니다.'
        return response

    # =====================================================
    # 서비스 — 수동 이동
    # =====================================================

    def go_to_dock_callback(self, request, response):
        rejected = self._reject_if_running(response)
        if rejected is not None:
            return rejected

        self.load_waypoints()           # 서비스 호출 시 YAML 다시 읽기
        dock = self.waypoints.get('dock')
        points = self.exit_waypoints('dock')
        if dock:
            points.append(dock)
        return self.start_navigation('dock', points, response, task='MANUAL')

    def go_to_standby_callback(self, request, response):
        rejected = self._reject_if_running(response)
        if rejected is not None:
            return rejected

        self.load_waypoints()
        standby = self.waypoints.get('standby')
        points = self.exit_waypoints('standby')
        if standby:
            points.append(standby)
        return self.start_navigation('standby', points, response, task='MANUAL')

    def go_to_room(self, room_name, response, task='MANUAL'):
        """병실 공통 처리: (출발 병실 hall) -> 목표 hall -> inside"""
        if task == 'MANUAL':
            rejected = self._reject_if_running(response)
            if rejected is not None:
                return rejected

        self.load_waypoints()
        room = (self.waypoints.get('rooms') or {}).get(room_name)
        if room is None:
            response.success = False
            response.message = f'{room_name} 좌표가 등록되어 있지 않습니다.'
            return response

        # hall(문 앞)은 waypoint_saver에서 건너뛸 수 있어 없을 수도 있다.
        # 있으면 hall -> inside 순으로 가고(복도끼리 이동이라 벽을 안 건넌다),
        # 없으면 inside로 바로 간다.
        hall = room.get('hall')
        inside = room.get('inside', room)

        points = self.exit_waypoints(room_name)
        points += [p for p in (hall, inside) if p]

        return self.start_navigation(room_name, points, response, task=task)

    # =====================================================
    # 순찰 — 병실 선택
    # =====================================================

    def print_priority(self, priority_rooms):
        self.get_logger().info('--------------------------------')
        self.get_logger().info('현재 병실 우선순위')
        self.get_logger().info('--------------------------------')
        for rank, info in enumerate(priority_rooms, start=1):
            weight = (self.RANK_WEIGHTS[rank - 1]
                      if rank <= len(self.RANK_WEIGHTS) else 10)
            self.get_logger().info(
                f'{rank}순위 | {info["room"]}호 | {info["score"]}점 | '
                f'{info["patient"]} | 가중치={weight}')

    def select_next_room(self):
        """가중 랜덤으로 다음 병실을 고른다. 고를 게 없으면 None.

        매 선택 시 DB를 다시 읽는다. 관리자가 웹에서 환자를 등록·수정하면
        다음 순찰부터 자동 반영된다.
        """
        priority_rooms = calculate_room_priority()

        # 재원 환자가 없거나 DB를 못 읽으면 고를 병실이 없다.
        # 여기서 멈추지 않으면 아래 random.choices 가 빈 목록으로 터진다.
        if not priority_rooms:
            return None

        self.print_priority(priority_rooms)

        candidates, weights = [], []
        for index, room_info in enumerate(priority_rooms):
            # 바로 전에 순찰한 병실은 제외 (같은 방을 연속으로 왕복하지 않게)
            if self.last_room is not None and str(room_info['room']) == self.last_room:
                continue
            candidates.append(room_info)
            weights.append(self.RANK_WEIGHTS[index]
                           if index < len(self.RANK_WEIGHTS) else 10)

        # 병실이 하나뿐이면 위에서 전부 걸러진다 — 그때는 제외 규칙을 포기한다
        if not candidates:
            candidates = priority_rooms
            weights = [self.RANK_WEIGHTS[i] if i < len(self.RANK_WEIGHTS) else 10
                       for i in range(len(candidates))]

        return random.choices(candidates, weights=weights, k=1)[0]

    def start_next_patrol(self):
        """다음 병실을 뽑아 이동을 시작한다. 시작했으면 True."""
        room_info = self.select_next_room()

        # 환자가 아직 등록되지 않았거나 DB 연결이 끊긴 상태.
        # 순찰을 멈추는 대신 잠시 뒤 다시 시도한다.
        if room_info is None:
            self.get_logger().warn(
                f'{self._no_room_reason()} {int(self.RETRY_SEC)}초 뒤 다시 확인합니다')
            self._start_retry_timer()
            return False

        room_number = str(room_info['room'])
        room_key = ROOM_KEY_MAP.get(room_number)
        if room_key is None:
            self.get_logger().error(f'{room_number}호 ROOM_KEY_MAP 없음')
            self._start_retry_timer()
            return False

        self.get_logger().info('================================')
        self.get_logger().info(f'다음 순찰 병실 : {room_number}호')
        self.get_logger().info(f'병실 위험도 : {room_info["score"]}점')
        self.get_logger().info(f'최고 위험 환자 : {room_info["patient"]}')
        self.get_logger().info('================================')

        class _Resp:                 # start_navigation 이 응답 객체를 요구한다
            success = False
            message = ''

        resp = self.go_to_room(room_key, _Resp(), task='PATROL')
        return bool(resp.success)

    def _no_room_reason(self):
        """순찰할 병실이 없는 이유를 사람이 읽을 문장으로."""
        if last_db_error:
            return f'DB에 연결하지 못했습니다 (DB_PASSWORD 확인). {last_db_error}'
        return '재원 환자가 없습니다. 대시보드에서 환자를 등록해 주세요.'

    def _start_retry_timer(self):
        self._cancel_retry_timer()
        self.retry_timer = self.create_timer(
            self.RETRY_SEC, self._retry_patrol, callback_group=self.cb_group)

    def _cancel_retry_timer(self):
        if self.retry_timer is not None:
            self.retry_timer.cancel()
            self.destroy_timer(self.retry_timer)
            self.retry_timer = None

    def _retry_patrol(self):
        """환자가 없어서 멈췄을 때 재시도. 타이머는 한 번만 쓰고 없앤다."""
        self._cancel_retry_timer()
        if self.mode == 'RUNNING' and self.task == 'PATROL':
            self.start_next_patrol()

    # =====================================================
    # 이동 실행
    # =====================================================

    def start_navigation(self, mode, points, response, task='MANUAL'):
        # 새 목적지를 받으면 남은 경로는 버리고 새로 시작한다
        self.pending_waypoints = []
        self.active_waypoint = None

        if not points:
            response.success = False
            response.message = '등록된 좌표가 없습니다.'
            return response

        route = ' -> '.join(f"({p['x']}, {p['y']})" for p in points)
        self.get_logger().info(f'{mode}: {len(points)} waypoint(s) — {route}')

        self.mode = 'RUNNING'
        self.task = task
        self.abort = False
        self.current_mode = mode
        self.pending_waypoints = list(points)

        # 첫 경유점 전송 실패(Nav2 미실행)면 이동을 시작하지 못한 것이므로
        # 성공으로 응답하지 않는다
        if not self.send_next_waypoint():
            self.navigation_failed()
            response.success = False
            response.message = 'Nav2에 연결할 수 없습니다.'
            return response

        response.success = True
        response.message = f'{mode} 이동을 시작합니다.'
        return response

    def send_next_waypoint(self):
        """경유점마다 도착을 확인하고 다음 goal을 보낸다."""
        if not self.pending_waypoints:
            self.navigation_completed()
            return True
        return self.send_goal(self.pending_waypoints.pop(0))

    def send_goal(self, waypoint):
        # 대시보드는 서비스 응답을 3초까지 기다리므로 그보다 짧게 잡는다
        if not self.nav_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error('NavigateToPose Action Server not available.')
            return False

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = self.make_pose(waypoint)
        self.active_waypoint = waypoint

        self.get_logger().info(f"Goal: x={waypoint['x']}, y={waypoint['y']}")

        future = self.nav_client.send_goal_async(
            goal_msg, feedback_callback=self.feedback_callback)
        future.add_done_callback(self.goal_response_callback)
        return True

    def make_pose(self, waypoint):
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = float(waypoint['x'])
        pose.pose.position.y = float(waypoint['y'])
        pose.pose.position.z = 0.0
        pose.pose.orientation.x = 0.0
        pose.pose.orientation.y = 0.0
        pose.pose.orientation.z = float(waypoint['z'])
        pose.pose.orientation.w = float(waypoint['w'])
        return pose

    def goal_response_callback(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().error('Navigation goal rejected.')
            self.navigation_failed()
            return

        self.goal_handle = goal_handle      # 일시정지가 취소할 대상
        goal_handle.get_result_async().add_done_callback(self.result_callback)

    def feedback_callback(self, feedback_msg):
        distance = feedback_msg.feedback.distance_remaining
        self.get_logger().info(f'Distance remaining: {distance:.2f} m')

    def result_callback(self, future):
        # 일시정지로 취소한 결과는 실패가 아니다
        if self.mode == 'PAUSED':
            self.get_logger().info('Navigation paused (goal cancelled).')
            return

        result = future.result()
        if result.status == GoalStatus.STATUS_SUCCEEDED:
            self.get_logger().info('Waypoint reached.')
            if not self.send_next_waypoint():
                self.navigation_failed()
        else:
            self.get_logger().error(f'Navigation failed. status={result.status}')
            self.navigation_failed()

    def navigation_failed(self):
        """이동 실패·중단.

        어디서 멈췄는지 알 수 없으므로 현재 위치 기억을 지운다.
        (모르는 채로 두면 다음 이동에서 엉뚱한 병실 입구를 들른다)
        """
        self.mode = 'IDLE'
        self.task = None
        self.current_mode = None
        self.current_place = None
        self.pending_waypoints = []
        self.active_waypoint = None
        self.goal_handle = None

    # =====================================================
    # 도착 후 행동 (순찰·수동 공통)
    # =====================================================

    def navigation_completed(self):
        # 다음 이동에서 "출발 병실 입구"를 계산하는 기준이 된다
        place = self.current_mode
        self.current_place = place
        self.active_waypoint = None
        self.goal_handle = None

        self.get_logger().info(f'Navigation completed: {place}')
        self.on_arrived(place)

    def on_arrived(self, place):
        """도착 지점별 행동. 순찰로 왔든 맵을 찍어 왔든 같은 처리를 한다."""
        if place == 'dock':
            self.get_logger().info('Charging station reached. Docking rotation.')
            self.start_rotation()        # 회전이 끝나면 _finish_task 를 부른다
            return

        if place in (self.waypoints.get('rooms') or {}):
            self.run_room_routine(place)
            return

        # standby 등 — 정지만
        self._finish_task()

    def run_room_routine(self, room_key):
        """병실 안에서: 마커 정렬 -> 낙상 스캔 -> /patrol_complete 발행.

        블로킹이라 오래 걸린다. 서비스는 다른 콜백 그룹에서 처리되므로
        이 동안에도 일시정지를 받을 수 있고, self.abort 로 빠져나온다.
        """
        marker_id = self.align_to_marker()
        if self.abort:
            return
        if marker_id is not None:
            self.get_logger().info(f'Alignment successful, marker ID={marker_id}')
        else:
            self.get_logger().warn('Marker not found (search failed)')

        if self.scan_for_fall(room_key):
            self.get_logger().warn(f'{room_key} fall suspected — holding on patient')
            self.hold_position()
            self.get_logger().info('Patient cleared — resuming')
        if self.abort:
            return

        # 병실 관찰 종료 -> 대시보드에 순찰 완료 신호
        self.complete_pub.publish(String(data=room_key))
        self.last_room = self._room_number_of(room_key)

        self._finish_task()

    def _room_number_of(self, room_key):
        for number, key in ROOM_KEY_MAP.items():
            if key == room_key:
                return number
        return None

    def _finish_task(self):
        """한 목적지 처리 완료. 순찰이면 다음 병실로, 수동이면 대기로."""
        if self.mode == 'PAUSED':
            return

        if self.task == 'PATROL':
            self.start_next_patrol()
        else:
            self.mode = 'IDLE'
            self.task = None
            self.current_mode = None
            self.get_logger().info('Manual move finished (mode=IDLE)')

    # =====================================================
    # 마커 정렬 · 낙상 스캔
    # =====================================================

    def _call_enable(self, client, on, name):
        """SetBool 서비스로 on/off 요청. 응답은 기다리지 않는다.

        (MultiThreadedExecutor 안에서 spin_until_future_complete 를 부르면
         자기 자신을 두 번 spin 하게 되므로 쓰지 않는다.)

        future 를 버리면 응답을 받기 전에 GC 되어 요청이 유실될 수 있으므로
        완료될 때까지 목록에 들고 있는다.
        """
        if not client.wait_for_service(timeout_sec=0.5):
            self.get_logger().warn(f'{name} service unavailable (skip)')
            return

        req = SetBool.Request()
        req.data = on
        future = client.call_async(req)
        self._pending_enables.append(future)
        self._pending_enables = [f for f in self._pending_enables if not f.done()]
        self.get_logger().info(f'{name} -> {"ON" if on else "OFF"}')

    def set_enable(self, on):
        self._call_enable(self.aruco_cli, on, 'aruco_enable')

    def set_fall_enable(self, on):
        self._call_enable(self.fall_cli, on, 'fall_enable')

    def _marker_visible(self):
        return (self.offset_time is not None
                and time.time() - self.offset_time < self.SEEN_TIMEOUT)

    def _spin_wait(self, seconds):
        """블로킹 대기. 구독 콜백은 executor의 다른 스레드가 갱신해 준다."""
        time.sleep(seconds)

    def align_to_marker(self):
        """마커를 찾아(탐색) 화면 중앙에 오도록 회전(정렬)한다.

        찾으면 마커 ID, 못 찾거나 중단되면 None.
        """
        self.set_enable(True)
        start = time.time()
        twist = Twist()

        try:
            # 1단계 — 마커가 보일 때까지 제자리 탐색 회전
            while rclpy.ok() and not self.abort:
                if self._marker_visible():
                    break
                if time.time() - start > self.ALIGN_TIMEOUT:
                    self.get_logger().warn('Marker search timed out')
                    return None
                twist.angular.z = self.SEARCH_SPEED
                self.cmd_vel_pub.publish(twist)
                self._spin_wait(0.05)

            # 2단계 — 화면 중앙으로 정렬
            while rclpy.ok() and not self.abort:
                if time.time() - start > self.ALIGN_TIMEOUT:
                    self.get_logger().warn('Marker align timed out')
                    return None

                if not self._marker_visible():
                    twist.angular.z = self.SEARCH_SPEED
                    self.cmd_vel_pub.publish(twist)
                    self._spin_wait(0.05)
                    continue

                offset = self.latest_offset
                if abs(offset) <= self.CENTER_TOL:
                    break

                turn = -self.ALIGN_K * offset
                turn = max(-self.MAX_TURN, min(self.MAX_TURN, turn))
                if 0 < abs(turn) < self.MIN_TURN:       # 모터 데드밴드 회피
                    turn = math.copysign(self.MIN_TURN, turn)
                twist.angular.z = turn
                self.cmd_vel_pub.publish(twist)
                self._spin_wait(0.05)

            self.stop_robot()
            if self.abort:
                return None
            return self.latest_ids[0] if self.latest_ids else None
        finally:
            self.stop_robot()
            self.set_enable(False)

    def _rotate_by(self, delta_deg):
        """제자리에서 delta_deg 만큼 회전. 도는 동안 낙상이 보이면 즉시 True."""
        target = math.radians(abs(delta_deg))
        direction = 1.0 if delta_deg > 0 else -1.0
        rotated = 0.0
        prev_yaw = self.yaw
        twist = Twist()

        while rclpy.ok() and not self.abort and rotated < target:
            if self.fall:
                self.stop_robot()
                return True
            twist.angular.z = direction * self.SCAN_SPEED
            self.cmd_vel_pub.publish(twist)
            self._spin_wait(0.05)

            if self.yaw is not None and prev_yaw is not None:
                d = self.yaw - prev_yaw
                d = math.atan2(math.sin(d), math.cos(d))   # -pi~pi 로 정규화
                rotated += abs(d)
            prev_yaw = self.yaw

        self.stop_robot()
        return False

    def scan_for_fall(self, name=''):
        """마커 중앙 기준 좌우로 천천히 훑으며 낙상 감지.

        스캔 동안만 YOLO를 켠다 (주행 중엔 꺼서 CPU 절약).
        낙상이 보이면 True — 이때는 hold_position 이 계속 응시해야 하므로 끄지 않는다.
        """
        self.get_logger().info(f'[{name}] Checking patient (scanning)...')
        self.fall = False
        self.fall_status = 'NO_PERSON'
        self.set_fall_enable(True)

        half = self.SCAN_HALF
        found = False
        # 왼쪽 half -> 오른쪽 2*half(왼끝->오른끝) -> 중앙 복귀(왼쪽 half)
        for delta in (half, -2 * half, half):
            if self._rotate_by(delta):
                found = True
                break
            if self.abort:
                break

        if not found:
            self.set_fall_enable(False)
        return found

    def hold_position(self):
        """낙상 환자를 향한 채 정지 유지. 10초 연속 안 보이면 복귀."""
        self.set_fall_enable(True)
        clear_since = None
        try:
            while rclpy.ok() and not self.abort:
                self.stop_robot()
                if self.fall:
                    clear_since = None          # 아직 낙상 -> 타이머 리셋
                elif clear_since is None:
                    clear_since = time.time()
                elif time.time() - clear_since >= self.HOLD_CLEAR_SEC:
                    break
                self._spin_wait(0.05)
        finally:
            self.set_fall_enable(False)

    # =====================================================
    # 도킹 회전
    # =====================================================

    def start_rotation(self):
        self.rotation_time = 0.0
        if self.rotation_timer is not None:
            self.rotation_timer.cancel()
        self.rotation_timer = self.create_timer(
            0.1, self.rotate, callback_group=self.cb_group)

    def rotate(self):
        twist = Twist()
        twist.angular.z = self.ANGULAR_SPEED
        self.cmd_vel_pub.publish(twist)

        self.rotation_time += 0.1
        if self.rotation_time >= 2.0 * math.pi / self.ANGULAR_SPEED:
            self.stop_rotation()
            self.get_logger().info('360 degree rotation completed.')
            self._finish_task()

    def stop_rotation(self):
        self.stop_robot()
        if self.rotation_timer is not None:
            self.rotation_timer.cancel()
            self.destroy_timer(self.rotation_timer)
            self.rotation_timer = None

    def stop_robot(self):
        self.cmd_vel_pub.publish(Twist())


def main(args=None):
    rclpy.init(args=args)
    node = PatrolNode()

    # 마커 정렬·낙상 스캔이 블로킹이라, 그 동안에도 서비스(일시정지)를
    # 받으려면 스레드가 여러 개 필요하다.
    executor = MultiThreadedExecutor()
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.abort = True
        node.stop_robot()
        node.set_enable(False)
        node.set_fall_enable(False)
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
