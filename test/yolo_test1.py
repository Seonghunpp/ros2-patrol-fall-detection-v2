#!/usr/bin/env python3
import time
from pathlib import Path

import cv2
import numpy as np
import rclpy
import torch
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import CompressedImage
from ultralytics import YOLO


MODEL_PATH = Path(__file__).resolve().parent / "yolov8s-pose.pt"
INPUT_TOPIC = "/image_raw/compressed"
OUTPUT_TOPIC = "/yolo_pose_test/annotated/compressed"

IMAGE_SIZE = 640
PERSON_CONFIDENCE = 0.15
JPEG_QUALITY = 90

# COCO 17-keypoint indices
LEFT_SHOULDER = 5
RIGHT_SHOULDER = 6
LEFT_HIP = 11
RIGHT_HIP = 12
LEFT_KNEE = 13
RIGHT_KNEE = 14


def pair_confidence(confidences, left_id, right_id):
    """Return the average confidence of a left/right keypoint pair."""
    left_conf = float(confidences[left_id])
    right_conf = float(confidences[right_id])
    return (left_conf + right_conf) / 2.0


class YoloPoseRosTest(Node):
    def __init__(self):
        super().__init__("yolo_pose_ros_test")

        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"모델 파일이 없습니다: {MODEL_PATH}")

        self.device = 0 if torch.cuda.is_available() else "cpu"
        self.model = YOLO(str(MODEL_PATH))

        self.fps = 0.0
        self.previous_time = time.perf_counter()

        image_qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
        )

        self.image_sub = self.create_subscription(
            CompressedImage,
            INPUT_TOPIC,
            self.image_callback,
            image_qos,
        )

        self.annotated_pub = self.create_publisher(
            CompressedImage,
            OUTPUT_TOPIC,
            10,
        )

        device_name = "CUDA GPU" if self.device == 0 else "CPU"
        self.get_logger().info("YOLO Pose ROS 테스트 시작")
        self.get_logger().info(f"모델: {MODEL_PATH}")
        self.get_logger().info(f"입력 토픽: {INPUT_TOPIC}")
        self.get_logger().info(f"출력 토픽: {OUTPUT_TOPIC}")
        self.get_logger().info(f"실행 장치: {device_name}")
        self.get_logger().info("OpenCV 창 종료: Q 또는 ESC")

    def image_callback(self, msg):
        np_arr = np.frombuffer(msg.data, dtype=np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if frame is None:
            self.get_logger().warning("압축 이미지를 디코딩하지 못했습니다.")
            return

        result = self.model.predict(
            source=frame,
            imgsz=IMAGE_SIZE,
            conf=PERSON_CONFIDENCE,
            device=self.device,
            verbose=False,
        )[0]

        annotated_frame = result.plot(
            boxes=True,
            labels=True,
            conf=True,
        )

        person_count = 0

        if (
            result.boxes is not None
            and result.keypoints is not None
            and result.keypoints.conf is not None
        ):
            boxes = result.boxes.xyxy.cpu().numpy()
            keypoint_confidences = result.keypoints.conf.cpu().numpy()
            person_count = min(len(boxes), len(keypoint_confidences))

            for person_index, (box, confidences) in enumerate(
                zip(boxes, keypoint_confidences)
            ):
                x1, y1, _, _ = box.astype(int)

                shoulder_conf = pair_confidence(
                    confidences,
                    LEFT_SHOULDER,
                    RIGHT_SHOULDER,
                )
                hip_conf = pair_confidence(
                    confidences,
                    LEFT_HIP,
                    RIGHT_HIP,
                )
                knee_conf = pair_confidence(
                    confidences,
                    LEFT_KNEE,
                    RIGHT_KNEE,
                )

                text_lines = [
                    f"Person {person_index + 1}",
                    f"Shoulder: {shoulder_conf:.2f}",
                    f"Hip: {hip_conf:.2f}",
                    f"Knee: {knee_conf:.2f}",
                ]

                text_y = max(y1 - 80, 25)

                for line_index, text in enumerate(text_lines):
                    cv2.putText(
                        annotated_frame,
                        text,
                        (x1, text_y + line_index * 20),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        (0, 255, 255),
                        2,
                        cv2.LINE_AA,
                    )

        current_time = time.perf_counter()
        elapsed_time = current_time - self.previous_time
        self.previous_time = current_time

        current_fps = 1.0 / elapsed_time if elapsed_time > 0 else 0.0
        self.fps = self.fps * 0.9 + current_fps * 0.1

        cv2.putText(
            annotated_frame,
            f"FPS: {self.fps:.1f}",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            annotated_frame,
            f"Persons: {person_count}",
            (20, 60),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )

        cv2.imshow("YOLOv8 Pose ROS Test", annotated_frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord("q") or key == 27:
            self.get_logger().info("종료 키 입력")
            rclpy.shutdown()
            return

        ok, encoded = cv2.imencode(
            ".jpg",
            annotated_frame,
            [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY],
        )

        if ok:
            output_msg = CompressedImage()
            output_msg.header = msg.header
            output_msg.format = "jpeg"
            output_msg.data = encoded.tobytes()
            self.annotated_pub.publish(output_msg)

    def destroy_node(self):
        cv2.destroyAllWindows()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = YoloPoseRosTest()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            rclpy.shutdown()
        node.destroy_node()


if __name__ == "__main__":
    main()