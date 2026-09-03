import cv2
import numpy as np
from ultralytics import YOLO
from datetime import datetime
from pathlib import Path
import time

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool
from std_srvs.srv import SetBool

LEFT_SHOULDER = 5
RIGHT_SHOULDER = 6
LEFT_ELBOW = 7
RIGHT_ELBOW = 8
LEFT_HIP = 11
RIGHT_HIP = 12
LEFT_KNEE = 13
RIGHT_KNEE = 14


# YOLO Pose 관절 좌표로 몸통이 수평에 가까운지 판정한다.
class FallJudge:
    def __init__(
        self,
        body_ratio=1.0,          # 가로 거리가 세로 거리의 몇 배여야 하는지
        keypoint_conf=0.25,      # 판정에 사용할 관절의 최소 신뢰도
    ):
        self.body_ratio = body_ratio
        self.keypoint_conf = keypoint_conf

    # 지정한 관절이 존재하고 최소 신뢰도를 만족하는지 확인한다.
    def _is_keypoint_valid(self, keypoint_scores, index):
        if keypoint_scores is None:
            return False

        if index >= len(keypoint_scores):
            return False

        return float(keypoint_scores[index]) >= self.keypoint_conf

    # 좌우 관절의 중간점을 계산한다.
    def _get_joint_center(self, keypoints, left_index, right_index):
        left_x, left_y = keypoints[left_index]
        right_x, right_y = keypoints[right_index]

        center_x = (left_x + right_x) / 2
        center_y = (left_y + right_y) / 2

        return center_x, center_y

    # 두 점의 가로 거리가 세로 거리보다 충분히 큰지 확인한다.
    def _is_horizontal(self, point_a, point_b):
        x1, y1 = point_a
        x2, y2 = point_b

        x_distance = abs(x2 - x1)
        y_distance = abs(y2 - y1)

        return bool(x_distance > y_distance * self.body_ratio)

    # 양쪽 어깨 중앙과 양쪽 골반 중앙을 이용하는 기본 판정이다.
    def _check_primary_joints(self, keypoints, keypoint_scores):
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

    # 기본 관절이 가려졌을 때 한쪽 몸통 또는 팔꿈치·무릎을 이용한다.
    def _check_fallback_joints(self, keypoints, keypoint_scores):
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

    # 기본 판정에 필요한 관절이 부족할 때만 보조 판정을 실행한다.
    def _check_body_horizontal(self, keypoints, keypoint_scores):
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

