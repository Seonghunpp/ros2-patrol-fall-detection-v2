# 충전 스테이션 ArUco 정렬 V2

## 목적

충전 스테이션 좌표까지 Nav2로 이동한 뒤 ArUco 마커 ID `249`를 이용해
로봇의 좌우 방향을 미세 정렬한다. 정렬이 끝나면 제자리에서 180도 회전하고
`충전중 ...` 메시지를 출력한다.

현재 구현에는 충전 스테이션으로 전진하거나 실제 충전 여부를 확인하는 동작은
포함되어 있지 않다.

## 관련 파일

- `my_patrol/aruco_id_node_v2.py`
  - 카메라 영상에서 ArUco 마커를 검출한다.
  - ID `249`를 병실 마커와 분리한다.
  - 충전소 마커 ID, 좌우 오차, 크기를 발행한다.
- `my_patrol/patrol_node_v2.py`
  - 충전소 관련 토픽을 구독한다.
  - 마커 중앙 정렬과 180도 회전을 수행한다.
- `setup.py`
  - 현재 `aruco_id` 명령은 `aruco_id_node_v2.py`를 실행한다.
  - 현재 `patrol` 명령은 `patrol_node_v2.py`를 실행한다.

## 기존 코드에서 변경된 부분

### `aruco_id_node_v2.py`

- 카메라 구독 QoS를 기본 큐 크기 `10`에서 `qos_profile_sensor_data`로
  변경했다.
- 기존에는 처음 검출된 마커 하나의 offset을 사용했지만, V2에서는 검출된
  마커를 병실 마커와 충전소 마커로 먼저 분류한다.
- 병실 마커가 여러 개 보이면 화면에서 가장 큰 마커를 정렬 대상으로 사용하고,
  해당 ID를 `/room_marker` 목록의 첫 번째에 둔다.
- ArUco 인식을 끌 때 병실 및 충전소 마커의 빈 상태를 발행하도록 변경했다.

### `setup.py`

- `ros2 run my_patrol aruco_id`가 기존 `aruco_id_node.py` 대신
  `aruco_id_node_v2.py`의 `main()`을 실행하도록 변경했다.
- `ros2 run my_patrol patrol`이 기존 `patrol_node.py` 대신
  `patrol_node_v2.py`의 `main()`을 실행하도록 변경했다.

## 새로 추가된 부분

### `aruco_id_node_v2.py`

- 충전소 전용 마커 ID 상수 `CHARGER_MARKER_ID = 249`
- 충전소 전용 토픽 `/charger_marker`, `/charger_offset`, `/charger_size`
- 마커의 좌우 오차와 화면 대비 크기를 계산하는 `marker_geometry()`
- 병실 마커와 충전소 마커를 분리해 발행하는 함수

### `charging_return.py`

- 충전소 마커 상태 변수 `charger_visible`, `latest_charger_offset`,
  `charger_offset_time`
- `/charger_marker`, `/charger_offset` 구독과 콜백 함수
- 최근 충전소 offset 수신 여부를 판단하는 `charger_marker_visible()`
- 360도 범위에서 ID 249를 찾는 탐색 상태
- ID 249를 기준으로 미세 정렬하는 정렬 상태
- 정렬 완료 후 odom 기준 180도 회전
- odom 최초 수신을 최대 3초 기다리는 처리
- 성공 시 `충전중 ...` 로그와 `True`, 실패 시 정지 후 `False` 반환

## ROS 2 토픽

### ArUco V2가 발행하는 충전소 토픽

| 토픽 | 타입 | 의미 |
| --- | --- | --- |
| `/charger_marker` | `std_msgs/msg/Int32MultiArray` | 감지 시 `[249]`, 미감지 시 `[]` |
| `/charger_offset` | `std_msgs/msg/Float32` | 화면 중심 기준 좌우 오차 |
| `/charger_size` | `std_msgs/msg/Float32` | 화면 너비 대비 마커 평균 변 길이 |

`/charger_offset`의 의미:

