import cv2
import numpy as np
from ultralytics import YOLO
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
from sensor_msgs.msg import CompressedImage

LEFT_SHOULDER = 5
RIGHT_SHOULDER = 6
LEFT_ELBOW = 7
RIGHT_ELBOW = 8
LEFT_HIP = 11
RIGHT_HIP = 12
LEFT_KNEE = 13
RIGHT_KNEE = 14

class FallJudge:
    def __init__(
        self,
        body_ratio=1.0,          # 몸통 수평 판정 기준
        keypoint_conf=0.25,      # 관절을 사용할 수 있는 최소 신뢰도
        threshold_count=10,      # 낙상으로 확정할 연속 프레임 수
    ):
        self.body_ratio = body_ratio
        self.keypoint_conf = keypoint_conf
        self.threshold_count = threshold_count
        self.fall_count = 0



    def _is_keypoint_valid(self, keypoint_scores, index): #keypoint_scores 한 사람의 17개 관절 신뢰도 / index 확인할 관절 번호
        if keypoint_scores is None:
            return False

        if index >= len(keypoint_scores):
            return False

        return float(keypoint_scores[index]) >= self.keypoint_conf

    def _get_joint_center(self, keypoints, left_index, right_index): # 좌우 관절의 가운데 좌표 계산
        left_x, left_y = keypoints[left_index]
        right_x, right_y = keypoints[right_index]

        center_x = (left_x + right_x) / 2
        center_y = (left_y + right_y) / 2

        return center_x, center_y

    def _is_horizontal(self, point_a, point_b): # 두 점이 수평인지 확인
        x1, y1 = point_a
        x2, y2 = point_b

        x_distance = abs(x2 - x1)
        y_distance = abs(y2 - y1)

        return x_distance > y_distance * self.body_ratio

    def _check_primary_joints(self, keypoints, keypoint_scores): # 어깨·골반으로 기본 수평 판정
        required = (
            LEFT_SHOULDER,
            RIGHT_SHOULDER,
            LEFT_HIP,
            RIGHT_HIP,
        )

        for index in required:
            if not self._is_keypoint_valid(keypoint_scores, index):
                return None

        shoulder_center = self._get_joint_center(
            keypoints,
            LEFT_SHOULDER,
            RIGHT_SHOULDER,
        )

        hip_center = self._get_joint_center(
            keypoints,
            LEFT_HIP,
            RIGHT_HIP,
        )

        return self._is_horizontal(
            shoulder_center,
            hip_center,
        )

    def _check_fallback_joints(self, keypoints, keypoint_scores): #기본 관절이 가리면 보조 관절로 판정
        if (
            self._is_keypoint_valid(keypoint_scores, LEFT_SHOULDER)
            and self._is_keypoint_valid(keypoint_scores, LEFT_HIP)
        ):
            return self._is_horizontal(
                keypoints[LEFT_SHOULDER],
                keypoints[LEFT_HIP],
            )

        if (
            self._is_keypoint_valid(keypoint_scores, RIGHT_SHOULDER)
            and self._is_keypoint_valid(keypoint_scores, RIGHT_HIP)
        ):
            return self._is_horizontal(
                keypoints[RIGHT_SHOULDER],
                keypoints[RIGHT_HIP],
            )

        required = (
            LEFT_ELBOW,
            RIGHT_ELBOW,
            LEFT_KNEE,
            RIGHT_KNEE,
        )

        for index in required:
            if not self._is_keypoint_valid(keypoint_scores, index):
                return None

        elbow_center = self._get_joint_center(
            keypoints,
            LEFT_ELBOW,
            RIGHT_ELBOW,
        )

        knee_center = self._get_joint_center(
            keypoints,
            LEFT_KNEE,
            RIGHT_KNEE,
        )

        return self._is_horizontal(
            elbow_center,
            knee_center,
        )
    
    def _check_body_horizontal(self, keypoints, keypoint_scores): # 기본 판정 후 필요할 때 보조 판정 실행
        primary_result = self._check_primary_joints(
            keypoints,
            keypoint_scores,
        )

        if primary_result is not None:
            return primary_result

        return self._check_fallback_joints(
            keypoints,
            keypoint_scores,
        )
    
    def _update_fall_count(self, horizontal_result): # 수평이면 증가, 벗어나면 초기화
        if horizontal_result:
            self.fall_count += 1
        else:
            self.fall_count = 0 

        return self.fall_count
    
    def _check_fall_threshold(self): # 카운트가 기준에 도달했는지 확인
        return self.fall_count >= self.threshold_count
    

class FallDetectionNode(Node):
    def __init__(self):
        super().__init__("fall_detection_node")

        default_model = str(                    # 욜로 모델 경로
             Path(__file__).resolve().parent 
            / "runs"
            / "pose"
            / "printed_patient_v2" 
            / "weights" 
            / "best.pt" 
        )
        self.declare_parameter("model_path", default_model)

        model_path = (                          # 욜로 모델 경로 가져오기
            self.get_parameter("model_path")
            .get_parameter_value()
            .string_value
        )

        self.model = YOLO(model_path)
        self.judge = FallJudge()

        self.fall_image_dir = Path.home() / "fall_images"
        self.fall_image_dir.mkdir(parents=True, exist_ok=True)


        image_qos = QoSProfile(                 # 이미지 구독 QoS 설정
            depth=1,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
        )

        self.image_sub = self.create_subscription(# 이미지 구독
            CompressedImage,
            "/image_raw/compressed",
            self.image_callback,
            image_qos,
        )

        self.annotated_image_pub = self.create_publisher(# 이미지 발행
            CompressedImage,
            "/image_annotated/compressed",
            10,
        )

    def _decode_image(self, compressed_image_msg):
        np_arr = np.frombuffer(compressed_image_msg.data, np.uint8)
        image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if image is None:
            self.get_logger().warning("image decode failed")
            return None

        return image

    def _run_pose_model(self, image):   # YOLO Pose 실행
        result = self.model.predict(
            image,
            imgsz=640,
            conf=0.25,
            iou=0.20,
            verbose=False,
        )[0]

        return result

    def _extract_persons(self, result):  # 사람별 박스와 해당 관절 묶기
        boxes = getattr(result, "boxes", None)
        keypoints = getattr(result, "keypoints", None)

        return boxes, keypoints

    def _save_fall_image(self, image): # 낙상감지시 이미지 저장
        timestamp = self.get_clock().now().seconds
        image_path = self.fall_image_dir / f"fall_{timestamp}.jpg"

        saved = cv2.imwrite(str(image_path), image)

        if not saved:
            self.get_logger().error("fall image save failed")
            return None

        self.get_logger().info(f"fall image saved: {image_path}")
        return image_path

    def _draw_person_box(self, image, person, fall_detected):
        x1, y1, x2, y2 = person["box"]
        confidence = person["confidence"]

        if fall_detected:
            label = "FALL"
            color = (0, 0, 255)
        else:
            label = "PERSON"
            color = (0, 255, 0)

        cv2.rectangle(
            image,
            (x1, y1),
            (x2, y2),
            color,
            2,
        )

        text = (
            f"{label} "
            f"conf:{confidence:.2f} "
            f"cnt:{self.judge.fall_count}"
        )

        cv2.putText(
            image,
            text,
            (x1, max(y1 - 10, 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
        )

def main(args=None):
    rclpy.init(args=args)
    node = FallDetectionNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()