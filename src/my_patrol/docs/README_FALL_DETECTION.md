# YOLO Pose 기반 낙상 감지

`fall_detection_node_v2.py`는 ROS 2 압축 카메라 영상을 받아 커스텀 YOLO Pose 모델로 `person`과 `fall_person`을 분류합니다. 같은 Tracking ID가 `fall_person`으로 10프레임 연속 검출되면 최종 낙상으로 확정합니다.

관절 좌표는 결과 영상에 스켈레톤을 표시하는 데만 사용합니다. 최종 낙상 판정에는 몸통 각도나 관절 수평 조건을 사용하지 않습니다.

## 주요 기능

- 커스텀 YOLO Pose 모델의 `person`/`fall_person` 클래스 사용
- YOLO Tracking ID를 이용한 사람별 상태 구분
- 사람마다 독립적인 `fall_count` 관리
- 연속 프레임 검증으로 순간적인 클래스 오탐 억제
- 낙상 확정 상태에서 중복 이벤트 방지
- 낙상 확정 순간의 전체 결과 화면을 JPG로 저장
- 스켈레톤, 커스텀 박스, 클래스, ID, 신뢰도와 누적 횟수 표시
- ROS 2 서비스를 통한 감지 활성화·비활성화

## 파일 및 모델 경로

실행 파일:

```text
src/my_patrol/my_patrol/fall_detection_node_v2.py
```

기본 모델:

```text
src/my_patrol/model/patient_pose_v2.pt
```

모델 클래스:

```text
0: person
1: fall_person
```

ROS 파라미터 `model_path`로 다른 모델을 지정할 수 있습니다.

```bash
ros2 run my_patrol fall_detection --ros-args -p model_path:=/경로/모델.pt
```

교체 모델도 반드시 `person`, `fall_person` 클래스 이름을 사용해야 합니다.

## 모델 실행 설정

```python
self.model.track(
    image,
    persist=True,
    imgsz=640,
    conf=0.25,
    iou=0.20,
)
```

- `persist=True`: 프레임 사이의 Tracking ID 유지
- `conf=0.25`: 박스를 사용할 최소 모델 신뢰도
- `iou=0.20`: 중복 박스를 제거하는 NMS IoU 기준
- `threshold_count=10`: 같은 ID가 `fall_person`으로 연속 검출돼야 하는 횟수

## 낙상 판정 기준

사람별 상태는 다음 값으로 관리합니다.

```python
{
    "fall_count": 0,
    "fall_event_active": False,
    "last_seen_frame": frame_index,
}
```

판정 규칙:

```text
person
→ fall_count = 0
→ PERSON

fall_person
→ fall_count + 1
→ 10회 미만: FALL CANDIDATE
→ 10회 도달: FALL DETECTED
```

카운트는 최대 `10/10`까지만 증가합니다. `person`으로 판정되는 프레임이 나오면 0으로 초기화됩니다.

낙상이 처음 확정될 때만 이벤트를 발생시키고 이미지를 저장합니다. 같은 ID가 계속 낙상 상태인 동안에는 중복 저장하지 않습니다. 이후 `person`으로 돌아오면 이벤트 상태가 해제되어 다음 낙상을 다시 감지할 수 있습니다.

## 사람별 추적

각 객체에는 임시 `track_id`가 부여됩니다. 낙상 카운트와 이벤트 상태는 ID별로 독립적으로 관리되므로 여러 사람이 동시에 검출되어도 카운트가 서로 섞이지 않습니다.

해당 ID가 30프레임 동안 보이지 않으면 저장된 상태를 삭제합니다. 가림, 검출 누락 또는 화면 이탈로 ID가 변경되면 같은 사람이라도 새로운 객체로 처리되어 카운트가 0부터 시작합니다.

## ROS 통신

구독 토픽:

```text
/image_raw/compressed
```

결과 영상 발행 토픽:

```text
/image_annotated/compressed
```

결과 영상 publisher의 큐 깊이는 `1`입니다. 오래된 프레임을 여러 장 쌓지 않고 최신 영상의 실시간성을 우선합니다.

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

## 결과 화면

표시 예시:

```text
PERSON ID:2  person 91%  [0/10]
FALL CANDIDATE ID:2  fall_person 91%  [4/10]
FALL DETECTED ID:2  fall_person 91%  [10/10]
```

- `PERSON`: 녹색 박스
- `FALL CANDIDATE`: 주황색 박스
- `FALL DETECTED`: 빨간색 박스

결과 확인:

```bash
ros2 run rqt_image_view rqt_image_view
```

RQT에서 `/image_annotated/compressed`를 선택합니다.

발행 속도 확인:

```bash
ros2 topic hz /image_annotated/compressed
```

## 낙상 이미지 저장

낙상 확정 순간의 전체 결과 화면을 다음 폴더에 저장합니다.

```text
~/fall_images
```

같은 프레임에서 여러 ID가 동시에 처음 확정되더라도 전체 이미지는 한 장만 저장합니다.

## 처리 순서

```text
image_callback()
→ _decode_image()
→ _run_pose_model()
→ _extract_persons()에서 박스·클래스·신뢰도·Track ID 추출
→ ID별 fall_person 연속 횟수 갱신
→ 10회 도달 여부 확인
→ 새 낙상 이벤트 확인
→ _draw_results()
→ 새 낙상 확정 시 _save_fall_image()
→ _publish_results()
```

## 알려진 제한 사항

- 최종 판정은 모델의 클래스 결과를 신뢰하므로 지속적인 클래스 오탐은 그대로 낙상 경보로 이어질 수 있습니다.
- `person`이 한 프레임이라도 나오면 현재 카운트는 0으로 초기화됩니다. 클래스가 자주 흔들리면 낙상 확정이 지연될 수 있습니다.
- Tracking ID가 변경되면 같은 사람이라도 카운트가 0부터 다시 시작합니다.
- 검출 결과에 Tracking ID가 없는 프레임은 사람 상태 갱신에서 제외됩니다.
- `10프레임`의 실제 시간은 카메라 입력 및 추론 FPS에 따라 달라집니다.
- 저장 이미지는 사람 영역만 자르는 방식이 아니라 전체 화면입니다.
