#!/usr/bin/env python3

import os
import random
import yaml

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient

from nav2_msgs.action import NavigateToPose
from action_msgs.msg import GoalStatus


# =========================================================
# YAML 파일 경로
# =========================================================

PATIENT_FILE = os.path.expanduser(
    "~/ros2-patrol-fall-detection-v2/"
    "src/my_patrol/config/patients.yaml"
)

ROOM_FILE = os.path.expanduser(
    "~/ros2-patrol-fall-detection-v2/"
    "src/my_patrol/config/rooms.yaml"
)


# =========================================================
# patients.yaml 병실 번호 ↔ rooms.yaml 병실 이름
# =========================================================

ROOM_KEY_MAP = {
    "101": "room1",
    "102": "room2",
    "103": "room3",
    "104": "room4"
}


# =========================================================
# 낙상 위험도 점수
# =========================================================

def get_fall_risk_score(risk):

    scores = {
        "매우 높음": 50,
        "높음": 35,
        "보통": 20,
        "낮음": 5
    }

    return scores.get(risk, 0)


# =========================================================
# 나이 점수
# =========================================================

def get_age_score(age):

    if age >= 80:
        return 30

    elif age >= 70:
        return 25

    elif age >= 60:
        return 20

    elif age >= 50:
        return 10

    else:
        return 5


# =========================================================
# 질환 점수
# =========================================================

def get_disease_score(disease):

    if "치매" in disease:
        return 25

    elif "뇌경색" in disease:
        return 25

    elif "파킨슨" in disease:
        return 25

    elif "대퇴골" in disease:
        return 20

    elif "골절" in disease:
        return 20

    elif "심부전" in disease:
        return 15

    else:
        return 5


# =========================================================
# 환자 종합 점수
# =========================================================

def calculate_patient_score(patient):

    fall_score = get_fall_risk_score(
        patient["fall_risk"]
    )

    age_score = get_age_score(
        patient["age"]
    )

    disease_score = get_disease_score(
        patient["disease"]
    )

    return (
        fall_score
        + age_score
        + disease_score
    )


# =========================================================
# 병실별 우선순위 계산
# =========================================================

