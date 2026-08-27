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
        self.threshold_count = 10
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
            1,
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

    def _extract_persons(self, result): # 사람별 박스, 클래스, 추적 ID 묶기
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

        for index in range(len(boxes_xyxy)):
            x1, y1, x2, y2 = map(int, boxes_xyxy[index])
            class_id = boxes_cls[index]
            class_name = result.names[class_id]

            person = {
                "track_id": track_ids[index],
                "box": (x1, y1, x2, y2),
                "confidence": float(boxes_conf[index]),
                "class_id": class_id,
                "class_name": class_name,
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
        class_name = person["class_name"]
        track_id = person["track_id"]
        fall_count = person["fall_count"]
        fall_detected = person["fall_detected"]

        if fall_detected:
            label = "FALL DETECTED"
            color = (40, 40, 230)
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
            f"{class_name} {confidence:.0%}  "
            f"[{fall_count}/{self.threshold_count}]"
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
                    "fall_count": 0,
                    "fall_event_active": False,
                    "last_seen_frame": self.frame_index,
                }

            state = self.person_states[track_id]
            state["last_seen_frame"] = self.frame_index

            # 모델이 같은 객체를 fall_person으로 연속 판정할 때만 누적한다.
            if person["class_name"] == "fall_person":
                state["fall_count"] = min(
                    state["fall_count"] + 1,
                    self.threshold_count,
                )
            else:
                state["fall_count"] = 0

            fall_detected = state["fall_count"] >= self.threshold_count

            person["fall_count"] = state["fall_count"]
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
