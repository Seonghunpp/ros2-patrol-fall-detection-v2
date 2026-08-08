#!/usr/bin/env python3
"""Generate draft YOLO Pose labels and preview images from a folder of photos.

Place this script next to:
  - yolov8n-pose.pt
  - pose_images/   (your photos)

Run:
  python3 auto_label_pose.py

Outputs:
  pose_autolabel/images/   copied source images
  pose_autolabel/labels/   YOLO Pose draft labels
  pose_autolabel/preview/  annotated preview images
"""

from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import torch
from ultralytics import YOLO


BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "runs/pose/printed_patient_v1/weights/best.pt"
INPUT_DIR = BASE_DIR / "pose_images_retry"
OUTPUT_DIR = BASE_DIR / "pose_autolabel_retry"

BOX_CONF = 0.15
KEYPOINT_CONF = 0.25
IMAGE_SIZE = 640
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def make_label_line(
    result,
    person_index: int,
    class_id: int,
) -> str:
    """Convert one predicted patient to a YOLO Pose label line."""
    box = result.boxes.xywhn[person_index].detach().cpu().tolist()

    keypoints_xy = result.keypoints.xyn[person_index].detach().cpu().tolist()
    keypoints_conf = result.keypoints.conf[person_index].detach().cpu().tolist()

    values: list[str] = [str(class_id)]
    values.extend(f"{value:.6f}" for value in box)

    for (x, y), confidence in zip(keypoints_xy, keypoints_conf):
        if confidence >= KEYPOINT_CONF:
            values.extend((f"{x:.6f}", f"{y:.6f}", "2"))
        else:
            values.extend(("0.000000", "0.000000", "0"))

    return " ".join(values)


def main() -> None:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model not found: {MODEL_PATH}")
    if not INPUT_DIR.exists():
        raise FileNotFoundError(
            f"Image folder not found: {INPUT_DIR}\n"
            "Create a pose_images folder next to this script and put your photos in it."
        )

    images_out = OUTPUT_DIR / "images"
    labels_out = OUTPUT_DIR / "labels"
    previews_out = OUTPUT_DIR / "preview"
    for folder in (images_out, labels_out, previews_out):
        folder.mkdir(parents=True, exist_ok=True)

    image_paths = sorted(
        path for path in INPUT_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not image_paths:
        raise RuntimeError(f"No images found in: {INPUT_DIR}")

    device: int | str = 0 if torch.cuda.is_available() else "cpu"
    print(f"Model: {MODEL_PATH}")
    print(f"Images: {len(image_paths)}")
    print(f"Device: {device}")

    model = YOLO(str(MODEL_PATH))
    total_people = 0
    empty_images = 0

    for index, image_path in enumerate(image_paths, start=1):
        category = image_path.parent.name.lower()

        if category == "person":
            class_id = 0
        elif category == "fall_person":
            class_id = 1
        elif category == "empty":
            class_id = None
        else:
            print(f"Skip unknown folder: {image_path}")
            continue

        results = model.predict(
            source=str(image_path),
            conf=BOX_CONF,
            iou=0.20,
            imgsz=IMAGE_SIZE,
            device=device,
            verbose=False,
        )
        result = results[0]

        output_name = f"{category}_{image_path.name}"
        output_stem = f"{category}_{image_path.stem}"

        output_image_path = images_out / output_name
        shutil.copy2(image_path, output_image_path)

        label_path = labels_out / f"{output_stem}.txt"
        lines: list[str] = []

        if class_id is None:
            empty_images += 1

        elif (
            result.boxes is not None
            and result.keypoints is not None
            and len(result.boxes) > 0
            and result.keypoints.conf is not None
        ):
            person_count = min(len(result.boxes), len(result.keypoints.xyn))

            for person_index in range(person_count):
                lines.append(
                    make_label_line(
                        result,
                        person_index,
                        class_id,
                    )
                )

            total_people += person_count

        else:
            empty_images += 1

        label_path.write_text("\n".join(lines), encoding="utf-8")

        preview = result.plot(boxes=True, labels=True)
        cv2.imwrite(str(previews_out / output_name), preview)

        print(f"[{index:03d}/{len(image_paths):03d}] {image_path.name}: {len(lines)} person(s)")

    print("\nDone")
    print(f"Detected people: {total_people}")
    print(f"Images with no detection: {empty_images}")
    print(f"Preview folder: {previews_out}")
    print(f"Label folder: {labels_out}")
    print("These are draft labels. Review preview images before training.")


if __name__ == "__main__":
    main()
