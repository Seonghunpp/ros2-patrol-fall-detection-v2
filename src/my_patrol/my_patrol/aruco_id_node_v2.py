#!/usr/bin/env python3
"""병실 및 충전소 ArUco 마커 인식 노드.

병실 마커와 충전소 마커(ID 249)를 분리해서 발행한다.

  ros2 run my_patrol aruco_id
"""
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, CompressedImage
from std_msgs.msg import Int32MultiArray, Float32
from std_srvs.srv import SetBool
from cv_bridge import CvBridge
import cv2
import numpy as np


ARUCO_DICT = cv2.aruco.DICT_5X5_250
CHARGER_MARKER_ID = 249
DEFAULT_MARKER_SIZE = 0.08


class ArucoIdNode(Node):
    def __init__(self):
        super().__init__('aruco_id')
        self.bridge = CvBridge()
        self.enabled = True   # /aruco_enable로 끌 수 있음 (기본 ON: 단독 테스트용)
        # ID 249 마커 전체 한 변의 실제 길이(m).
        # 필요하면 --ros-args -p marker_size:=0.08로 변경한다.
        self.declare_parameter('marker_size', DEFAULT_MARKER_SIZE)
        self.marker_size = float(self.get_parameter('marker_size').value)
        self.camera_matrix = None
        self.distortion = None

        self.aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
        # OpenCV 4.7+ (신 API: ArucoDetector) / 4.6 이하 (구 API) 모두 지원
        if hasattr(cv2.aruco, 'ArucoDetector'):
            params = cv2.aruco.DetectorParameters()
            self.detector = cv2.aruco.ArucoDetector(self.aruco_dict, params)
        else:
            self.detector = None
            self.params = cv2.aruco.DetectorParameters_create()
        self.last_ids = None   # 직전 프레임의 ID 목록 (변할 때만 발행)

        # 압축 영상을 직접 구독
        self.create_subscription(
            CompressedImage,
            '/image_raw/compressed',
            self.image_cb,
            qos_profile_sensor_data,
        )
        # v4l2_camera가 camera_info.yaml 기준으로 발행하는 보정값
        self.create_subscription(
            CameraInfo,
            '/camera_info',
            self.camera_info_cb,
            qos_profile_sensor_data,
        )
        # 외부에서 인식 on/off 제어 (SetBool 서비스)
        self.create_service(SetBool, 'aruco_enable', self.enable_srv)
        # 검출된 ID 발행 (바뀔 때만)
        self.id_pub = self.create_publisher(Int32MultiArray, '/room_marker', 10)
        # 병실 마커의 화면 좌우 위치 (기존 순찰 정렬용)
        self.offset_pub = self.create_publisher(Float32, '/marker_offset', 10)
        # 충전소 마커 전용 토픽
        self.charger_id_pub = self.create_publisher(
            Int32MultiArray, '/charger_marker', 10)
        self.charger_offset_pub = self.create_publisher(
            Float32, '/charger_offset', 10)
        self.charger_size_pub = self.create_publisher(
            Float32, '/charger_size', 10)
        # 카메라에서 충전소 마커까지의 전방 거리(m)
        self.charger_distance_pub = self.create_publisher(
            Float32, '/charger_distance', 10)

        self.get_logger().info(
            f'aruco_id Start (charger marker size: {self.marker_size:.3f} m)'
        )

    def camera_info_cb(self, msg):
        """camera_info.yaml에서 발행된 카메라 보정값을 저장한다."""
        self.camera_matrix = np.asarray(msg.k, dtype=np.float64).reshape(3, 3)
        self.distortion = np.asarray(msg.d, dtype=np.float64)

    def enable_srv(self, request, response):
        self.enabled = request.data
        # 켤 때 직전 결과를 비워서, 같은 마커가 보여도 다시 발행
        if self.enabled:
            self.last_ids = None
        else:
            self.publish_room_ids([])
            self.charger_id_pub.publish(Int32MultiArray(data=[]))
        state = 'ON' if self.enabled else 'OFF'
        self.get_logger().info(f'identification {state}')
        response.success = True
        response.message = f'aruco {state}'
        return response

    @staticmethod
    def largest_marker_index(detections):
        """검출 목록에서 화면 면적이 가장 큰 마커의 인덱스를 반환한다."""
        return max(
            range(len(detections)),
            key=lambda i: abs(
                cv2.contourArea(detections[i][0].reshape(4, 2))
            ),
        )

    @staticmethod
    def marker_geometry(corner, image_width):
        """마커의 정규화된 좌우 오차와 크기를 계산한다."""
        points = corner.reshape(4, 2)
        marker_cx = float(points[:, 0].mean())
        offset = (marker_cx - image_width / 2.0) / (image_width / 2.0)

        edge_lengths = [
            cv2.norm(points[i], points[(i + 1) % 4]) for i in range(4)
        ]
        size = float(sum(edge_lengths) / len(edge_lengths) / image_width)
        return offset, size

    def publish_room_ids(self, room_ids):
        """병실 마커 ID가 변했을 때만 발행한다."""
        if room_ids == self.last_ids:
            return

        self.last_ids = room_ids
        self.id_pub.publish(Int32MultiArray(data=room_ids))
        if room_ids:
            self.get_logger().info(f'Room marker ID: {room_ids}')
        else:
            self.get_logger().info('Room marker disappeared')

    def publish_room_markers(self, detections, image_width):
        """병실 ID와 정렬 대상 병실 마커의 좌우 오차를 발행한다."""
        if not detections:
            self.publish_room_ids([])
            return

        selected = self.largest_marker_index(detections)
        corner, selected_id = detections[selected]
        offset, _ = self.marker_geometry(corner, image_width)
        self.offset_pub.publish(Float32(data=offset))

        room_ids = [selected_id]
        room_ids.extend(
            marker_id for i, (_, marker_id) in enumerate(detections)
            if i != selected
        )
        self.publish_room_ids(room_ids)

    def publish_charger_marker(self, detections, image_width):
        """충전소 마커의 좌우 오차, 크기, 보정 거리를 발행한다."""
        if not detections:
            self.charger_id_pub.publish(Int32MultiArray(data=[]))
            return

        selected = self.largest_marker_index(detections)
        corner, _ = detections[selected]
        offset, size = self.marker_geometry(corner, image_width)

        self.charger_id_pub.publish(
            Int32MultiArray(data=[CHARGER_MARKER_ID]))
        self.charger_offset_pub.publish(Float32(data=offset))
        self.charger_size_pub.publish(Float32(data=size))

        # 카메라 보정값이 없으면 임의의 거리를 발행하지 않는다.
        if self.camera_matrix is None:
            self.get_logger().warn(
                '/camera_info를 기다리는 중입니다.',
                throttle_duration_sec=5.0,
            )
            return

        # 마커 모서리, 실제 크기, 카메라 보정값으로 pose를 구한다.
        # tvec의 z가 카메라 기준 마커까지의 전방 거리다.
        _, tvecs, _ = cv2.aruco.estimatePoseSingleMarkers(
            [corner],
            self.marker_size,
            self.camera_matrix,
            self.distortion,
        )
        distance = float(tvecs[0][0][2])
        self.charger_distance_pub.publish(Float32(data=distance))

    def image_cb(self, msg):
        # 꺼져 있으면 압축해제·검출 자체를 안 함 → CPU 절약
        if not self.enabled:
            return
        img = self.bridge.compressed_imgmsg_to_cv2(msg)      # 노드 내부에서 압축 풀기
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, w = gray.shape[:2]
        # ID만 검출 (pose 없음) — OpenCV 버전에 맞는 방식 사용
        if self.detector is not None:
            corners, ids, _ = self.detector.detectMarkers(gray)          # 신 API
        else:
            corners, ids, _ = cv2.aruco.detectMarkers(
                gray, self.aruco_dict, parameters=self.params)           # 구 API

        detections = [] if ids is None else [
            (corner, int(marker_id))
            for corner, marker_id in zip(corners, ids.flatten())
        ]
        room_detections = [
            detection for detection in detections
            if detection[1] != CHARGER_MARKER_ID
        ]
        charger_detections = [
            detection for detection in detections
            if detection[1] == CHARGER_MARKER_ID
        ]

        self.publish_room_markers(room_detections, w)
        self.publish_charger_marker(charger_detections, w)


def main():
    rclpy.init()
    node = ArucoIdNode()
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
