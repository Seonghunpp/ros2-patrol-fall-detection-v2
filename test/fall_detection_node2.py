import cv2
import logging
import numpy as np
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy

from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String, Bool
from std_srvs.srv import SetBool

from ultralytics import YOLO


logging.getLogger("ultralytics").setLevel(logging.ERROR)


class FallJudge:
    def __init__(
        self,
        lie_ratio=1.2, # 바운딩박스 가로/세로 비율이 이보다 크면 누움으로 판단
        torso_ratio=1.0, # 몸통 관절(어깨↔엉덩이) 수평 여부 판단 기준 비율
        keypoint_conf=0.23, # 관절 신뢰도 기준 (낮으면 수평 판단에서 제외)
        threshold_count=10, # 연속 프레임 수평/누움 카운트가 이보다 크면 낙상으로 판단
    ):
        self.lie_ratio = lie_ratio
        self.torso_ratio = torso_ratio
        self.keypoint_conf = keypoint_conf
        self.threshold_count = threshold_count
        self.fall_count = 0

    def _check_torso_horizontal(self, keypoints, keypoint_scores):
        required = (5, 6, 11, 12) #관절 번호

        if keypoints is None or keypoint_scores is None:
            return False

        if any(keypoint_scores[index] < self.keypoint_conf for index in required):
            return False

        shoulder_center = (keypoints[5] + keypoints[6]) / 2
        hip_center = (keypoints[11] + keypoints[12]) / 2

        dx, dy = np.abs(hip_center - shoulder_center)

        return dx > dy * self.torso_ratio

    def check(self, x1, y1, x2, y2, keypoints, keypoint_scores):
        person_w = x2 - x1
        person_h = y2 - y1
        bbox_horizontal = person_w > person_h * self.lie_ratio
        torso_horizontal = self._is_torso_horizontal(keypoints, keypoint_scores)

        lying_pose = bbox_horizontal or torso_horizontal

        return lying_pose, bbox_horizontal, torso_horizontal


