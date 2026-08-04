import cv2
import numpy as np


# =========================
# 설정
# =========================

CAMERA_INDEX = 1

# 사용하는 ArUco 마커 종류에 맞게 변경
ARUCO_DICT = cv2.aruco.DICT_5X5_250


# =========================
# ArUco 검출기 생성
# =========================

dictionary = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
parameters = cv2.aruco.DetectorParameters()
detector = cv2.aruco.ArucoDetector(dictionary, parameters)


# =========================
# 카메라 실행
# =========================

cap = cv2.VideoCapture(CAMERA_INDEX)

if not cap.isOpened():
    print("카메라를 열 수 없습니다.")
    exit()


while True:
    ret, frame = cap.read()

    if not ret:
        print("카메라 영상을 읽을 수 없습니다.")
        break

    frame_height, frame_width = frame.shape[:2]

    screen_center_x = frame_width // 2
    screen_center_y = frame_height // 2

    # ArUco 검출
    corners, ids, rejected = detector.detectMarkers(frame)

    # 화면 중심 표시
    cv2.circle(
        frame,
        (screen_center_x, screen_center_y),
        5,
        (255, 0, 0),
        -1
    )

    if ids is not None:

        # 검출된 마커 테두리와 ID 표시
        cv2.aruco.drawDetectedMarkers(frame, corners, ids)

        for marker_corner, marker_id in zip(corners, ids.flatten()):

            # 네 모서리 좌표
            points = marker_corner.reshape(4, 2)

            top_left = points[0]
            top_right = points[1]
            bottom_right = points[2]
            bottom_left = points[3]

            # 마커 중심점
            center_x = int(np.mean(points[:, 0]))
            center_y = int(np.mean(points[:, 1]))

            # 화면 중앙 기준 오프셋
            offset_x = center_x - screen_center_x
            offset_y = center_y - screen_center_y

            # 마커 중심 표시
            cv2.circle(
                frame,
                (center_x, center_y),
                6,
                (0, 0, 255),
                -1
            )

            # 화면 중심과 마커 중심 연결선
            cv2.line(
                frame,
                (screen_center_x, screen_center_y),
                (center_x, center_y),
                (0, 255, 255),
                2
            )

            # 화면에 값 표시
            text = (
                f"ID:{marker_id} "
                f"Center:({center_x},{center_y}) "
                f"Offset:({offset_x},{offset_y})"
            )

            cv2.putText(
                frame,
                text,
                (center_x + 10, center_y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 0),
                2
            )

            # 터미널 출력
            print("=" * 60)
            print(f"Marker ID      : {marker_id}")
            print(f"Top Left       : {top_left}")
            print(f"Top Right      : {top_right}")
            print(f"Bottom Right   : {bottom_right}")
            print(f"Bottom Left    : {bottom_left}")
            print(f"Center         : ({center_x}, {center_y})")
            print(f"Screen Center  : ({screen_center_x}, {screen_center_y})")
            print(f"Offset X       : {offset_x}")
            print(f"Offset Y       : {offset_y}")
            print(f"Rejected Count : {len(rejected)}")

    cv2.imshow("ArUco Camera Test", frame)

    # q 또는 ESC로 종료
    key = cv2.waitKey(1) & 0xFF

    if key == ord("q") or key == 27:
        break


cap.release()
cv2.destroyAllWindows()