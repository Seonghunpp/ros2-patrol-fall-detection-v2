import cv2
import numpy as np
from ultralytics import YOLO
from datetime import datetime
from pathlib import Path

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
from sensor_msgs.msg import CompressedImage
from std_srvs.srv import SetBool

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
            self.fall_count = min(
                self.fall_count + 1,
                self.threshold_count,
            )
        else:
            self.fall_count = 0 

        return self.fall_count
    
    def _check_fall_threshold(self): # 카운트가 기준에 도달했는지 확인
        return self.fall_count >= self.threshold_count

class FallDetectionNode(Node):
    def __init__(self):
        super().__init__("fall_detection_node")

        default_model = str(                    # 욜로 모델 경로
            Path(get_package_share_directory("my_patrol"))
            / "model"
            / "patient_pose_v2.pt"
        )
        self.declare_parameter("model_path", default_model)

        model_path = (                          # 욜로 모델 경로 가져오기
            self.get_parameter("model_path")
            .get_parameter_value()
            .string_value
        )

        self.model = YOLO(model_path)
        self.judge = FallJudge()
        self.person_states = {}
        self.frame_index = 0
        self.max_missed_frames = 30
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

        self.annotated_image_pub = self.create_publisher(
            CompressedImage,
            "/image_annotated/compressed",
            10,
        )

        self.enabled = True
        self.create_service(
            SetBool,
            "fall_enable",
            self.enable_callback,
        )

    def _decode_image(self, compressed_image_msg): # 압축 이미지를 OpenCV 이미지로 변환
        np_arr = np.frombuffer(compressed_image_msg.data, np.uint8)
        image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if image is None:
            self.get_logger().warning("image decode failed")
            return None

        return image

    def _run_pose_model(self, image): # YOLO Pose 실행
        result = self.model.track(
            image,
            persist=True,
            imgsz=640,
            conf=0.25,
            iou=0.20,
            verbose=False,
        )[0]

        return result

    def _extract_persons(self, result): # 사람별 박스와 해당 관절 묶기
        persons = []

        boxes = result.boxes
        keypoints = result.keypoints

        self.get_logger().info(
        f"boxes={len(boxes)}, "
        f"ids={boxes.id}, "
        f"keypoints={keypoints.xy is not None}"
)

        if boxes is None or keypoints is None:
            return persons

        if boxes.id is None or keypoints.xy is None or keypoints.conf is None:
            return persons

        boxes_xyxy = boxes.xyxy.cpu().numpy()
        boxes_conf = boxes.conf.cpu().numpy()
        track_ids = boxes.id.int().cpu().tolist()
        keypoints_xy = keypoints.xy.cpu().numpy()
        keypoints_conf = keypoints.conf.cpu().numpy()

        for index in range(len(boxes_xyxy)):
            x1, y1, x2, y2 = map(int, boxes_xyxy[index])

            person = {
                "track_id": track_ids[index],
                "box": (x1, y1, x2, y2),
                "confidence": float(boxes_conf[index]),
                "keypoints": keypoints_xy[index],
                "keypoint_scores": keypoints_conf[index],
            }

            persons.append(person)

        return persons

    def _save_fall_image(self, image): # 낙상 확정 순간 이미지 저장
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"fall_{timestamp}.jpg"
        filepath = self.fall_image_dir / filename

        saved = cv2.imwrite(str(filepath), image)

        if not saved:
            self.get_logger().error(
                f"fall image save failed: {filepath}"
            )

        return saved
    
    def _draw_person_box(self, image, person): #사람 한 명의 박스와 상태 표시
        x1, y1, x2, y2 = person["box"]
        confidence = person["confidence"]
        track_id = person["track_id"]
        fall_count = person["fall_count"]
        fall_detected = person["fall_detected"]

        if fall_detected:
            label = "FALL DETECTED"
            color = (40, 40, 230)
        else:
            label = "PERSON"
            color = (60, 200, 80)

        corner_length = 25
        thickness = 3

        # 왼쪽 위
        cv2.line(image, (x1, y1), (x1 + corner_length, y1), color, thickness)
        cv2.line(image, (x1, y1), (x1, y1 + corner_length), color, thickness)

        # 오른쪽 위
        cv2.line(image, (x2, y1), (x2 - corner_length, y1), color, thickness)
        cv2.line(image, (x2, y1), (x2, y1 + corner_length), color, thickness)

        # 왼쪽 아래
        cv2.line(image, (x1, y2), (x1 + corner_length, y2), color, thickness)
        cv2.line(image, (x1, y2), (x1, y2 - corner_length), color, thickness)

        # 오른쪽 아래
        cv2.line(image, (x2, y2), (x2 - corner_length, y2), color, thickness)
        cv2.line(image, (x2, y2), (x2, y2 - corner_length), color, thickness)

        text = (
            f"{label} ID:{track_id}  "
            f"{confidence:.0%}  "
            f"[{fall_count}/{self.judge.threshold_count}]"
        )

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.55
        text_thickness = 1

        (text_width, text_height), baseline = cv2.getTextSize(
            text,
            font,
            font_scale,
            text_thickness,
        )

        label_top = max(y1 - text_height - 18, 0)
        label_bottom = label_top + text_height + 16

        # 글자 뒤 배경
        cv2.rectangle(
            image,
            (x1, label_top),
            (x1 + text_width + 16, label_bottom),
            color,
            -1,
        )

        cv2.putText(
            image,
            text,
            (x1 + 8, label_bottom - 8),
            font,
            font_scale,
            (255, 255, 255),
            text_thickness,
            cv2.LINE_AA,
        )

    def _draw_results(self, image, persons): # 검출된 모든 사람의 결과 표시
        for person in persons:
            self._draw_person_box(
                image,
                person,
            )

        return image

    def _publish_results(self, original_msg, image): # 상태와 결과 영상 ROS 발행
        encode_success, encoded_image = cv2.imencode(
            ".jpg",
            image,
            [cv2.IMWRITE_JPEG_QUALITY, 90],
        )

        if not encode_success:
            self.get_logger().error("image encode failed")
            return

        annotated_msg = CompressedImage()
        annotated_msg.header = original_msg.header
        annotated_msg.format = "jpeg"
        annotated_msg.data = encoded_image.tobytes()

        self.annotated_image_pub.publish(annotated_msg)

    def enable_callback(self, request, response): # 낙상 감지 켜기·끄기 서비스 처리
        self.enabled = request.data
        if self.enabled:
            self.person_states.clear()
        state = "ON" if self.enabled else "OFF"
        self.get_logger().info(f"fall detection {state}")
        response.success = True
        response.message = f"fall detection {state}"
        return response

    def image_callback(self, compressed_image_msg): # 위 함수들을 실제 처리 순서대로 호출
        
        #입력 및 사람 추출
        if not self.enabled:
            return

        image = self._decode_image(compressed_image_msg)

        if image is None:
            return

        result = self._run_pose_model(image)
        image = result.plot(boxes=False, labels=False)
        persons = self._extract_persons(result)
        self.frame_index += 1

        # 낙상 판정 
        new_fall_ids = []

        for person in persons:
            track_id = person["track_id"]

            if track_id not in self.person_states:
                self.person_states[track_id] = {
                    "judge": FallJudge(
                        body_ratio=self.judge.body_ratio,
                        keypoint_conf=self.judge.keypoint_conf,
                        threshold_count=self.judge.threshold_count,
                    ),
                    "fall_event_active": False,
                    "last_seen_frame": self.frame_index,
                }

            state = self.person_states[track_id]
            person_judge = state["judge"]
            state["last_seen_frame"] = self.frame_index

            horizontal_result = person_judge._check_body_horizontal(
                person["keypoints"],
                person["keypoint_scores"],
            )

            # 관절을 판단할 수 없는 None이면 기존 카운트를 유지한다.
            if horizontal_result is not None:
                person_judge._update_fall_count(horizontal_result)

            fall_detected = person_judge._check_fall_threshold()
            person["fall_count"] = person_judge.fall_count
            person["fall_detected"] = fall_detected

            if fall_detected and not state["fall_event_active"]:
                new_fall_ids.append(track_id)
                state["fall_event_active"] = True
            elif not fall_detected:
                state["fall_event_active"] = False

        expired_ids = [
            track_id
            for track_id, state in self.person_states.items()
            if self.frame_index - state["last_seen_frame"] > self.max_missed_frames
        ]
        for track_id in expired_ids:
            del self.person_states[track_id]

        # 그리기, 저장, 발행
        image = self._draw_results(
            image,
            persons,
        )

        # 같은 프레임에서 여러 명이 확정돼도 전체 이미지는 한 장만 저장한다.
        if new_fall_ids:
            self._save_fall_image(image)

        self._publish_results(
            compressed_image_msg,
            image,
        )
            
def main(args=None):
    rclpy.init(args=args)
    node = FallDetectionNode()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()

if __name__ == "__main__": # python 파일을 직접 실행할 때만 main() 호출
    main()
