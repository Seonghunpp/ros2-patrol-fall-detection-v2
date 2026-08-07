#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient

from nav2_msgs.action import NavigateToPose


class ChargingReturn(Node):

    def __init__(self):
        super().__init__('charging_return')

        # Nav2 NavigateToPose Action Client
        self.nav_client = ActionClient(
            self,
            NavigateToPose,
            'navigate_to_pose'
        )

        # 충전 스테이션 좌표
        self.charging_x = -1.878
        self.charging_y = 3.069

        # tf2_echo에서 얻은 Quaternion
        self.charging_qz = 0.803
        self.charging_qw = 0.595

        self.get_logger().info('충전 스테이션 복귀 노드 시작')

        self.send_charging_goal()

    def send_charging_goal(self):

        self.get_logger().info(
            'Nav2 action server를 기다리는 중...'
        )

        self.nav_client.wait_for_server()

        goal_msg = NavigateToPose.Goal()

        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = (
            self.get_clock().now().to_msg()
        )

        # 위치
        goal_msg.pose.pose.position.x = self.charging_x
        goal_msg.pose.pose.position.y = self.charging_y
        goal_msg.pose.pose.position.z = 0.0

        # 방향
        goal_msg.pose.pose.orientation.x = 0.0
        goal_msg.pose.pose.orientation.y = 0.0
        goal_msg.pose.pose.orientation.z = self.charging_qz
        goal_msg.pose.pose.orientation.w = self.charging_qw

        self.get_logger().info(
            f'충전 스테이션으로 이동 시작 '
            f'(x={self.charging_x}, y={self.charging_y})'
        )

        future = self.nav_client.send_goal_async(
            goal_msg,
            feedback_callback=self.feedback_callback
        )

        future.add_done_callback(
            self.goal_response_callback
        )

    def goal_response_callback(self, future):

        goal_handle = future.result()

        if not goal_handle.accepted:
            self.get_logger().error(
                'Nav2가 충전 스테이션 Goal을 거절했습니다.'
            )
            return

        self.get_logger().info(
            '충전 스테이션 Goal이 승인되었습니다.'
        )

        result_future = goal_handle.get_result_async()

        result_future.add_done_callback(
            self.result_callback
        )

    def feedback_callback(self, feedback_msg):

        feedback = feedback_msg.feedback

        distance = feedback.distance_remaining

        self.get_logger().info(
            f'충전 스테이션까지 남은 거리: {distance:.2f} m'
        )

    def result_callback(self, future):

        result = future.result()

        status = result.status

        if status == 4:
            self.get_logger().info(
                '충전 스테이션 위치에 도착했습니다!'
            )
        else:
            self.get_logger().warn(
                f'이동이 정상 완료되지 않았습니다. status={status}'
            )


def main(args=None):

    rclpy.init(args=args)

    node = ChargingReturn()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