def calculate_room_priority():

    # -----------------------------------------------------
    # patients.yaml 읽기
    # -----------------------------------------------------

    with open(
        PATIENT_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        data = yaml.safe_load(f)

    patients = data["patients"]

    rooms = {}

    # -----------------------------------------------------
    # 병실별 환자 점수 저장
    # -----------------------------------------------------

    for patient in patients:

        room = str(
            patient["room"]
        )

        score = calculate_patient_score(
            patient
        )

        if room not in rooms:
            rooms[room] = []

        rooms[room].append({
            "name": patient["name"],
            "score": score
        })

    room_priority = []

    # -----------------------------------------------------
    # 병실 점수 =
    # 해당 병실 환자 종합점수 중 최대값
    # -----------------------------------------------------

    for room, patient_list in rooms.items():

        highest_patient = max(
            patient_list,
            key=lambda p: p["score"]
        )

        room_priority.append({

            "room": room,

            "score":
                highest_patient["score"],

            "patient":
                highest_patient["name"]
        })

    # -----------------------------------------------------
    # 점수 높은 순으로 정렬
    # -----------------------------------------------------

    room_priority.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return room_priority


# =========================================================
# Priority Patrol Node
# =========================================================

class PriorityPatrol(Node):

    def __init__(self):

        super().__init__(
            "priority_patrol"
        )

        # -------------------------------------------------
        # Nav2 NavigateToPose
        # -------------------------------------------------

        self.nav_client = ActionClient(
            self,
            NavigateToPose,
            "navigate_to_pose"
        )

        # -------------------------------------------------
        # rooms.yaml 읽기
        # -------------------------------------------------

        with open(
            ROOM_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            self.room_data = yaml.safe_load(f)

        # -------------------------------------------------
        # 상태 변수
        # -------------------------------------------------

        # 직전에 순찰한 병실
        self.last_room = None

        # 현재 이동 중인 병실
        self.current_room_number = None

        # rooms.yaml의 room1 / room2...
        self.current_room_key = None

        # hall 또는 inside
        self.stage = None

        # 현재 병실 위험도
        self.current_room_score = 0

        # 현재 최고위험 환자
        self.current_patient = None

        # -------------------------------------------------
        # 순찰 시작
        # -------------------------------------------------

        self.get_logger().info(
            "================================"
        )

        self.get_logger().info(
            "우선순위 기반 랜덤 순찰 시작"
        )

        self.get_logger().info(
            "================================"
        )

        self.get_logger().info(
            "Nav2 서버 대기 중..."
        )

        self.nav_client.wait_for_server()

        self.get_logger().info(
            "Nav2 연결 완료"
        )

        # 첫 병실 선택
        self.start_next_patrol()


    # =====================================================
    # 현재 우선순위 출력
    # =====================================================

    def print_priority(
        self,
        priority_rooms
    ):

        self.get_logger().info(
            "--------------------------------"
        )

        self.get_logger().info(
            "현재 병실 우선순위"
        )

        self.get_logger().info(
            "--------------------------------"
        )

        rank_weights = [
            40,
            30,
            20,
            10
        ]

        for rank, info in enumerate(
            priority_rooms,
            start=1
        ):

            if rank <= len(
                rank_weights
            ):
                weight = (
                    rank_weights[
                        rank - 1
                    ]
                )
            else:
                weight = 10

            self.get_logger().info(
                f'{rank}순위 | '
                f'{info["room"]}호 | '
                f'{info["score"]}점 | '
                f'{info["patient"]} | '
                f'가중치={weight}'
            )


    # =====================================================
    # 다음 병실 선택
    # =====================================================

    def select_next_room(self):

        # -------------------------------------------------
        # 매 선택 시 patients.yaml 다시 읽음
        #
        # 환자 정보가 변경되면
        # 다음 순찰부터 자동 반영됨.
        # -------------------------------------------------

        priority_rooms = (
            calculate_room_priority()
        )

        self.print_priority(
            priority_rooms
        )

        # 순위별 기본 가중치
        rank_weights = [
            40,
            30,
            20,
            10
        ]

        candidates = []

        weights = []

        # -------------------------------------------------
        # 후보 병실 생성
        # -------------------------------------------------

        for index, room_info in enumerate(
            priority_rooms
        ):

            room_number = str(
                room_info["room"]
            )

            # ---------------------------------------------
            # 바로 전에 순찰한 병실은 제외
            # ---------------------------------------------

            if (
                self.last_room is not None
                and
                room_number
                == self.last_room
            ):

                continue

            candidates.append(
                room_info
            )

            # ---------------------------------------------
            # 순위별 가중치
            # ---------------------------------------------

            if index < len(
                rank_weights
            ):

                weight = (
                    rank_weights[index]
                )

            else:

                weight = 10

            weights.append(
                weight
            )

        # -------------------------------------------------
        # 혹시 후보가 없는 경우
        # -------------------------------------------------

        if not candidates:

            candidates = (
                priority_rooms
            )

            weights = []

            for index in range(
                len(candidates)
            ):

                if index < len(
                    rank_weights
                ):

                    weights.append(
                        rank_weights[
                            index
                        ]
                    )

                else:

                    weights.append(
                        10
                    )

        # -------------------------------------------------
        # 가중 랜덤 선택
        # -------------------------------------------------

        selected = random.choices(
            candidates,
            weights=weights,
            k=1
        )[0]

        return selected


    # =====================================================
    # 새로운 병실 순찰 시작
    # =====================================================

    def start_next_patrol(self):

        # -------------------------------------------------
        # 가중 랜덤 방식으로 다음 병실 선택
        # -------------------------------------------------

        room_info = (
            self.select_next_room()
        )

        room_number = str(
            room_info["room"]
        )

        # -------------------------------------------------
        # 101 → room1
        # -------------------------------------------------

        room_key = ROOM_KEY_MAP.get(
            room_number
        )

        if room_key is None:

            self.get_logger().error(
                f"{room_number}호 "
                "ROOM_KEY_MAP 없음"
            )

            return

        self.current_room_number = (
            room_number
        )

        self.current_room_key = (
            room_key
        )

        self.current_room_score = (
            room_info["score"]
        )

        self.current_patient = (
            room_info["patient"]
        )

        # -------------------------------------------------
        # 첫 목적지는 hall
        # -------------------------------------------------

        self.stage = "hall"

        hall_pose = (
            self.room_data["rooms"]
            [room_key]
            ["hall"]
        )

        self.get_logger().info(
            ""
        )

        self.get_logger().info(
            "================================"
        )

        self.get_logger().info(
            f'다음 순찰 병실 : '
            f'{room_number}호'
        )

        self.get_logger().info(
            f'병실 위험도 : '
            f'{self.current_room_score}점'
        )

        self.get_logger().info(
            f'최고 위험 환자 : '
            f'{self.current_patient}'
        )

        self.get_logger().info(
            "================================"
        )

        self.get_logger().info(
            f'{room_number}호 '
            'Hall 이동 시작'
        )

        self.send_goal(
            hall_pose["x"],
            hall_pose["y"],
            hall_pose["z"],
            hall_pose["w"]
        )


    # =====================================================
    # Nav2 Goal 전송
    # =====================================================

    def send_goal(
        self,
        x,
        y,
        z,
        w
    ):

        goal = (
            NavigateToPose.Goal()
        )

        # -------------------------------------------------
        # map 좌표계
        # -------------------------------------------------

        goal.pose.header.frame_id = (
            "map"
        )

        goal.pose.header.stamp = (
            self.get_clock()
            .now()
            .to_msg()
        )

        # -------------------------------------------------
        # 위치
        # -------------------------------------------------

        goal.pose.pose.position.x = (
            float(x)
        )

        goal.pose.pose.position.y = (
            float(y)
        )

        goal.pose.pose.position.z = (
            0.0
        )

        # -------------------------------------------------
        # orientation
        #
        # rooms.yaml의 z, w는 quaternion 값
        # -------------------------------------------------

        goal.pose.pose.orientation.x = (
            0.0
        )

        goal.pose.pose.orientation.y = (
            0.0
        )

        goal.pose.pose.orientation.z = (
            float(z)
        )

        goal.pose.pose.orientation.w = (
            float(w)
        )

        # -------------------------------------------------
        # Goal 전송
        # -------------------------------------------------

        future = (
            self.nav_client
            .send_goal_async(
                goal,
                feedback_callback=
                self.feedback_callback
            )
        )

        future.add_done_callback(
            self.goal_response_callback
        )


    # =====================================================
    # Goal 수락 여부
    # =====================================================

    def goal_response_callback(
        self,
        future
    ):

        try:

            goal_handle = (
                future.result()
            )

        except Exception as e:

            self.get_logger().error(
                f"Goal 전송 오류: {e}"
            )

            self.start_next_patrol()

            return

        # -------------------------------------------------
        # Goal 거부
        # -------------------------------------------------

        if not goal_handle.accepted:

            self.get_logger().error(
                f'{self.current_room_number}호 '
                f'{self.stage} '
                'Goal rejected'
            )

            # 다음 병실 선택
            self.last_room = (
                self.current_room_number
            )

            self.start_next_patrol()

            return

        # -------------------------------------------------
        # Goal 수락
        # -------------------------------------------------

        self.get_logger().info(
            f'{self.current_room_number}호 '
            f'{self.stage} '
            'Goal accepted'
        )

        result_future = (
            goal_handle
            .get_result_async()
        )

        result_future.add_done_callback(
            self.result_callback
        )


    # =====================================================
    # Nav2 주행 Feedback
    # =====================================================

    def feedback_callback(
        self,
        feedback_msg
    ):

        distance = (
            feedback_msg
            .feedback
            .distance_remaining
        )

        self.get_logger().info(
            f'{self.current_room_number}호 '
            f'{self.stage}까지 '
            f'{distance:.2f} m'
        )


    # =====================================================
    # Nav2 이동 결과
    # =====================================================

    def result_callback(
        self,
        future
    ):

        try:

            wrapped_result = (
                future.result()
            )

        except Exception as e:

            self.get_logger().error(
                f"Navigation 결과 오류: {e}"
            )

            self.last_room = (
                self.current_room_number
            )

            self.start_next_patrol()

            return

        status = (
            wrapped_result.status
        )

        # -------------------------------------------------
        # 이동 실패
        # -------------------------------------------------

        if (
            status
            != GoalStatus.STATUS_SUCCEEDED
        ):

            self.get_logger().warning(
                f'{self.current_room_number}호 '
                f'{self.stage} '
                f'이동 실패 '
                f'status={status}'
            )

            # 실패한 병실 바로 재시도 방지
            self.last_room = (
                self.current_room_number
            )

            # 다음 병실 선택
            self.start_next_patrol()

            return

        # =================================================
        # Hall 도착
        # =================================================

        if self.stage == "hall":

            self.get_logger().info(
                f'{self.current_room_number}호 '
                'Hall 도착'
            )

            # -------------------------------------------------
            # 다음 목적지는 inside
            # -------------------------------------------------

            self.stage = "inside"

            inside_pose = (
                self.room_data["rooms"]
                [self.current_room_key]
                ["inside"]
            )

            self.get_logger().info(
                f'{self.current_room_number}호 '
                'Inside 이동 시작'
            )

            self.send_goal(
                inside_pose["x"],
                inside_pose["y"],
                inside_pose["z"],
                inside_pose["w"]
            )

            return

        # =================================================
        # Inside 도착
        # =================================================

        if self.stage == "inside":

            self.get_logger().info(
                ""
            )

            self.get_logger().info(
                "================================"
            )

            self.get_logger().info(
                f'{self.current_room_number}호 '
                '병실 내부 도착'
            )

            self.get_logger().info(
                f'위험도 : '
                f'{self.current_room_score}점'
            )

            self.get_logger().info(
                "낙상 환자 검사 수행"
            )

            self.get_logger().info(
                "================================"
            )

            # =================================================
            # 나중에 여기서
            #
            # YOLO Pose 낙상감지 실행
            #
            # 예:
            #
            # self.check_fall()
            #
            # =================================================


            # -------------------------------------------------
            # 현재 병실을 마지막 방문 병실로 저장
            # -------------------------------------------------

            self.last_room = (
                self.current_room_number
            )

            # -------------------------------------------------
            # 다음 병실 랜덤 선택
            # -------------------------------------------------

            self.start_next_patrol()


# =========================================================
# main
# =========================================================

def main(args=None):

    rclpy.init(
        args=args
    )

    node = (
        PriorityPatrol()
    )

    try:

        rclpy.spin(
            node
        )

    except KeyboardInterrupt:

        node.get_logger().info(
            "순찰 종료"
        )

    finally:

        node.destroy_node()

        rclpy.shutdown()


if __name__ == "__main__":

    main()
