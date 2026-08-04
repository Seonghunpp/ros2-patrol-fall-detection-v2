import cv2
import numpy as np
from ultralytics import YOLO

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
from sensor_msgs.msg import CompressedImage

class FallJudge:
    def __init__(
        self,
        lie_ratio=1.2, # 바운딩박스 가로/세로 비율이 이보다 크면 누움으로 판단
        body_ratio=1.0, # 몸통 관절(어깨↔엉덩이) 수평 여부 판단 기준 비율
        keypoint_conf=0.23, # 관절 신뢰도 기준 (낮으면 수평 판단에서 제외)
        threshold_count=10, # 연속 프레임 수평/누움 카운트가 이보다 크면 낙상으로 판단
    ):
        self.lie_ratio = lie_ratio
        self.body_ratio = body_ratio
        self.keypoint_conf = keypoint_conf
        self.threshold_count = threshold_count
        self.fall_count = 0

    def _is_body_horizontal(self, keypoints, keypoint_scores):
            if box_size == large:
                required = (5, 6, 11, 12)
            elif box_size == small:
                required = (5, 6, 13, 14)

class FallDetectionNode(Node):
    def __init__ (self):
        super().__init__('fall_detection')
        self.enabled = True

        self.model = YOLO("yolov8n-pose.pt")

        image_qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
        )

        self.image_sub = self.create_subscription(
            CompressedImage,
            "/image_raw/compressed",
            self.image_callback,
            image_qos,
        )
    def _decode_image(self, msg):
        np_arr = np.frombuffer(msg.data, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if frame is None:
            self.get_logger().warn("image decode failed")

        return frame

    def _run_yolo(self, frame):
        return self.model(frame, conf=0.3, imgsz=640, verbose=False)

    def _draw_person_box(self, frame, person, fall_detected):
        x1 = person["x1"]
        y1 = person["y1"]
        x2 = person["x2"]
        y2 = person["y2"]
        conf = person["conf"]

        if fall_detected:
            label = "FALL"
            color = (0, 0, 255)
        else:
            label = "PERSON"
            color = (0, 255, 0)

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(frame, f"{label} {conf:.2f}", (x1, max(y1 - 10, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, )

        

    def image_callback(self, msg):
        if not self.enabled:
            return

        frame = self._decode_image(msg)
        if frame is None:
            return

        results = self._run_yolo(frame)
        persons = self._extract_persons(results, frame.shape[0])

        status, fall_detected = self._judge_frame(persons)

        self._draw_persons(frame, persons, fall_detected)
        self._publish_status(status, fall_detected)
        self._publish_annotated_image(frame, msg.header)


def main(args=None):
    rclpy.init(args=args)
    node = FallDetectionNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