class FallDetectionNode(Node):
    def __init__(self):
        super().__init__("fall_detection_node")

        default_model = str(
            Path(__file__).resolve().parent / "yolov8n-pose.pt"
        )

        self.declare_parameter("model_path", default_model)

        model_path = (
            self.get_parameter("model_path")
            .get_parameter_value()
            .string_value
        )

        self.model = YOLO(model_path)
        
        self.judge = FallJudge()

        # 감지선: 화면 높이 * 비율 아래쪽(발 기준)만 감지
        # 0.67 = 화면 위에서 2/3 지점 → 위 2/3는 감지 안 함, 아래 1/3만 감지
        self.declare_parameter("detect_line_ratio", 0.67)
        self.detect_line_ratio = (
            self.get_parameter("detect_line_ratio").get_parameter_value().double_value
        )

        self.enabled = True

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
        self.create_service(SetBool, "fall_enable", self.enable_callback)

        self.status_pub = self.create_publisher(String, "/fall_status", 10)
        self.fall_pub = self.create_publisher(Bool, "/fall_detected", 10)
        self.annotated_image_pub = self.create_publisher(
            CompressedImage, "/image_annotated/compressed", 10
        )

        self.get_logger().info("fall_detection_node started")
        self.get_logger().info(f"model path: {model_path}")

    def enable_callback(self, request, response):
        self.enabled = request.data
        if self.enabled:
            self.judge.fall_count = 0
        state = "ON" if self.enabled else "OFF"
        self.get_logger().info(f"fall detection {state}")
        response.success = True
        response.message = f"fall detection {state}"
        return response

    def image_callback(self, msg):
        if not self.enabled:
            return

        np_arr = np.frombuffer(msg.data, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if frame is None:
            self.get_logger().warn("image decode failed")
            return

        # 감지선 y좌표 (발=박스 하단 y2가 이 선 아래인 사람만 감지)
        line_y = int(frame.shape[0] * self.detect_line_ratio)

        results = self.model(frame, conf=0.23, imgsz=640, verbose=False)

        person_detected = False
        frame_has_lying_pose = False
        final_status = "NO_PERSON"
        final_fall_detected = False
        detected_persons = []

        for result in results:
            frame = result.plot(boxes=False, labels=False)

            boxes = getattr(result, "boxes", None)
            keypoints = getattr(result, "keypoints", None)

            if boxes is None or keypoints is None:
                continue
            if len(boxes) == 0:
                continue
            if getattr(keypoints, "xy", None) is None or len(keypoints.xy) == 0:
                continue

            keypoints_xy = (
                keypoints.xy.cpu().numpy()
                if hasattr(keypoints.xy, "cpu")
                else np.array(keypoints.xy)
            )
            keypoints_conf = None
            if getattr(keypoints, "conf", None) is not None:
                keypoints_conf = (
                    keypoints.conf.cpu().numpy()
                    if hasattr(keypoints.conf, "cpu")
                    else np.array(keypoints.conf)
                )

            for index, box in enumerate(boxes):
                try:
                    conf = float(box.conf[0])
                except Exception:
                    conf = 1.0

                if conf < 0.23:
                    continue

                xyxy = (
                    box.xyxy[0].cpu().numpy()
                    if hasattr(box.xyxy, "cpu")
                    else box.xyxy[0]
                )
                x1, y1, x2, y2 = map(int, xyxy)

                # 발(박스 하단 y2)이 감지선 위쪽이면 감지 x
                if y2 < line_y:
                    continue

                person_detected = True

                # keypoint 존재 여부로 유효한 감지 판단 (Pose 모델은 사람만 탐지)
                if keypoints_conf is None:
                    continue

                if index >= len(keypoints_xy) or index >= len(keypoints_conf):
                    continue

                lying_pose, bbox_horizontal, torso_horizontal = self.judge.check(
                    x1,
                    y1,
                    x2,
                    y2,
                    keypoints_xy[index],
                    keypoints_conf[index],
                )

                if lying_pose:
                    frame_has_lying_pose = True

                person_w = x2 - x1
                person_h = y2 - y1

                detected_persons.append(
                    (x1, y1, x2, y2, conf, bbox_horizontal, torso_horizontal, person_w, person_h)
                )

        if not person_detected:
            self.judge.fall_count = 0
            final_status = "NO_PERSON"
            final_fall_detected = False
        else:
            if frame_has_lying_pose:
                self.judge.fall_count += 1
            else:
                self.judge.fall_count = 0

            if self.judge.fall_count >= self.judge.threshold_count:
                final_status = "FALL"
                final_fall_detected = True
            else:
                final_status = "PERSON"
                final_fall_detected = False

        label = "FALL" if final_fall_detected else "PERSON"
        color = (0, 0, 255) if final_fall_detected else (0, 255, 0)

        for (x1, y1, x2, y2, conf, bbox_horizontal, torso_horizontal,
             person_w, person_h) in detected_persons:
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            text = (
                f"{label} conf:{conf:.2f} "
                f"box:{int(bbox_horizontal)} pose:{int(torso_horizontal)} "
                f"cnt:{self.judge.fall_count}"  # 프레임 단위 수평/누움 카운트
            )

            cv2.putText(
                frame,
                text,
                (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
            )

            self.get_logger().info(
                f"person conf={conf:.2f}, "
                f"w={person_w}, h={person_h}, "
                f"bbox_horizontal={bbox_horizontal}, "
                f"torso_horizontal={torso_horizontal}, "
                f"fall={final_fall_detected}"
            )

        # 감지 기준선은 대시보드 웹화면에 CSS로 표기

        status_msg = String()
        status_msg.data = final_status
        self.status_pub.publish(status_msg)

        fall_msg = Bool()
        fall_msg.data = final_fall_detected
        self.fall_pub.publish(fall_msg)

        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            annotated_msg = CompressedImage()
            annotated_msg.header = msg.header
            annotated_msg.format = "jpeg"
            annotated_msg.data = encoded.tobytes()
            self.annotated_image_pub.publish(annotated_msg)

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

if __name__ == "__main__":
    main()