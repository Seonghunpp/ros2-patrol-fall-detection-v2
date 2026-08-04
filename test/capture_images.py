from pathlib import Path
from datetime import datetime

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSProfile,
    QoSHistoryPolicy,
    QoSReliabilityPolicy,
)
from sensor_msgs.msg import CompressedImage


SAVE_DIR = Path.home() / "Desktop" / "raw_images"


class ImageCaptureNode(Node):
    def __init__(self):
        super().__init__("image_capture_node")

        SAVE_DIR.mkdir(parents=True, exist_ok=True)

        image_qos = QoSProfile(
            depth=1,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
        )

        self.current_frame = None
        self.image_count = 0

        self.create_subscription(
            CompressedImage,
            "/image_raw/compressed",
            self.image_callback,
            image_qos,
        )

        # 약 30FPS로 화면과 키 입력 처리
        self.create_timer(0.03, self.display_callback)

        self.get_logger().info("S: 저장 / Q: 종료")
        self.get_logger().info(f"저장 경로: {SAVE_DIR}")

    def image_callback(self, msg):
        np_arr = np.frombuffer(msg.data, dtype=np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if frame is not None:
            self.current_frame = frame

    def display_callback(self):
        if self.current_frame is None:
            return

        cv2.imshow("TurtleBot Camera", self.current_frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("s"):
            self.save_image()

        elif key == ord("q"):
            rclpy.shutdown()

    def save_image(self):
        now = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        file_path = SAVE_DIR / f"patient_{now}.jpg"

        if cv2.imwrite(str(file_path), self.current_frame):
            self.image_count += 1
            self.get_logger().info(
                f"저장 완료 #{self.image_count}: {file_path}"
            )
        else:
            self.get_logger().error("이미지 저장 실패")


def main(args=None):
    rclpy.init(args=args)
    node = ImageCaptureNode()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()

        if rclpy.ok():
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    main()