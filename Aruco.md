# ArUco Marker 실행

TurtleBot 카메라를 이용해 ArUco Marker의 ID와 상대 위치를 검출한다.

### 설정값

- ROS_DOMAIN_ID: `5`
- ArUco Dictionary: `DICT_5X5_250`
- Marker Size: `0.08 m` (8 cm)
- Camera Resolution: `640 × 480`
- Camera Calibration: `/home/team3/camera_info.yaml`

### 실행 위치

- **터미널 1, 2:** Raspberry Pi에서 실행
- **터미널 3, 4:** PC에서 실행
- 실행 순서: **1 → 2 → 3 → 4**

---

<table>
<tr>
<td valign="top">

### 터미널 1 — TurtleBot Bringup

```bash
ssh team3@192.168.0.45

export ROS_DOMAIN_ID=5
export TURTLEBOT3_MODEL=waffle_pi

ros2 launch turtlebot3_bringup robot.launch.py
```

</td>
<td valign="top">

### 터미널 2 — Camera

```bash
ssh team3@192.168.0.45

export ROS_DOMAIN_ID=5

ros2 run v4l2_camera v4l2_camera_node --ros-args \
  -p video_device:="/dev/video0" \
  -p image_size:="[640,480]" \
  -p camera_info_url:="file:///home/team3/camera_info.yaml"
```

</td>
</tr>

<tr>
<td valign="top">

### 터미널 3 — ArUco Node

PC에서 실행한다.

```bash
export ROS_DOMAIN_ID=5

ros2 run ros2_aruco aruco_node --ros-args \
  -p marker_size:=0.08 \
  -p aruco_dictionary_id:=DICT_5X5_250 \
  -p image_topic:=/image_raw \
  -p camera_info_topic:=/camera_info
```

</td>
<td valign="top">

### 터미널 4 — 검출 결과 확인

PC에서 실행한다.

```bash

ros2 topic echo /aruco_markers
```

마커를 카메라에 보여줬을 때 값이 출력되면 정상이다.

</td>
</tr>
</table>

---

## 검출 결과

```text
marker_ids
└─ 검출된 ArUco Marker ID

position.x
└─ 카메라 기준 좌우 위치 [m]

position.y
└─ 카메라 기준 상하 위치 [m]

position.z
└─ 카메라와 마커 사이 전방 거리 [m]

orientation
└─ 마커 회전 자세 (Quaternion)
```

정상 검출 예시:

```text
marker_ids:
- 2

position:
  x: ...
  y: ...
  z: ...
```

`marker_ids`와 `position` 값이 계속 출력되면 ArUco 검출이 정상적으로 동작하는 상태.