# YOLO Pose 기반 낙상 감지 테스트

`fall_detection_node_v2.py`는 ROS 2 압축 카메라 이미지를 받아 YOLO Pose로 사람과 관절을 검출하고, 사람별 몸통 수평 상태가 10프레임 연속 유지되면 낙상으로 판정하는 노드입니다.

## 주요 기능

- YOLO Pose 기반 사람·관절 검출
- YOLO Tracking ID를 이용한 사람별 상태 구분
- 사람마다 독립적인 낙상 카운트 관리
- 어깨 중심과 골반 중심을 이용한 기본 수평 판정
- 기본 관절이 가려지면 한쪽 어깨·골반 또는 팔꿈치·무릎으로 보조 판정
- 낙상 확정 순간의 결과 이미지를 JPG로 한 번 저장
- 관절, 커스텀 박스, ID, 신뢰도와 낙상 카운트 표시
- ROS 2 서비스를 통한 감지 활성화·비활성화

## 파일 및 모델 경로

실행 파일:

```text
src/my_patrol/my_patrol/fall_detection_node_v2.py
```

기본 모델 경로:

```text
src/my_patrol/model/patient_pose_v2.pt
```

ROS 파라미터 `model_path`로 다른 모델을 지정할 수도 있습니다.

## 낙상 판정 기준

기본 설정은 다음과 같습니다.

```python
body_ratio = 1.0
keypoint_conf = 0.25
threshold_count = 10
```

- `body_ratio`: 두 기준점의 가로 거리가 세로 거리보다 클 때 수평으로 판정
- `keypoint_conf`: 판정에 사용할 수 있는 관절의 최소 신뢰도
- `threshold_count`: 수평 상태가 10프레임 연속 유지되면 낙상 확정

카운트는 최대 `10/10`까지만 증가합니다. 관절을 판단할 수 없는 경우에는 기존 카운트를 유지하고, 정상 자세로 판정되면 0으로 초기화합니다.

## 사람별 추적

모델은 다음 방식으로 실행됩니다.

```python
self.model.track(image, persist=True, ...)
```

각 사람에게 임시 `track_id`가 붙으며, ID별로 별도의 `FallJudge`를 사용합니다. 따라서 다른 사람이 화면에 들어와도 기존 사람의 낙상 카운트와 섞이지 않습니다.

추적 상태는 30프레임 동안 해당 ID가 보이지 않으면 삭제됩니다. 사람이 심하게 가려지거나 화면 밖으로 나갔다 돌아오면 추적 ID가 바뀔 수 있습니다.

## ROS 통신

구독 토픽:

```text
/image_raw/compressed
```

결과 이미지 발행 토픽:

```text
/image_annotated/compressed
```

감지 활성화 서비스:

```text
/fall_enable
```

감지 끄기:

```bash
ros2 service call /fall_enable std_srvs/srv/SetBool "{data: false}"
```

감지 켜기:

```bash
ros2 service call /fall_enable std_srvs/srv/SetBool "{data: true}"
```

감지를 다시 켜면 기존 사람별 판정 상태가 초기화됩니다.

## 이미지 표시

결과 영상에는 다음 정보가 표시됩니다.

```text
PERSON ID:2  91%  [4/10]
FALL DETECTED ID:2  91%  [10/10]
```

- 정상 사람: 녹색 커스텀 박스
- 낙상 사람: 빨간색 커스텀 박스

결과 확인:

```bash
ros2 run rqt_image_view rqt_image_view
```

RQT에서 다음 토픽을 선택합니다.

```text
/image_annotated/compressed
```

발행 속도 확인:

```bash
ros2 topic hz /image_annotated/compressed
```

## 낙상 이미지 저장

낙상 확정 순간의 박스와 관절이 표시된 전체 화면을 저장합니다.

같은 사람의 낙상 상태가 계속 유지되는 동안에는 중복 저장하지 않습니다. 다른 ID가 새로 낙상으로 확정되면 이미지를 추가로 저장합니다. 같은 프레임에서 여러 명이 동시에 확정되면 전체 화면을 한 장만 저장합니다.

## 처리 순서

```text
image_callback()
→ _decode_image()
→ _run_pose_model()
→ _extract_persons()
→ ID별 _check_body_horizontal()
→ ID별 _update_fall_count()
→ ID별 _check_fall_threshold()
→ _draw_results()
→ 새 낙상 확정 시 _save_fall_image()
→ _publish_results()
```

## 알려진 제한 사항

- 추적 ID는 실제 신원이 아닌 일시적인 번호입니다.
- 사람끼리 심하게 겹치거나 검출이 오래 끊기면 ID가 변경될 수 있습니다. 
- ID가 변경되면 동일 인물이어도 새로운 사람으로 처리되어 카운트가 0부터 시작합니다.
- 현재 낙상 이미지는 사람 영역만 자르는 방식이 아니라 전체 화면을 저장합니다.
- 실제 카메라 FPS와 촬영 환경에 따라 `threshold_count`, `keypoint_conf`, `imgsz` 조정이 필요할 수 있습니다.
