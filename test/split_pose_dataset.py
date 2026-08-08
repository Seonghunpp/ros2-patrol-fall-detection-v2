from pathlib import Path
import random
import shutil


BASE_DIR = Path(__file__).resolve().parent

SOURCE_DIR = BASE_DIR / "pose_autolabel"
OUTPUT_DIR = BASE_DIR / "pose_dataset"

IMAGE_DIR = SOURCE_DIR / "images"
LABEL_DIR = SOURCE_DIR / "labels"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

VAL_RATIO = 0.2
RANDOM_SEED = 42


def get_category(filename: str):
    # retry 작업 중 생성된 중복 파일 제외
    if filename.startswith("fall_person_fall_person_"):
        return None

    if filename.startswith("fall_person_person_"):
        return None

    if filename.startswith("fall_person_"):
        return "fall_person"

    if filename.startswith("person_"):
        return "person"

    if filename.startswith("empty_"):
        return "empty"

    return None


if not IMAGE_DIR.is_dir():
    raise FileNotFoundError(f"이미지 폴더 없음: {IMAGE_DIR}")

if not LABEL_DIR.is_dir():
    raise FileNotFoundError(f"라벨 폴더 없음: {LABEL_DIR}")


groups = {
    "person": [],
    "fall_person": [],
    "empty": [],
}


for image_path in sorted(IMAGE_DIR.iterdir()):
    if not image_path.is_file():
        continue

    if image_path.suffix.lower() not in IMAGE_EXTENSIONS:
        continue

    category = get_category(image_path.name)

    if category is None:
        continue

    label_path = LABEL_DIR / f"{image_path.stem}.txt"

    if not label_path.exists():
        raise FileNotFoundError(
            f"이미지와 짝이 맞는 라벨 없음: {image_path.name}"
        )

    text = label_path.read_text(encoding="utf-8").strip()

    # empty 이미지는 반드시 빈 라벨이어야 함
    if category == "empty":
        if text:
            raise ValueError(
                f"empty 라벨에 내용이 있음: {label_path.name}"
            )

    # person, fall_person은 라벨 내용이 있어야 함
    else:
        if not text:
            raise ValueError(
                f"사람 이미지 라벨이 비어 있음: {label_path.name}"
            )

        expected_class = {
            "person": "0",
            "fall_person": "1",
        }[category]

        for line_number, line in enumerate(
            text.splitlines(),
            start=1,
        ):
            values = line.split()

            # class 1개 + bbox 4개 + 관절 17개 × 3개 = 56개
            if len(values) != 56:
                raise ValueError(
                    f"필드 수 오류: {label_path.name}, "
                    f"{line_number}번째 줄, "
                    f"현재 {len(values)}개, 정상 56개"
                )

            class_id = values[0]

            if class_id != expected_class:
                raise ValueError(
                    f"클래스 번호 오류: {label_path.name}, "
                    f"{line_number}번째 줄, "
                    f"현재 class={class_id}, "
                    f"정상 class={expected_class}"
                )

    groups[category].append(
        (image_path, label_path)
    )


# 기존 pose_dataset 삭제 후 새로 생성
if OUTPUT_DIR.exists():
    shutil.rmtree(OUTPUT_DIR)


for split in ("train", "val"):
    (OUTPUT_DIR / "images" / split).mkdir(
        parents=True,
        exist_ok=True,
    )

    (OUTPUT_DIR / "labels" / split).mkdir(
        parents=True,
        exist_ok=True,
    )


random.seed(RANDOM_SEED)

total_train = 0
total_val = 0


# person, fall_person, empty를 각각 80:20으로 분리
for category, items in groups.items():
    random.shuffle(items)

    if len(items) >= 2:
        val_count = max(
            1,
            round(len(items) * VAL_RATIO),
        )
    else:
        val_count = 0

    val_items = items[:val_count]
    train_items = items[val_count:]

    for split, split_items in (
        ("train", train_items),
        ("val", val_items),
    ):
        for image_path, label_path in split_items:
            shutil.copy2(
                image_path,
                OUTPUT_DIR
                / "images"
                / split
                / image_path.name,
            )

            shutil.copy2(
                label_path,
                OUTPUT_DIR
                / "labels"
                / split
                / label_path.name,
            )

    total_train += len(train_items)
    total_val += len(val_items)

    print(
        f"{category}: "
        f"전체 {len(items)}, "
        f"train {len(train_items)}, "
        f"val {len(val_items)}"
    )


yaml_text = f"""path: {OUTPUT_DIR}

train: images/train
val: images/val

kpt_shape: [17, 3]
flip_idx: [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]

names:
  0: person
  1: fall_person
"""

(OUTPUT_DIR / "data.yaml").write_text(
    yaml_text,
    encoding="utf-8",
)


print()
print(f"train 전체: {total_train}")
print(f"val 전체: {total_val}")
print(f"전체 데이터: {total_train + total_val}")
print(f"완료: {OUTPUT_DIR}")
print(f"YAML: {OUTPUT_DIR / 'data.yaml'}")