class FallDetectionNode(Node):
    # 박스가 프레임 경계와 맞닿은 경우를 제외하기 위한 픽셀 여백이다.
    FRAME_MARGIN = 10

    def __init__(self):
        super().__init__("fall_detection_node")

        # 설치된 my_patrol 패키지의 기본 모델을 사용한다.
        default_model = str(
            Path(get_package_share_directory("my_patrol"))
            / "model"
            / "patient_pose_v2.pt"
        )
        self.declare_parameter("model_path", default_model)

        # ROS 파라미터로 다른 모델 경로를 지정할 수 있다.
        model_path = (
            self.get_parameter("model_path")
            .get_parameter_value()
            .string_value
        )

        self.model = YOLO(model_path)
        self.threshold_count = 10
        self.judge = FallJudge()

        # Track ID별 카운트·확정·알림 상태를 저장한다.
        self.person_states = {}
        self.frame_index = 0
        # 이 프레임 수만큼 보이지 않은 Track ID는 화면에서 사라진 것으로 본다.
        self.max_missed_frames = 30
        # 미확정 낙상 후보가 사라진 뒤 정지 신호를 유지하는 시간(초)이다.
        self.fall_clear_wait = 5.0
        # /fall_detected에 발행하는 즉시 정지 상태다.
        self.fall_latched = False
        self.fall_clear_since = None
        # 한 번의 병실 감지 세션에서 /fall_confirmed를 한 번만 확정한다.
        self.confirmed_latched = False

        # 확정 순간의 원본 카메라 프레임을 저장한다.
        self.fall_image_dir = Path.home() / "fall_images"
        self.fall_image_dir.mkdir(parents=True, exist_ok=True)

        # 카메라 지연을 줄이기 위해 최신 프레임 하나만 Best Effort로 받는다.
        image_qos = QoSProfile(
            depth=1,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
        )

        self.image_sub = self.create_subscription(
            CompressedImage,
            "/image_raw/compressed",
            self.image_callback,
            image_qos,
        )

        self.annotated_image_pub = self.create_publisher(
            CompressedImage,
            "/image_annotated/compressed",
            1,
        )
        # 로봇 정지용: 완전히 보이는 fall_person이 한 프레임만 있어도 True다.
        self.fall_detected_pub = self.create_publisher(
            Bool,
            "/fall_detected",
            10,
        )

        # 기록용: 사람별 누적 기준과 관절 검증을 모두 통과해야 True다.
        # 한 병실 감지 세션 동안 값을 유지해 대시보드의 중복 기록을 막는다.
        self.fall_confirmed_pub = self.create_publisher(
            Bool,
            "/fall_confirmed",
            10,
        )
        self.enabled = False
        self.create_service(
            SetBool,
            "/fall_enable",
            self.enable_callback,
        )

    # ROS 압축 이미지를 OpenCV BGR 이미지로 변환한다.
    def _decode_image(self, compressed_image_msg):
        np_arr = np.frombuffer(compressed_image_msg.data, np.uint8)
        image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if image is None:
            self.get_logger().warning("image decode failed")
            return None

        return image

    # 이전 프레임의 Track ID를 유지하면서 YOLO Pose 추론을 실행한다.
    def _run_pose_model(self, image):
        result = self.model.track(
            image,
            persist=True,
            imgsz=640,
            conf=0.25,
            iou=0.20,
            verbose=False,
        )[0]

        return result

    def _is_box_fully_visible(self, box, image_shape):
        """바운딩박스 네 변이 여백을 포함한 프레임 안에 있는지 확인한다."""
        x1, y1, x2, y2 = map(int, box)
        image_height, image_width = image_shape[:2]
        return (
            x1 >= self.FRAME_MARGIN
            and y1 >= self.FRAME_MARGIN
            and x2 <= image_width - self.FRAME_MARGIN
            and y2 <= image_height - self.FRAME_MARGIN
        )

    def _has_visible_fall_candidate(self, result, image_shape):
        """Track ID 없이도 즉시 정지할 낙상 후보가 있는지 확인한다."""
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return False

        boxes_xyxy = boxes.xyxy.cpu().numpy()
        boxes_cls = boxes.cls.int().cpu().tolist()

        for index, box in enumerate(boxes_xyxy):
            class_name = result.names[boxes_cls[index]]
            if (class_name == "fall_person"
                    and self._is_box_fully_visible(box, image_shape)):
                return True

        return False

    # 추론 결과에서 Track ID가 있는 사람의 박스·관절 정보를 추출한다.
    def _extract_persons(self, result, image_shape):
        persons = []

        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return persons

        if boxes.id is None:
            return persons

        boxes_xyxy = boxes.xyxy.cpu().numpy()
        boxes_conf = boxes.conf.cpu().numpy()
        boxes_cls = boxes.cls.int().cpu().tolist()
        track_ids = boxes.id.int().cpu().tolist()
        keypoints_xy = None
        keypoints_conf = None
        if (result.keypoints is not None
                and result.keypoints.xy is not None
                and result.keypoints.conf is not None):
            keypoints_xy = result.keypoints.xy.cpu().numpy()
            keypoints_conf = result.keypoints.conf.cpu().numpy()

        for index in range(len(boxes_xyxy)):
            x1, y1, x2, y2 = map(int, boxes_xyxy[index])
            class_id = boxes_cls[index]
            class_name = result.names[class_id]
            box_fully_visible = self._is_box_fully_visible(
                boxes_xyxy[index], image_shape)

            person = {
                "track_id": track_ids[index],
                "box": (x1, y1, x2, y2),
                "confidence": float(boxes_conf[index]),
                "class_name": class_name,
                "box_fully_visible": box_fully_visible,
                "keypoints": (
                    keypoints_xy[index] if keypoints_xy is not None else None
                ),
                "keypoint_scores": (
                    keypoints_conf[index]
                    if keypoints_conf is not None else None
                ),
            }

            persons.append(person)

        return persons

    # 낙상 확정 순간의 원본 카메라 프레임을 저장한다.
    def _save_fall_image(self, image):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"fall_{timestamp}.jpg"
        filepath = self.fall_image_dir / filename

        saved = cv2.imwrite(str(filepath), image)

        if not saved:
            self.get_logger().error(
                f"fall image save failed: {filepath}"
            )

        return saved
    
    # 사람 한 명의 현재 판정 상태를 박스와 문자열로 표시한다.
    def _draw_person_box(self, image, person):
        x1, y1, x2, y2 = person["box"]
        confidence = person["confidence"]
        class_name = person["class_name"]
        track_id = person["track_id"]
        fall_count = person["fall_count"]
        confirmed = person["confirmed"]
        box_fully_visible = person["box_fully_visible"]
        pose_result = person["pose_result"]

        if confirmed:
            label = "FALL DETECTED"
            color = (40, 40, 230)
        elif class_name == "fall_person" and not box_fully_visible:
            label = "WAIT FULL BODY"
            color = (0, 180, 255)
        elif class_name == "fall_person":
            label = "FALL CANDIDATE"
            color = (0, 180, 255)
        else:
            label = "PERSON"
            color = (60, 200, 80)

        box_width = max(x2 - x1, 1)
        box_height = max(y2 - y1, 1)
        short_side = min(box_width, box_height)

        corner_length = int(np.clip(short_side * 0.2, 10, 40))
        thickness = int(np.clip(short_side / 80, 2, 5))

        # 전체 사각형 대신 네 모서리만 그려 사람과 관절이 잘 보이게 한다.
        cv2.line(image, (x1, y1), (x1 + corner_length, y1), color, thickness)
        cv2.line(image, (x1, y1), (x1, y1 + corner_length), color, thickness)

        cv2.line(image, (x2, y1), (x2 - corner_length, y1), color, thickness)
        cv2.line(image, (x2, y1), (x2, y1 + corner_length), color, thickness)

        cv2.line(image, (x1, y2), (x1 + corner_length, y2), color, thickness)
        cv2.line(image, (x1, y2), (x1, y2 - corner_length), color, thickness)

        cv2.line(image, (x2, y2), (x2 - corner_length, y2), color, thickness)
        cv2.line(image, (x2, y2), (x2, y2 - corner_length), color, thickness)

        pose_text = ""
        if confirmed:
            pose_text = " CONFIRMED"
        elif fall_count >= self.threshold_count:
            if pose_result is True:
                pose_text = " POSE:FALL"
            elif pose_result is False:
                pose_text = " POSE:NORMAL"
            else:
                pose_text = " POSE:UNKNOWN"

        text = (
            f"{label} ID:{track_id}  "
            f"{class_name} {confidence:.0%}  "
            f"[{fall_count}/{self.threshold_count}]"
            f"{pose_text}"
        )

        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.55
        text_thickness = 1

        (text_width, text_height), _ = cv2.getTextSize(
            text,
            font,
            font_scale,
            text_thickness,
        )

        label_top = max(y1 - text_height - 18, 0)
        label_bottom = label_top + text_height + 16

        # 밝은 영상에서도 읽을 수 있도록 상태 문자열 뒤에 배경색을 넣는다.
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

    # 추적 중인 모든 사람의 판정 결과를 분석 화면에 표시한다.
    def _draw_results(self, image, persons):
        for person in persons:
            self._draw_person_box(
                image,
                person,
            )

        return image

    # 분석 화면을 JPEG로 압축하고 원본 메시지의 헤더를 유지해 발행한다.
    def _publish_results(self, original_msg, image):
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

    # 감지 시작·종료 시 이전 병실에서 사용한 모든 판정 상태를 초기화한다.
    def enable_callback(self, request, response):
        self.enabled = request.data
        self.person_states.clear()
        self.fall_latched = False
        self.fall_clear_since = None
        self.confirmed_latched = False
        self.fall_detected_pub.publish(Bool(data=False))
        self.fall_confirmed_pub.publish(Bool(data=False))
        state = "ON" if self.enabled else "OFF"
        self.get_logger().info(f"fall detection {state}")
        response.success = True
        response.message = f"fall detection {state}"
        return response

    # 프레임 입력부터 판정·저장·토픽 발행까지의 전체 처리 순서다.
    def image_callback(self, compressed_image_msg):

        if not self.enabled:
            return

        image = self._decode_image(compressed_image_msg)

        if image is None:
            return

        result = self._run_pose_model(image)
        annotated_image = result.plot(boxes=False, labels=False)
        persons = self._extract_persons(result, image.shape)
        self.frame_index += 1

        # Track ID별 누적값과 확정 상태를 갱신한다.
        new_fall_ids = []

        for person in persons:
            track_id = person["track_id"]

            if track_id not in self.person_states:
                self.person_states[track_id] = {
                    "fall_count": 0,
                    "recovery_count": 0,
                    "confirmed": False,
                    "alert_sent": False,
                    "last_seen_frame": self.frame_index,
                }

            state = self.person_states[track_id]
            state["last_seen_frame"] = self.frame_index

            pose_result = None

            # 확정된 Track ID는 상태가 만료될 때까지 클래스와 자세를 재판정하지 않는다.
            if not state["confirmed"]:
                # 박스 전체가 보일 때만 카운트를 갱신한다.
                # 프레임 경계에 걸린 경우에는 기존 카운트를 유지한다.
                if person["box_fully_visible"]:
                    if person["class_name"] == "fall_person":
                        state["fall_count"] = min(
                            state["fall_count"] + 1,
                            self.threshold_count,
                        )
                    else:
                        state["fall_count"] = 0

                # 동일 ID가 기준 횟수에 도달한 뒤 관절 좌표로 최종 검증한다.
                if (person["box_fully_visible"]
                        and person["class_name"] == "fall_person"
                        and state["fall_count"] >= self.threshold_count):
                    pose_result = self.judge._check_body_horizontal(
                        person["keypoints"],
                        person["keypoint_scores"],
                    )
                    if pose_result is True:
                        state["confirmed"] = True
                        state["fall_count"] = self.threshold_count

            # 확정 후에는 전체 박스가 보이는 person 판정만 회복으로 누적한다.
            # fall_person이 다시 나오거나 10회에 도달하지 못하면 확정 상태를 유지한다.
            else:
                if person["box_fully_visible"]:
                    if person["class_name"] == "person":
                        state["recovery_count"] = min(
                            state["recovery_count"] + 1,
                            self.threshold_count,
                        )
                    else:
                        state["recovery_count"] = 0

                if state["recovery_count"] >= self.threshold_count:
                    state["confirmed"] = False
                    state["fall_count"] = 0
                    state["recovery_count"] = 0

            # Track ID별 알림은 한 번만 처리하고, 병실별 기록도 한 번만 생성한다.
            if state["confirmed"] and not state["alert_sent"]:
                state["alert_sent"] = True
                if not self.confirmed_latched:
                    new_fall_ids.append(track_id)
                    self.confirmed_latched = True

            person["fall_count"] = state["fall_count"]
            person["confirmed"] = state["confirmed"]
            person["pose_result"] = pose_result

        # 오래 보이지 않은 Track ID와 그 ID의 확정 상태를 함께 제거한다.
        # 이후 다시 나타나 새 ID를 받으면 새로운 사람으로 판정한다.
        expired_ids = [
            track_id
            for track_id, state in self.person_states.items()
            if self.frame_index - state["last_seen_frame"] > self.max_missed_frames
        ]
        for track_id in expired_ids:
            del self.person_states[track_id]

        # 관절과 사람별 판정 결과를 분석 화면에 그린다.
        annotated_image = self._draw_results(
            annotated_image,
            persons,
        )

        # 병실에서 처음 확정된 순간의 원본 프레임만 한 장 저장한다.
        if new_fall_ids:
            self._save_fall_image(image)

        # 미확정 후보는 한 프레임만 보여도 즉시 정지한다.
        # 확정된 Track ID는 상태가 만료될 때까지 정지를 유지한다.
        # 둘 다 사라진 뒤에는 fall_clear_wait 동안 기다렸다가 정지를 해제한다.
        current_fall = (
            any(state["confirmed"] for state in self.person_states.values())
            or self._has_visible_fall_candidate(result, image.shape)
        )

        # 확정 신호는 감지를 끌 때까지 유지해 같은 병실의 중복 기록을 막는다.
        self.fall_confirmed_pub.publish(Bool(data=self.confirmed_latched))

        if current_fall:
            self.fall_latched = True
            self.fall_clear_since = None
        elif self.fall_latched:
            if self.fall_clear_since is None:
                self.fall_clear_since = time.monotonic()
            elif time.monotonic() - self.fall_clear_since >= self.fall_clear_wait:
                self.fall_latched = False
                self.fall_clear_since = None

        self.fall_detected_pub.publish(Bool(data=self.fall_latched))

        self._publish_results(
            compressed_image_msg,
            annotated_image,
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

# 이 파일을 직접 실행할 때만 ROS 노드를 시작한다.
if __name__ == "__main__":
    main()
