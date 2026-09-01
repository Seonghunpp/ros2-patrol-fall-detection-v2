#!/usr/bin/env python3
"""충전소와 병실의 ArUco 마커 동작을 담당하는 보조 컨트롤러."""

from dataclasses import dataclass
import math
import time
from typing import Callable, Optional

from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from std_msgs.msg import Bool, Float32, Int32MultiArray
from std_srvs.srv import SetBool


@dataclass(frozen=True)
class ArucoBehaviorResult:
    success: bool
    message: str


class ArucoBehaviorController:
    """주행 노드가 목적지에 도착한 뒤 호출하는 ArUco 동작 모음."""

    CHARGER_MARKER_ID = 249
    ROOM_MARKER_IDS = (0, 1, 2, 3)
    CONTROL_PERIOD = 0.05
    SEEN_TIMEOUT = 0.4

    TARGET_OFFSET = 0.05
    CENTER_TOL = 0.03
    TARGET_DISTANCE = 0.55
    DISTANCE_TOL = 0.02

    SEARCH_SPEED = 0.3            # 마커 탐색 회전 (0.6 -> 0.3)
    RETRY_SEARCH_SPEED = 0.15     # 재탐색 회전 (0.3 -> 0.15)
    SEARCH_ANGLE = 2.0 * math.pi
    ALIGN_K = 0.6
    MIN_TURN = 0.10
    MAX_TURN = 0.4
    APPROACH_K = 0.5
    APPROACH_MIN_SPEED = 0.03
    APPROACH_MAX_SPEED = 0.08

    CHARGER_TURN_ANGLE = math.pi
    ROOM_TURN_ANGLE = 3.0 * math.pi
    TURN_SPEED = 0.25             # 병실 확인 · 도킹 제자리 회전 (0.5 -> 0.25)
    BACKUP_DISTANCE = 0.30
    BACKUP_SPEED = 0.05

    def __init__(self, node: Node):
        self.node = node
        self.cmd_vel_pub = node.create_publisher(Twist, '/cmd_vel', 10)
        node.create_subscription(Int32MultiArray, '/charger_marker', self._charger_marker_cb, 10)
        node.create_subscription(Float32, '/charger_offset', self._charger_offset_cb, 10)
        node.create_subscription(Float32, '/charger_distance', self._charger_distance_cb, 10)
        node.create_subscription(Int32MultiArray, '/room_marker', self._room_marker_cb, 10)
        node.create_subscription(Float32, '/marker_offset', self._room_offset_cb, 10)
        node.create_subscription(Float32, '/marker_distance', self._room_distance_cb, 10)
        node.create_subscription(Bool, '/fall_detected', self._fall_cb, 10)
        node.create_subscription(Odometry, '/odom', self._odom_cb, 10)
        self.aruco_client = node.create_client(SetBool, '/aruco_enable')
        self.fall_client = node.create_client(SetBool, '/fall_enable')

        self.charger_target_distance = self._parameter('charger_target_distance', self.TARGET_DISTANCE)
        self.room_target_distance = self._parameter('room_target_distance', self.TARGET_DISTANCE)
        self.backup_distance = self._parameter('charger_backup_distance', self.BACKUP_DISTANCE)

        self.charger_visible = False
        self.room_visible = False
        self.charger_offset = self.room_offset = 0.0
        self.charger_distance = self.room_distance = None
        self.charger_update_time = self.room_update_time = None
        self.fall_detected = False

        self.yaw = self.previous_yaw = None
        self.rotated = 0.0
        self.odom_x = self.odom_y = None
        self.backup_start_x = self.backup_start_y = None

        self.mode = self.state = None
        self.search_retried = False
        self.timer = self.done_callback = None

    def _parameter(self, name, default):
        if not self.node.has_parameter(name):
            self.node.declare_parameter(name, default)
        return float(self.node.get_parameter(name).value)

    def _charger_marker_cb(self, msg):
        self.charger_visible = self.CHARGER_MARKER_ID in msg.data

    def _room_marker_cb(self, msg):
        self.room_visible = any(marker_id in self.ROOM_MARKER_IDS for marker_id in msg.data)

    def _charger_offset_cb(self, msg):
        self.charger_offset = float(msg.data)
        self.charger_update_time = time.monotonic()

    def _room_offset_cb(self, msg):
        self.room_offset = float(msg.data)
        self.room_update_time = time.monotonic()

    def _charger_distance_cb(self, msg):
        self.charger_distance = float(msg.data)
        self.charger_update_time = time.monotonic()

    def _room_distance_cb(self, msg):
        self.room_distance = float(msg.data)
        self.room_update_time = time.monotonic()

    def _fall_cb(self, msg):
        self.fall_detected = bool(msg.data)

    def _odom_cb(self, msg):
        self.odom_x = msg.pose.pose.position.x
        self.odom_y = msg.pose.pose.position.y
        q = msg.pose.pose.orientation
        siny = 2.0 * (q.w * q.z + q.x * q.y)
        cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.yaw = math.atan2(siny, cosy)

    def is_busy(self):
        return self.state is not None

    def start_charging_parking(self, done_callback: Optional[Callable[[ArucoBehaviorResult], None]] = None):
        """충전소 마커 탐색부터 후면 주차까지 시작한다."""
        return self._start('charging', done_callback)

    def start_room_inspection(self, done_callback: Optional[Callable[[ArucoBehaviorResult], None]] = None):
        """병실 마커 정렬과 540도 낙상 탐색을 시작한다."""
        return self._start('room', done_callback)

    def _start(self, mode, done_callback):
        if self.is_busy():
            return False
        self.mode = mode
        self.done_callback = done_callback
        self.search_retried = False
        self._change_state('search', reset_rotation=True)
        if not self._set_aruco_enabled(True):
            self._finish(False, 'ArUco 인식을 켤 수 없습니다.')
            return False
        self.timer = self.node.create_timer(self.CONTROL_PERIOD, self._control)
        self.node.get_logger().info(f'{mode} ArUco 마커 탐색 시작')
        return True

    def cancel(self, message='ArUco 동작이 취소됐습니다.', notify=True):
        if not self.is_busy():
            return False
        self._finish(False, message, notify=notify)
        return True

    def _control(self):
        if not self.is_busy():
            return
        if self.yaw is None:
            self._stop_robot()
            return
        if self.state in ('search', 'charger_turn', 'room_turn'):
            self.rotated += self._consume_yaw_delta()

        twist = Twist()
        if self.state == 'search':
            if self._marker_is_fresh():
                self._change_state('align')
                return
            if self.rotated >= self.SEARCH_ANGLE and not self.search_retried:
                self.search_retried = True
                self.node.get_logger().warn('360도 탐색 실패: 0.3 rad/s로 계속 탐색합니다.')
            twist.angular.z = -(self.RETRY_SEARCH_SPEED if self.search_retried else self.SEARCH_SPEED)

        elif self.state == 'align':
            if not self._marker_is_fresh():
                self._change_state('search', reset_rotation=True)
                return
            center_error = self._offset() - self.TARGET_OFFSET
            if abs(center_error) <= self.CENTER_TOL:
                self._change_state('approach')
                return
            twist.angular.z = self._turn_speed(center_error)

        elif self.state == 'approach':
            if not self._marker_is_fresh() or self._distance() is None:
                self._stop_robot()
                return
            center_error = self._offset() - self.TARGET_OFFSET
            if abs(center_error) > self.CENTER_TOL:
                self._change_state('align')
                return
            distance_error = self._distance() - self._target_distance()
            if abs(distance_error) <= self.DISTANCE_TOL:
                next_state = 'charger_turn' if self.mode == 'charging' else 'room_turn'
                self._set_aruco_enabled(False)
                if self.mode == 'room' and not self._set_fall_enabled(True):
                    self._finish(False, '낙상 감지를 켤 수 없습니다.')
                    return
                self._change_state(next_state, reset_rotation=True)
                return
            twist.linear.x = self._linear_speed(distance_error)
            twist.angular.z = -self.ALIGN_K * center_error

        elif self.state == 'charger_turn':
            if self.rotated >= self.CHARGER_TURN_ANGLE:
                self.backup_start_x, self.backup_start_y = self.odom_x, self.odom_y
                self._change_state('backup')
                return
            twist.angular.z = -self.TURN_SPEED

        elif self.state == 'room_turn':
            if self.fall_detected:
                self._change_state('fall_hold')
                self.node.get_logger().warn('낙상 대상 감지: 즉시 정지')
                return
            if self.rotated >= self.ROOM_TURN_ANGLE:
                self._finish(True, '병실 확인 완료: 순찰을 재개합니다.')
                return
            twist.angular.z = -self.TURN_SPEED

        elif self.state == 'fall_hold':
            if self.fall_detected:
                self._stop_robot()
                return
            self._change_state('room_turn')
            self.node.get_logger().info('낙상 해제 5초 유지: 남은 회전 재개')
            return

        elif self.state == 'backup':
            backed_up = math.hypot(self.odom_x - self.backup_start_x, self.odom_y - self.backup_start_y)
            if backed_up >= self.backup_distance:
                self._finish(True, '충전소 후면 주차 완료: 대기합니다.')
                return
            twist.linear.x = -self.BACKUP_SPEED

        self.cmd_vel_pub.publish(twist)

    def _marker_is_fresh(self):
        visible = self.charger_visible if self.mode == 'charging' else self.room_visible
        updated = self.charger_update_time if self.mode == 'charging' else self.room_update_time
        return visible and updated is not None and time.monotonic() - updated < self.SEEN_TIMEOUT

    def _offset(self):
        return self.charger_offset if self.mode == 'charging' else self.room_offset

    def _distance(self):
        return self.charger_distance if self.mode == 'charging' else self.room_distance

    def _target_distance(self):
        return self.charger_target_distance if self.mode == 'charging' else self.room_target_distance

    def _turn_speed(self, center_error):
        angular = -self.ALIGN_K * center_error
        magnitude = min(self.MAX_TURN, max(self.MIN_TURN, abs(angular)))
        return math.copysign(magnitude, angular)

    def _linear_speed(self, distance_error):
        magnitude = min(self.APPROACH_MAX_SPEED, max(self.APPROACH_MIN_SPEED, self.APPROACH_K * abs(distance_error)))
        return math.copysign(magnitude, distance_error)

    def _set_aruco_enabled(self, enabled):
        if not self.aruco_client.wait_for_service(timeout_sec=1.0):
            self.node.get_logger().warn('/aruco_enable service unavailable')
            return False
        request = SetBool.Request()
        request.data = enabled
        self.aruco_client.call_async(request)
        return True

    def _set_fall_enabled(self, enabled):
        if not self.fall_client.wait_for_service(timeout_sec=1.0):
            self.node.get_logger().warn('/fall_enable service unavailable')
            return False
        request = SetBool.Request()
        request.data = enabled
        self.fall_client.call_async(request)
        return True

    def _consume_yaw_delta(self):
        if self.previous_yaw is None:
            self.previous_yaw = self.yaw
            return 0.0
        delta = self.yaw - self.previous_yaw
        delta = math.atan2(math.sin(delta), math.cos(delta))
        self.previous_yaw = self.yaw
        return abs(delta)

    def _change_state(self, state, reset_rotation=False):
        self._stop_robot()
        self.state = state
        self.previous_yaw = self.yaw
        if reset_rotation:
            self.rotated = 0.0

    def _stop_robot(self):
        self.cmd_vel_pub.publish(Twist())

    def _finish(self, success, message, notify=True):
        callback = self.done_callback
        self._stop_robot()
        if self.mode == 'room':
            self._set_fall_enabled(False)
        self._set_aruco_enabled(False)
        self.mode = self.state = self.done_callback = None
        if self.timer is not None:
            self.timer.cancel()
            self.node.destroy_timer(self.timer)
            self.timer = None
        result = ArucoBehaviorResult(success=success, message=message)
        logger = self.node.get_logger()
        (logger.info if success else logger.warn)(message)
        if notify and callback is not None:
            callback(result)
