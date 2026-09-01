#!/usr/bin/env python3
"""ArUco 동작을 서비스로 직접 시작하는 현장 테스트 노드."""

import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger

from .aruco_behavior_controller import ArucoBehaviorController


class ArucoBehaviorTestNode(Node):
    def __init__(self):
        super().__init__('aruco_behavior_test')
        self.controller = ArucoBehaviorController(self)

        self.create_service(
            Trigger, '/test_charging_aruco', self._start_charging)
        self.create_service(
            Trigger, '/test_room_aruco', self._start_room)
        self.create_service(
            Trigger, '/test_cancel_aruco', self._cancel)

        self.get_logger().info(
            'ArUco test ready: /test_charging_aruco, /test_room_aruco')

    def _start_charging(self, _request, response):
        response.success = self.controller.start_charging_parking(
            self._completed)
        response.message = (
            '충전소 ArUco 테스트 시작'
            if response.success else '다른 ArUco 동작이 실행 중입니다.')
        return response

    def _start_room(self, _request, response):
        response.success = self.controller.start_room_inspection(
            self._completed)
        response.message = (
            '병실 ArUco 테스트 시작'
            if response.success else '다른 ArUco 동작이 실행 중입니다.')
        return response

    def _cancel(self, _request, response):
        response.success = self.controller.cancel('테스트 동작 취소')
        response.message = (
            'ArUco 테스트를 취소했습니다.'
            if response.success else '실행 중인 ArUco 동작이 없습니다.')
        return response

    def _completed(self, result):
        self.get_logger().info(
            f'ArUco test completed: success={result.success}, '
            f'message={result.message}')


def main(args=None):
    rclpy.init(args=args)
    node = ArucoBehaviorTestNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.controller.cancel('테스트 노드 종료')
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
