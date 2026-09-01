#!/usr/bin/env python3

import os
import yaml


# ==========================================
# patients.yaml 경로
# ==========================================
PATIENT_FILE = os.path.expanduser(
    "~/ros2-patrol-fall-detection-v2/"
    "src/my_patrol/config/patients.yaml"
)


# ==========================================
# 낙상 위험도 점수
# ==========================================
def get_fall_risk_score(risk):

    scores = {
        "매우 높음": 50,
        "높음": 35,
        "보통": 20,
        "낮음": 5
    }

    return scores.get(risk, 0)


# ==========================================
# 나이 점수
# ==========================================
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


# ==========================================
# 질환 점수
# ==========================================
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


# ==========================================
# 환자 종합점수 계산
# ==========================================
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

    total_score = (
        fall_score
        + age_score
        + disease_score
    )

    return total_score


# ==========================================
# main
# ==========================================
def main():

    # --------------------------------------
    # patients.yaml 읽기
    # --------------------------------------
    with open(
        PATIENT_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        data = yaml.safe_load(f)

    patients = data["patients"]

    # 병실별 환자 정보를 저장
    rooms = {}

    print()
    print("====================================")
    print("          환자별 위험도")
    print("====================================")

    # --------------------------------------
    # 환자별 종합점수 계산
    # --------------------------------------
    for patient in patients:

        score = calculate_patient_score(
            patient
        )

        room = str(
            patient["room"]
        )

        print(
            f'{patient["name"]} | '
            f'{room}호 | '
            f'나이: {patient["age"]} | '
            f'낙상위험: {patient["fall_risk"]} | '
            f'종합점수: {score}점'
        )

        # 처음 등장한 병실이면 리스트 생성
        if room not in rooms:
            rooms[room] = []

        # 환자 이름과 점수를 같이 저장
        rooms[room].append({
            "name": patient["name"],
            "score": score
        })

    # ======================================
    # 병실별 점수 계산
    #
    # 병실 점수 =
    # 해당 병실 환자들의 종합점수 중 최대값
    # ======================================

    room_priority = {}

    for room, patient_list in rooms.items():

        # 해당 병실의 모든 환자 점수
        scores = [
            patient["score"]
            for patient in patient_list
        ]

        # 가장 높은 환자 점수
        room_score = max(scores)

        # 가장 위험한 환자 찾기
        highest_patient = max(
            patient_list,
            key=lambda x: x["score"]
        )

        room_priority[room] = {
            "score": room_score,
            "patient": highest_patient["name"]
        }

    # ======================================
    # 점수가 높은 병실부터 정렬
    # ======================================

    sorted_rooms = sorted(
        room_priority.items(),
        key=lambda x: x[1]["score"],
        reverse=True
    )

    print()
    print("====================================")
    print("          병실 순찰 우선순위")
    print("====================================")

    for rank, (room, info) in enumerate(
        sorted_rooms,
        start=1
    ):

        print(
            f'{rank}순위 : '
            f'{room}호 | '
            f'{info["score"]}점 | '
            f'최고 위험 환자: {info["patient"]}'
        )


if __name__ == "__main__":
    main()
