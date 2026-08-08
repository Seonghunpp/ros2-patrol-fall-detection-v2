[ArUco 실행 방법](./Aruco.md)
# ros2-patrol-fall-detection

TurtleBot3 기반 **병실 순찰 로봇** — Nav2로 병실을 순회하며 ArUco 마커로 병실 번호를 인식하고, YOLOv8-pose로 낙상 환자를 감지.

## 주요 기능

- **Waypoint 기반 순찰** — 저장된 병실 좌표를 Nav2로 순차 방문
- **ArUco 마커 인식** — 병실 도착 후 마커 ID로 병실 식별 (압축 영상 직접 구독)
- **낙상 감지** — YOLOv8-pose 관절 감지

## 주요 패키지

| 노드 | 역할 |
|------|------|
| `waypoint_saver` | 병실 좌표 저장 |
| `patrol` | 순찰 + 마커 정렬 + 환자 확인 |
| `aruco_id` | 마커 ID/offset 인지 |
| `fall_detection` | 낙상 감지 (YOLOv8-pose) |
| `dashboard_server` | 웹 대시보드 — ROS 브리지 + Flask (`:5000`) |

## 환경

- ROS2 Humble / Ubuntu 22.04
- TurtleBot3 (WAFFLE_PI)


### 모델 파일

낙상 감지는 `yolov8n-pose.pt`(Ultralytics 공식 모델)를 사용.
- 기본 경로: `~/yolov8n-pose.pt`
- 파일이 없으면 최초 실행 시 자동 다운로드
- 다른 경로 지정: `ros2 run my_patrol fall_detection --ros-args -p model_path:=/경로/모델.pt`

## 실행

### 🍓 라즈베리파이 (로봇)

```bash
# ① 로봇 bringup — 모터·odom·배터리 등 로봇 구동
ros2 launch turtlebot3_bringup robot.launch.py

# ② 카메라 켜기 — /image_raw/compressed 영상 발행
ros2 run v4l2_camera v4l2_camera_node --ros-args \
  -p video_device:="/dev/video0" \
  -p image_size:="[640,480]" \
  -p camera_info_url:="file:///home/team3/camera_info.yaml"
```

### 💻 원격 PC (Ubuntu)

**맵 생성·저장 (최초 1회)**

```bash
ros2 launch turtlebot3_cartographer cartographer.launch.py   # 맵 작성
ros2 run nav2_map_server map_saver_cli -f ~/map              # 맵 저장
```

**순찰·감지·대시보드 실행**

```bash
# 저장한 맵으로 Nav2 실행
ros2 launch turtlebot3_navigation2 navigation2.launch.py map:=$HOME/map.yaml

ros2 run my_patrol waypoint_saver     # (순찰 전) 병실 좌표 저장 → rooms.yaml
ros2 run my_patrol aruco_id           # 마커 인식
ros2 run my_patrol fall_detection     # 낙상 감지 (YOLOv8-pose)
ros2 run my_patrol patrol             # 순찰 메인 (Nav2 이동 + 마커 정렬 + 낙상 확인)
ros2 run dashboard dashboard_server   # 웹 대시보드 (http://localhost:5000)
```


## 토픽

| 토픽 | 타입 | 설명 |
|------|------|------|
| `/image_raw/compressed` | `sensor_msgs/CompressedImage` | 카메라 압축 영상 |
| `/image_annotated/compressed` | `sensor_msgs/CompressedImage` | YOLO 주석 영상 (fall_detection 발행) |
| `/room_marker` | `std_msgs/Int32MultiArray` | 인식된 마커 ID |
| `/marker_offset` | `std_msgs/Float32` | 마커 화면 좌우 위치 (정렬용) |
| `/fall_status` | `std_msgs/String` | NO_PERSON / PERSON / FALL |
| `/fall_detected` | `std_msgs/Bool` | 낙상 확정 여부 |
| `/cmd_vel` | `geometry_msgs/Twist` | 주행 명령 (마커 정렬·정지) |
| `/odom` | `nav_msgs/Odometry` | 로봇 오도메트리 |
| `/battery_state` | `sensor_msgs/BatteryState` | 배터리 상태 |

## 서비스

| 서비스 | 타입 | 설명 |
|------|------|------|
| `aruco_enable` | `std_srvs/SetBool` | 마커 인식 on/off (patrol이 호출) |
| `fall_enable` | `std_srvs/SetBool` | 낙상 감지 on/off (patrol이 호출) |
