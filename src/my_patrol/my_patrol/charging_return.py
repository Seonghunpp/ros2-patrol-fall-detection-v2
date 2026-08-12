#!/usr/bin/env python3

import math

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient

from nav2_msgs.action import NavigateToPose
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import Twist


class ChargingReturn(Node):

    def __init__(self):
        super().__init__('charging_return')

        # Nav2 Action Client
        self.nav_client = ActionClient(
            self,
            NavigateToPose,
            'navigate_to_pose'
        )

        # cmd_vel Publisher
        self.cmd_vel_pub = self.create_publisher(
            Twist,
            '/cmd_vel',
            10
        )

        # 충전 스테이션 좌표
        self.charging_x = -1.878
        self.charging_y = 3.069

        self.charging_qz = 0.803
        self.charging_qw = 0.595

        # 회전 설정
        self.angular_speed = 0.3

        self.rotation_duration = (
            2.0 * math.pi / self.angular_speed
        )

        self.rotation_time = 0.0
        self.rotation_timer = None

        self.get_logger().info(
            '충전 스테이션 복귀 노드 시작'
        )

        self.send_charging_goal()

    def send_charging_goal(self):

        self.get_logger().info(
            'Nav2 Action Server 대기 중...'
        )

        self.nav_client.wait_for_server()

        goal_msg = NavigateToPose.Goal()

        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = (
            self.get_clock().now().to_msg()
        )

        goal_msg.pose.pose.position.x = self.charging_x
        goal_msg.pose.pose.position.y = self.charging_y
        goal_msg.pose.pose.position.z = 0.0

        goal_msg.pose.pose.orientation.x = 0.0
        goal_msg.pose.pose.orientation.y = 0.0
        goal_msg.pose.pose.orientation.z = self.charging_qz
        goal_msg.pose.pose.orientation.w = self.charging_qw

        self.get_logger().info(
            '충전 스테이션으로 이동 시작'
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
                'Goal 거절'
            )
            return

        self.get_logger().info(
            'Goal 승인'
        )

        result_future = goal_handle.get_result_async()

        result_future.add_done_callback(
            self.result_callback
        )

    def feedback_callback(self, feedback_msg):

        distance = (
            feedback_msg.feedback.distance_remaining
        )

        self.get_logger().info(
            f'남은 거리: {distance:.2f} m'
        )

    def result_callback(self, future):

        result = future.result()

        if result.status == GoalStatus.STATUS_SUCCEEDED:

            self.get_logger().info(
                '충전 스테이션 도착!'
            )

            self.get_logger().info(
                '360도 회전 시작'
            )

            self.start_rotation()

        else:

            self.get_logger().error(
                f'이동 실패 status={result.status}'
            )

    def start_rotation(self):

        self.rotation_time = 0.0

        self.rotation_timer = self.create_timer(
            0.1,
            self.rotate
        )

    def rotate(self):

        twist = Twist()

        twist.linear.x = 0.0
        twist.angular.z = self.angular_speed

        self.cmd_vel_pub.publish(twist)

        self.rotation_time += 0.1

        if self.rotation_time >= self.rotation_duration:

            self.stop_robot()

            self.get_logger().info(
                '360도 회전 완료'
            )

    def stop_robot(self):

        twist = Twist()

        twist.linear.x = 0.0
        twist.angular.z = 0.0

        self.cmd_vel_pub.publish(twist)

        if self.rotation_timer is not None:

            self.rotation_timer.cancel()

            self.rotation_timer = None


def main(args=None):

    rclpy.init(args=args)

    node = ChargingReturn()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        node.stop_robot()

    node.destroy_node()

    rclpy.shutdown()


if __name__ == '__main__':
    main()
