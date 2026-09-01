import rclpy
from rclpy.node import Node

from geometry_msgs.msg import PoseWithCovarianceStamped


class AutoInitialPose(Node):

    def __init__(self):
        super().__init__('auto_initial_pose')

        self.initial_pose_pub = self.create_publisher(
            PoseWithCovarianceStamped,
            '/initialpose',
            10
        )

        # 충전 스테이션 좌표
        self.charging_x = -1.878
        self.charging_y = 3.069

        self.charging_qz = 0.803
        self.charging_qw = 0.595

        self.publish_count = 0

        # 1초마다 확인
        self.timer = self.create_timer(
            1.0,
            self.publish_initial_pose
        )

        self.get_logger().info(
            'Auto Initial Pose Node Started'
        )


    def publish_initial_pose(self):

        # /initialpose를 구독하는 노드가 있는지 확인
        subscriber_count = (
            self.initial_pose_pub
            .get_subscription_count()
        )

        if subscriber_count == 0:

            self.get_logger().info(
                'Waiting for AMCL...'
            )

            return

        # 메시지 생성
        msg = PoseWithCovarianceStamped()

        msg.header.frame_id = 'map'
        msg.header.stamp = (
            self.get_clock()
            .now()
            .to_msg()
        )

        # Position
        msg.pose.pose.position.x = self.charging_x
        msg.pose.pose.position.y = self.charging_y
        msg.pose.pose.position.z = 0.0

        # Orientation
        msg.pose.pose.orientation.x = 0.0
        msg.pose.pose.orientation.y = 0.0
        msg.pose.pose.orientation.z = self.charging_qz
        msg.pose.pose.orientation.w = self.charging_qw

        # Covariance
        msg.pose.covariance[0] = 0.25
        msg.pose.covariance[7] = 0.25
        msg.pose.covariance[35] = 0.0685

        self.initial_pose_pub.publish(msg)

        self.publish_count += 1

        self.get_logger().info(
            f'Initial pose published '
            f'({self.publish_count}/3)'
        )

        # 안정성을 위해 3번 전송
        if self.publish_count >= 3:

            self.timer.cancel()

            self.get_logger().info(
                'Initial pose setup complete.'
            )


def main(args=None):

    rclpy.init(args=args)

    node = AutoInitialPose()

    try:
        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:

        node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
