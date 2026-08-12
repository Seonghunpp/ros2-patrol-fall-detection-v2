import math
import os
import time
import yaml

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from tf2_ros import Buffer, TransformListener

from ament_index_python.packages import get_package_share_directory

from nav2_msgs.action import NavigateToPose
from action_msgs.msg import GoalStatus
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32, Int32MultiArray
from std_srvs.srv import SetBool, Trigger
from geometry_msgs.msg import Twist, PoseStamped


class NavigationService(Node):

    # 현재 위치 판정 허용 오차(m).
    # 판정이 갈리는 가장 가까운 두 지점(room4의 hall <-> inside)이 약 1.13 m라
    # 그 절반보다 짧게 잡는다. 이보다 멀면 '모름'으로 두고 탈출 경유점을 안 붙인다
    PLACE_MATCH_DIST = 0.5

    CHARGER_MARKER_ID = 249
    CHARGER_SEEN_TIMEOUT = 0.4
    CHARGER_CENTER_TOL = 0.05
    CHARGER_SEARCH_SPEED = 0.2
    CHARGER_ALIGN_K = 0.6
    CHARGER_MIN_TURN = 0.10
    CHARGER_MAX_TURN = 0.4
    CHARGER_TURN_SPEED = 0.25
    CHARGER_SEARCH_ANGLE = 2.0 * math.pi
    CHARGER_TURN_ANGLE = math.pi
    CHARGER_SEARCH_TIMEOUT = 45.0
    CHARGER_TURN_TIMEOUT = 20.0

    def __init__(self):
        super().__init__("navigation_service")
        # Nav2 Action Client
        self.nav_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        # 충전소 탐색·정렬·180도 회전용 Publisher
        self.cmd_vel_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.create_subscription(
            Int32MultiArray, "/charger_marker", self.charger_id_callback, 10
        )
        self.create_subscription(
            Float32, "/charger_offset", self.charger_offset_callback, 10
        )
        self.create_subscription(Odometry, "/odom", self.odom_callback, 10)
        self.aruco_client = self.create_client(SetBool, "/aruco_enable")
        # 현재 위치를 모를 때만 참고할 TF (map -> base_footprint)
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        # YAML 파일 경로
        self.config_path = os.path.join(
            get_package_share_directory("my_patrol"), "config", "rooms.yaml"
        )
        # YAML 읽기
        self.load_waypoints()
        # 서비스
        self.create_service(Trigger, "/go_to_dock", self.go_to_dock_callback)
        self.create_service(Trigger, "/go_to_standby", self.go_to_standby_callback)
        self.create_service(Trigger, "/go_to_room1", self.go_to_room1_callback)
        self.create_service(Trigger, "/go_to_room2", self.go_to_room2_callback)
        self.create_service(Trigger, "/go_to_room3", self.go_to_room3_callback)
        self.create_service(Trigger, "/go_to_room4", self.go_to_room4_callback)
        self.create_service(
            Trigger, "/pause_navigation", self.pause_navigation_callback
        )
        self.create_service(
            Trigger, "/resume_navigation", self.resume_navigation_callback
        )
        # 상태
        self.navigation_busy = False
        self.pending_waypoints = []
        self.current_mode = None
        # ==========================================
        # 일시정지
        #
        # Nav2 goal을 취소해서 멈추고, 남은 경유점은 그대로 들고 있는다.
        # 이어서 가면 취소된 지점부터 다시 간다.
        # active_waypoint는 '지금 가고 있던' 경유점 — 취소하면 아직 도착을
        # 못 한 것이므로 pending 맨 앞으로 되돌려 놓아야 한다.
        # ==========================================
        self.paused = False
        self.goal_handle = None
        self.active_waypoint = None
        # ==========================================
        # 마지막으로 도착한 목적지 (yaml 키: dock/standby/room1~4)
        #
        # 병실 안에서 출발할 때 그 병실 문 앞으로 먼저 나오려면
        # "지금 어디 있는지"를 알아야 한다. 기본은 이 노드가 보낸 이동의
        # 결과를 기억하는 것이고, 모를 때만 TF로 한 번 확인한다.
        # ==========================================
        self.current_place = None
        # 충전소 마커 탐색·정렬 상태
        self.charger_visible = False
        self.latest_charger_offset = 0.0
        self.charger_offset_time = None
        self.yaw = None
        self.previous_yaw = None
        self.charger_rotated = 0.0
        self.charger_state = None
        self.charger_deadline = None
        self.rotation_timer = None
        self.get_logger().info("Navigation Service Node Started")

    # ==========================================
    # YAML 읽기
    # ==========================================
    def load_waypoints(self):
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.waypoints = yaml.safe_load(f)
        self.get_logger().info(f"Waypoint file loaded: {self.config_path}")

    # ==========================================
    # 현재 위치 확인 (기억이 없을 때만)
    #
    # 등록된 모든 지점(병실 hall/inside, dock, standby) 중 가장 가까운 것을
    # 찾아, 그게 어떤 병실의 inside면 그 병실 안에 있는 것으로 본다.
    #
    # amcl_pose 토픽이 아니라 TF를 읽는 이유는, amcl_pose는 로봇이 움직여야
    # 발행돼서 켜자마자 첫 명령을 주면 값이 아예 없기 때문. TF는 정지 상태에서도
    # 계속 나온다. waypoint_saver가 좌표를 찍을 때 쓰는 방식과 같다.
    #
    # 가까운 지점이 하나도 없으면(위치 추정이 어긋났거나 엉뚱한 곳에 있으면)
    # 억지로 고르지 않고 None을 돌려준다 — 잘못된 병실 입구를 들르는 것보다
    # 안 들르는 편이 낫다.
    # ==========================================
    def locate_current_place(self):
        try:
            tf = self.tf_buffer.lookup_transform(
                "map", "base_footprint", rclpy.time.Time()
            )
        except Exception as e:  # noqa: BLE001
            self.get_logger().warn(f"TF lookup failed: {e}")
            return None
        robot_x = tf.transform.translation.x
        robot_y = tf.transform.translation.y
        nearest_room = None
        nearest_dist = None

        def check(point, room_name):
            nonlocal nearest_room, nearest_dist
            if not isinstance(point, dict) or "x" not in point:
                return
            dist = math.hypot(float(point["x"]) - robot_x, float(point["y"]) - robot_y)
            if nearest_dist is None or dist < nearest_dist:
                nearest_dist = dist
                nearest_room = room_name

        rooms = self.waypoints.get("rooms") or {}
        for room_name, room in rooms.items():
            if not isinstance(room, dict):
                continue
            # hall(복도)은 병실 '안'이 아니므로 이름을 달지 않는다
            check(room.get("hall"), None)
            check(room.get("inside", room), room_name)
        check(self.waypoints.get("dock"), None)
        check(self.waypoints.get("standby"), None)
        if nearest_dist is None or nearest_dist > self.PLACE_MATCH_DIST:
            self.get_logger().warn(
                "Current place unknown " "(no registered waypoint nearby)."
            )
            return None
        self.get_logger().info(
            f"Located by TF: " f'{nearest_room or "hallway"} ' f"({nearest_dist:.2f} m)"
        )
        return nearest_room

    # ==========================================
    # 출발 병실 탈출 경유점
    #
    # 병실 안에서 목적지를 바로 찍으면 Nav2가 문을 비스듬히 빠져나가는
    # 경로를 짜서 ㄱ자로 꺾인다. 그래서 지금 있는 병실의 문 앞(hall)을
    # 맨 앞에 붙여 복도로 먼저 나오게 한다.
    #
    # - 병실이 아닌 곳(dock/standby)에서 출발하면 나올 필요가 없다
    # - 목적지가 지금 있는 병실 자신이면 나올 필요가 없다
    # ==========================================
    def exit_waypoints(self, target_name):
        here = self.current_place
        # 기억이 없으면(노드를 막 켰거나 직전 이동이 실패했으면) 위치를 한 번 확인한다
        if here is None:
            here = self.locate_current_place()
            self.current_place = here
        if here is None or here == target_name:
            return []
        room = (self.waypoints.get("rooms") or {}).get(here)
        if not isinstance(room, dict):
            return []
        hall = room.get("hall")
        if not hall:
            return []
        self.get_logger().info(f"Leaving {here} through its hall first.")
        return [hall]

    # ==========================================
    # 충전 스테이션
    # YAML의 dock 사용
    # ==========================================
    def go_to_dock_callback(self, request, response):
        # 서비스 호출 시 YAML 다시 읽기
        self.load_waypoints()
        dock = self.waypoints.get("dock")
        points = self.exit_waypoints("dock")
        if dock:
            points.append(dock)
        return self.start_navigation("dock", points, response)

    # ==========================================
    # 대기 장소
    # YAML의 standby 사용
    # ==========================================
    def go_to_standby_callback(self, request, response):
        self.load_waypoints()
        standby = self.waypoints.get("standby")
        points = self.exit_waypoints("standby")
        if standby:
            points.append(standby)
        return self.start_navigation("standby", points, response)

    # ==========================================
    # Room 1
    # ==========================================
    def go_to_room1_callback(self, request, response):
        return self.go_to_room("room1", response)

    # ==========================================
    # Room 2
    # ==========================================
    def go_to_room2_callback(self, request, response):
        return self.go_to_room("room2", response)

    # ==========================================
    # Room 3
    # ==========================================
    def go_to_room3_callback(self, request, response):
        return self.go_to_room("room3", response)

    # ==========================================
    # Room 4
    # ==========================================
    def go_to_room4_callback(self, request, response):
        return self.go_to_room("room4", response)

    # ==========================================
    # 병실 공통 처리
    # (출발 병실 hall) -> 목표 hall -> inside
    # ==========================================
    def go_to_room(self, room_name, response):
        self.load_waypoints()
        room = (self.waypoints.get("rooms") or {}).get(room_name)
        if room is None:
            response.success = False
            response.message = f"{room_name} 좌표가 등록되어 있지 않습니다."
            return response
        # hall(문 앞)은 waypoint_saver에서 s로 건너뛸 수 있어 없을 수도 있다.
        # 있으면 hall -> inside 순으로 가고(복도끼리 이동이라 벽을 안 건넌다),
        # 없으면 inside로 바로 간다. patrol_node가 좌표를 읽는 방식과 같다.
        hall = room.get("hall")
        inside = room.get("inside", room)
        points = self.exit_waypoints(room_name)
        points += [point for point in (hall, inside) if point]
        return self.start_navigation(room_name, points, response)

    # ==========================================
    # 일시정지
    #
    # Nav2 goal을 취소해서 멈춘다. 남은 경유점은 그대로 두고,
    # 취소된 지점은 아직 도착 전이므로 맨 앞에 되돌려 놓는다.
    # ==========================================
    def pause_navigation_callback(self, request, response):
        # 충전소 탐색·정렬·회전 중이면 동작을 멈춘다 (이어갈 경로가 없다)
        if self.rotation_timer is not None:
            self.stop_rotation()
            self.navigation_busy = False
            self.current_mode = None
            response.success = True
            response.message = "회전을 멈췄습니다."
            return response
        if not self.navigation_busy:
            response.success = False
            response.message = "이동 중이 아닙니다."
            return response
        self.paused = True
        self.navigation_busy = False
        if self.active_waypoint is not None:
            self.pending_waypoints.insert(0, self.active_waypoint)
            self.active_waypoint = None
        if self.goal_handle is not None:
            self.goal_handle.cancel_goal_async()
        self.stop_robot()
        # 경유점 사이 어딘가에서 멈췄으므로 현재 위치를 알 수 없다.
        # 다음 이동 때 TF로 다시 확인하게 둔다
        self.current_place = None
        self.get_logger().info(
            f"Paused. {len(self.pending_waypoints)} " f"waypoint(s) left."
        )
        response.success = True
        response.message = "이동을 일시정지했습니다."
        return response

    # ==========================================
    # 이어서 이동
    # ==========================================
    def resume_navigation_callback(self, request, response):
        if not self.paused:
            response.success = False
            response.message = "일시정지 상태가 아닙니다."
            return response
        if not self.pending_waypoints:
            self.paused = False
            response.success = False
            response.message = "이어서 갈 경로가 없습니다."
            return response
        self.paused = False
        self.navigation_busy = True
        if not self.send_next_waypoint():
            # 다시 일시정지 상태로 되돌린다 (남은 경로는 그대로 유지)
            self.navigation_busy = False
            self.paused = True
            response.success = False
            response.message = "Nav2에 연결할 수 없습니다."
            return response
        self.get_logger().info("Resumed.")
        response.success = True
        response.message = "이동을 다시 시작합니다."
        return response

    # ==========================================
    # 이동 시작 (모든 목적지 공통)
    # ==========================================
    def start_navigation(self, mode, points, response):
        if self.navigation_busy:
            response.success = False
            response.message = "일시정지 후 경로를 변경하세요."
            return response
        # 일시정지 상태에서 새 목적지를 받으면 남은 경로는 버리고 새로 시작한다
        self.paused = False
        self.pending_waypoints = []
        self.active_waypoint = None
        if not points:
            response.success = False
            response.message = "등록된 좌표가 없습니다."
            return response
        route = " -> ".join(f"({point['x']}, {point['y']})" for point in points)
        self.get_logger().info(f"{mode}: {len(points)} waypoint(s) — {route}")
        self.navigation_busy = True
        self.current_mode = mode
        self.pending_waypoints = list(points)
        # 첫 경유점 전송 실패(Nav2 미실행)면 이동을 시작하지 못한 것이므로
        # 성공으로 응답하지 않는다
        if not self.send_next_waypoint():
            self.navigation_busy = False
            self.current_mode = None
            self.pending_waypoints = []
            response.success = False
            response.message = "Nav2에 연결할 수 없습니다."
            return response
        response.success = True
        response.message = f"{mode} 이동을 시작합니다."
        return response

    # ==========================================
    # 다음 waypoint 이동
    #
    # 경유점마다 도착을 확인하고 다음 goal을 보낸다.
    # 지점마다 한 번 멈췄다 출발한다.
    # ==========================================
    def send_next_waypoint(self):
        if not self.pending_waypoints:
            self.navigation_completed()
            return True
        waypoint = self.pending_waypoints.pop(0)
        return self.send_goal(waypoint)

    # ==========================================
    # Nav2 Goal 전송
    # ==========================================
    def send_goal(self, waypoint):
        # 대시보드는 서비스 응답을 3초까지 기다리므로 그보다 짧게 잡는다
        if not self.nav_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error("NavigateToPose Action Server " "not available.")
            return False
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = self.make_pose(waypoint)
        # 일시정지 시 되돌려 놓아야 하므로 '지금 가는 중인' 경유점을 기억한다
        self.active_waypoint = waypoint
        self.get_logger().info(f"Goal: x={waypoint['x']}, " f"y={waypoint['y']}")
        future = self.nav_client.send_goal_async(
            goal_msg, feedback_callback=self.feedback_callback
        )
        future.add_done_callback(self.goal_response_callback)
        return True

    # ==========================================
    # waypoint dict -> PoseStamped
    # ==========================================
    def make_pose(self, waypoint):
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = float(waypoint["x"])
        pose.pose.position.y = float(waypoint["y"])
        pose.pose.position.z = 0.0
        pose.pose.orientation.x = 0.0
        pose.pose.orientation.y = 0.0
        pose.pose.orientation.z = float(waypoint["z"])
        pose.pose.orientation.w = float(waypoint["w"])
        return pose

    # ==========================================
    # Goal 승인
    # ==========================================
    def goal_response_callback(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().error("Navigation goal rejected.")
            self.navigation_failed()
            return
        # 일시정지가 취소할 대상
        self.goal_handle = goal_handle
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self.result_callback)

    # ==========================================
    # 이동 피드백
    # ==========================================
    def feedback_callback(self, feedback_msg):
        distance = feedback_msg.feedback.distance_remaining
        self.get_logger().info(f"Distance remaining: " f"{distance:.2f} m")

    # ==========================================
    # waypoint 도착
    # ==========================================
    def result_callback(self, future):
        result = future.result()
        # 일시정지로 취소한 결과는 실패가 아니다. 남은 경로를 그대로 들고 대기
        if self.paused:
            self.get_logger().info("Navigation paused " "(goal cancelled).")
            return
        if result.status == (GoalStatus.STATUS_SUCCEEDED):
            self.get_logger().info("Waypoint reached.")
            if not self.send_next_waypoint():
                self.navigation_failed()
        else:
            self.get_logger().error(f"Navigation failed. " f"status={result.status}")
            self.navigation_failed()

    # ==========================================
    # 이동 실패 · 중단
    #
    # 어디서 멈췄는지 알 수 없으므로 현재 위치 기억을 지운다.
    # (모르는 채로 두면 다음 이동에서 엉뚱한 병실 입구를 들른다)
    # ==========================================
    def navigation_failed(self):
        self.navigation_busy = False
        self.paused = False
        self.current_mode = None
        self.current_place = None
        self.pending_waypoints = []
        self.active_waypoint = None
        self.goal_handle = None

    # ==========================================
    # 전체 목적지 완료
    # ==========================================
    def navigation_completed(self):
        # 다음 이동에서 "출발 병실 입구"를 계산하는 기준이 된다
        self.current_place = self.current_mode
        self.active_waypoint = None
        self.goal_handle = None
        if self.current_mode == "dock":
            self.get_logger().info("Charging station reached.")
            self.start_charger_alignment()
        else:
            self.get_logger().info(f"Navigation completed: " f"{self.current_mode}")
            self.navigation_busy = False
            self.current_mode = None

    # ==========================================
    # 충전소 ArUco 탐색 → 정렬 → 180도 회전
    # ==========================================
    def charger_id_callback(self, msg):
        self.charger_visible = self.CHARGER_MARKER_ID in msg.data

    def charger_offset_callback(self, msg):
        self.latest_charger_offset = msg.data
        self.charger_offset_time = time.monotonic()

    def odom_callback(self, msg):
        q = msg.pose.pose.orientation
        siny = 2.0 * (q.w * q.z + q.x * q.y)
        cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.yaw = math.atan2(siny, cosy)

    def charger_marker_visible(self):
        return (
            self.charger_visible
            and self.charger_offset_time is not None
            and time.monotonic() - self.charger_offset_time < self.CHARGER_SEEN_TIMEOUT
        )

    def set_aruco_enabled(self, enabled):
        if not self.aruco_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().warn("/aruco_enable service unavailable")
            return False
        request = SetBool.Request()
        request.data = enabled
        self.aruco_client.call_async(request)
        return True

    def start_charger_alignment(self):
        self.stop_robot()
        self.charger_visible = False
        self.charger_offset_time = None
        self.previous_yaw = self.yaw
        self.charger_rotated = 0.0
        self.charger_state = "search"
        self.charger_deadline = time.monotonic() + self.CHARGER_SEARCH_TIMEOUT
        if not self.set_aruco_enabled(True):
            self.finish_charger_alignment(False, "ArUco 인식을 켤 수 없습니다.")
            return
        if self.rotation_timer is not None:
            self.rotation_timer.cancel()
        self.rotation_timer = self.create_timer(0.05, self.charger_control)
        self.get_logger().info("ID 249 충전소 마커 탐색 시작")

    def consume_yaw_delta(self):
        if self.yaw is None:
            return 0.0
        if self.previous_yaw is None:
            self.previous_yaw = self.yaw
            return 0.0
        delta = self.yaw - self.previous_yaw
        delta = math.atan2(math.sin(delta), math.cos(delta))
        self.previous_yaw = self.yaw
        return abs(delta)

    def charger_control(self):
        if self.charger_state is None:
            return

        if self.yaw is None:
            self.stop_robot()
            if time.monotonic() >= self.charger_deadline:
                self.finish_charger_alignment(False, "odom을 받지 못했습니다.")
            return

        self.charger_rotated += self.consume_yaw_delta()
        if time.monotonic() >= self.charger_deadline:
            self.finish_charger_alignment(False, "충전소 정렬 시간 초과")
            return

        twist = Twist()
        if self.charger_state == "search":

            if self.charger_marker_visible():
                self.stop_robot()
                self.charger_state = "align"
                self.get_logger().info("ID 249 발견, 중앙 정렬 시작")
                return

            if self.charger_rotated >= self.CHARGER_SEARCH_ANGLE:
                self.finish_charger_alignment(False, "ID 249를 찾지 못했습니다.")
                return

            twist.angular.z = self.CHARGER_SEARCH_SPEED

        elif self.charger_state == "align":

            if not self.charger_marker_visible():
                self.charger_state = "search"
                self.get_logger().warn("마커 유실, 탐색 재개")
                return

            offset = self.latest_charger_offset

            if abs(offset) <= self.CHARGER_CENTER_TOL:
                self.stop_robot()
                self.charger_state = "turn"
                self.charger_rotated = 0.0
                self.previous_yaw = self.yaw
                self.charger_deadline = time.monotonic() + self.CHARGER_TURN_TIMEOUT

                self.get_logger().info("중앙 정렬 완료, 180도 회전 시작")
                return

            angular = -self.CHARGER_ALIGN_K * offset
            magnitude = min(
                self.CHARGER_MAX_TURN, max(self.CHARGER_MIN_TURN, abs(angular))
            )
            twist.angular.z = magnitude if angular > 0.0 else -magnitude

        elif self.charger_state == "turn":
            if self.charger_rotated >= self.CHARGER_TURN_ANGLE:
                self.finish_charger_alignment(True, "충전중 ...")
                return

            twist.angular.z = self.CHARGER_TURN_SPEED
        self.cmd_vel_pub.publish(twist)

    def finish_charger_alignment(self, success, message):
        self.stop_rotation()
        self.navigation_busy = False
        self.current_mode = None
        if success:
            self.get_logger().info(message)
        else:
            self.get_logger().warn(message)

    def stop_rotation(self):
        self.stop_robot()
        self.set_aruco_enabled(False)
        self.charger_state = None
        if self.rotation_timer is not None:
            self.rotation_timer.cancel()
            self.rotation_timer = None

    def stop_robot(self):
        twist = Twist()
        twist.linear.x = 0.0
        twist.angular.z = 0.0
        self.cmd_vel_pub.publish(twist)


def main(args=None):

    rclpy.init(args=args)

    node = NavigationService()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:
        node.stop_rotation()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
