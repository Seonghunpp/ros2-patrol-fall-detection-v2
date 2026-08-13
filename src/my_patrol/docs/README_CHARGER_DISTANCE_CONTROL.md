# 충전 스테이션 정렬·거리 제어

## 동작 개요

Nav2로 충전소 좌표에 도착한 후 ArUco ID `249`를 사용해 정렬한다.

```text
ID 249 탐색
→ 마커 중앙 정렬
→ 중앙을 유지하며 20cm까지 접근
→ 180도 제자리 회전
→ odom 기준 15cm 후진
→ "충전중 ..." 출력
```

실제 충전 커넥터를 체결하는 도킹 제어가 아니라, 충전 위치에 정렬하는
데모 로직이다.

## 기준값

| 항목 | 값 | 설명 |
| --- | --- | --- |
| 카메라 해상도 | `640×480` | `camera_info.yaml` 생성 해상도 |
| 체커보드 | 내부 코너 `8×6`, 한 칸 `0.020m` | 카메라 캘리브레이션 기준 |
| ArUco ID 249 | 한 변 `0.08m` | 마커 전체 검은 정사각형 길이 |
| 정지 거리 | `0.20m` | 카메라에서 마커까지의 z 거리 |
| 후진 거리 | `0.15m` | 180도 회전 후 odom 기준 이동 거리 |

## 수정된 코드

### `aruco_id_node_v2.py`

기존 ID·좌우 offset 검출에 카메라 보정 거리 계산을 추가했다.

```python
# ID 249 마커 전체 한 변의 실제 길이(m)
DEFAULT_MARKER_SIZE = 0.08

# camera_info.yaml의 카메라 행렬과 왜곡 계수 수신
self.create_subscription(
    CameraInfo, '/camera_info', self.camera_info_cb, qos_profile_sensor_data
)

# 카메라에서 마커까지의 전방 거리(m)
self.charger_distance_pub = self.create_publisher(
    Float32, '/charger_distance', 10
)
```

거리는 OpenCV pose estimation의 `tvec.z`로 구한다.

```python
_, tvecs, _ = cv2.aruco.estimatePoseSingleMarkers(
    [corner],
    self.marker_size,
    self.camera_matrix,
    self.distortion,
)
distance = float(tvecs[0][0][2])
```

`/camera_info`가 없으면 임의의 거리를 발행하지 않는다.

### `charging_return.py`

`/charger_distance` 구독과 두 개의 ROS 파라미터를 추가했다.

```python
self.create_subscription(
    Float32, '/charger_distance', self.charger_distance_callback, 10
)

# 단위: m
self.declare_parameter('charger_target_distance', 0.20)
self.declare_parameter('charger_backup_distance', 0.15)
```

상태별 동작:

| 상태 | 동작 |
| --- | --- |
| `search` | 최대 360도 회전하며 ID 249 탐색 |
| `align` | `/charger_offset` 기준 중앙 정렬 |
| `approach` | 중앙을 유지하며 `/charger_distance` 기준 전진 |
| `turn` | odom yaw 기준 180도 회전 |
| `backup` | odom x/y 기준 지정 거리 후진 |

접근 중 마커가 중앙에서 벗어나면 정지하고 `align`으로 돌아간다.
마커나 거리 데이터가 유실되면 정지하고 `search`를 재개한다.

주요 조정값:

```python
CHARGER_CENTER_TOL = 0.05         # 화면 중앙 허용 오차 ±5%
CHARGER_DISTANCE_TOL = 0.02       # 목표 거리 허용 오차 2cm
CHARGER_APPROACH_MIN_SPEED = 0.03 # 접근 최저 속도(m/s)
CHARGER_APPROACH_MAX_SPEED = 0.08 # 접근 최고 속도(m/s)
CHARGER_BACKUP_SPEED = 0.05       # 후진 속도(m/s)
```

### `package.xml`

거리 계산에 사용하는 의존성을 추가했다.

```xml
<exec_depend>sensor_msgs</exec_depend>
<exec_depend>cv_bridge</exec_depend>
<exec_depend>python3-numpy</exec_depend>
<exec_depend>python3-opencv</exec_depend>
```

## 빌드

```bash
cd ~/Desktop/yolo_test/ros2-patrol-fall-detection-v2
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select my_patrol
source install/setup.bash
```

## 테스트

bringup, 카메라, Nav2는 먼저 실행해야 한다. Nav2 주행 전 RViz에서
`2D Pose Estimate`로 초기 위치를 지정한다.

### 1. ArUco와 거리 확인

```bash
cd ~/Desktop/yolo_test/ros2-patrol-fall-detection-v2
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 run my_patrol aruco_id --ros-args -p marker_size:=0.08
```

다른 터미널:

```bash
cd ~/Desktop/yolo_test/ros2-patrol-fall-detection-v2
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 topic echo /charger_distance
```

줄자 거리와 토픽 값이 비슷한지 먼저 확인한다. 차이가 크면 주행하지 말고
마커 실측 크기, 인쇄 배율, `/camera_info`를 확인한다.

### 2. 전체 동작 테스트

첫 테스트는 안전을 위해 후진 거리를 5cm로 제한한다.

```bash
cd ~/Desktop/yolo_test/ros2-patrol-fall-detection-v2
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 run my_patrol charging_return --ros-args \
  -p charger_target_distance:=0.20 \
  -p charger_backup_distance:=0.05
```

다른 터미널에서 충전소 이동을 시작한다.

```bash
cd ~/Desktop/yolo_test/ros2-patrol-fall-detection-v2
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 service call /go_to_dock std_srvs/srv/Trigger "{}"
```

5cm 후진이 안전하면 `charger_backup_distance:=0.15`로 변경한다.

## 정상 로그

```text
ID 249 충전소 마커 탐색 시작
ID 249 발견, 중앙 정렬 시작
중앙 정렬 완료, 0.20 m까지 접근 시작
목표 거리 도착 (0.20 m), 180도 회전 시작
180도 회전 완료, 0.15 m 후진 시작
충전중 ...
```

## 안전·튀닝

- TurtleBot3 Waffle Pi는 약 `281×306mm`이다. 로봇 전후에 충분한 공간을 둔다.
- 첫 테스트는 벽이 아닌 넓은 바닥에서 실행한다.
- teleop·patrol 등 다른 `/cmd_vel` 발행 노드를 동시에 실행하지 않는다.
- 거리 값이 비정상이거나 `/camera_info`가 없으면 주행하지 않는다.
- 현장 오차는 코드 수정 대신 ROS 파라미터로 먼저 튀닝한다.

비상 정지:

```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist "{}"
```

## 검증 상태

- Python 문법 검사 통과
- OpenCV pose API 호출 확인
- `colcon build --symlink-install --packages-select my_patrol` 통과
- 실물 로봇 주행 테스트 미실시