- 음수: 마커가 화면 왼쪽
- `0` 근처: 화면 중앙
- 양수: 마커가 화면 오른쪽

현재 충전소 중앙 정렬 허용 오차는 `±0.05`이다.

### Patrol V2가 사용하는 토픽

| 토픽 | 용도 |
| --- | --- |
| `/charger_marker` | ID 249 감지 여부 확인 |
| `/charger_offset` | 좌우 미세 정렬 |
| `/odom` | 180도 회전량 측정 |
| `/cmd_vel` | 제자리 회전 명령 발행 |

## 현재 충전소 정렬 동작

1. ArUco 인식을 활성화한다.
2. odom 기준 최대 360도 범위에서 ID 249를 탐색한다.
3. 마커를 찾으면 회전을 멈추고 `/charger_offset`으로 중앙 정렬한다.
4. 오차가 `±0.05` 이내이면 정지한다.
5. ArUco 인식을 비활성화한다.
6. `/odom`을 최대 3초 기다린다.
7. odom 누적 회전량을 기준으로 제자리에서 180도 회전한다.
8. 정지 후 `충전중 ...`을 출력하고 `True`를 반환한다.

마커 미검출, 정렬 시간 초과, odom 미수신, 회전 시간 초과 시에는 정지하고
`False`를 반환한다. 마커가 안 보일 때 자동 탐색 회전은 하지 않는다. 큰 방향
정렬은 향후 Nav2 충전소 목표 좌표의 yaw로 처리한다.

## 실행 전 준비

다음 노드가 필요하다.

1. TurtleBot bringup: `/cmd_vel`, `/odom`
2. 카메라 노드: `/image_raw/compressed`
3. ArUco V2 노드

Nav2 전체 순찰 노드는 단독 정렬 테스트에 필요하지 않다.

필수 토픽 확인:

```bash
ros2 topic list | grep -E 'image_raw/compressed|charger_offset|cmd_vel|odom'
```

## 빌드

```bash
cd ~/Desktop/yolo_test/ros2-patrol-fall-detection-v2
colcon build --symlink-install --packages-select my_patrol
source install/setup.bash
```

## 코드 실행 방법

bringup과 카메라를 먼저 실행하고, 아래 두 명령은 각각 별도 터미널에서
실행한다.

| 실행 대상 | 명령어 |
| --- | --- |
| ArUco V2 노드 | `ros2 run my_patrol aruco_id` |
| 충전소 복귀 서비스 | `ros2 run my_patrol charging_return` |

ID와 측정값은 다음 명령으로 확인한다.

```bash
ros2 topic echo /charger_marker
ros2 topic echo /charger_offset --once
ros2 topic echo /charger_size --once
```

정상 로그 흐름:

```text
ID 249 충전소 마커 탐색 시작
ID 249 발견, 중앙 정렬 시작
중앙 정렬 완료, 180도 회전 시작
충전중 ...
result: True
```

## 안전 주의사항

- 테이블 위에서 바퀴가 바닥에 닿은 상태로 주행 테스트하지 않는다.
- 최초 테스트는 바퀴가 공중에 뜨도록 로봇을 단단히 고정한다.
- 실제 정렬은 낭떠러지와 장애물이 없는 넓은 바닥에서 시험한다.
- teleop, 기존 patrol 등 다른 `/cmd_vel` 발행 노드는 종료한다.
- 이상 동작 시 즉시 `Ctrl+C`를 누르거나 로봇 전원을 끈다.
- 비상 정지 메시지는 다음 명령으로 발행할 수 있다.

```bash
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist "{}"
```

## 확인된 환경 문제

시스템 OpenCV와 NumPy 2.x가 충돌하면 다음 오류가 발생할 수 있다.

```text
ImportError: numpy.core.multiarray failed to import
```

현재 환경에서는 NumPy `1.26.4`로 맞춰 해결했다.

```bash
python3 -m pip install --user --force-reinstall "numpy==1.26.4"
```
