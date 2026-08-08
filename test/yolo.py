from pathlib import Path

from ultralytics import YOLO


BASE_DIR = Path("/home/kjw1796/Desktop/yolo_test/test")

MODEL_PATH = Path(
    "/home/kjw1796/Desktop/yolo_test/test/yolov8n-pose.pt"
)
DATA_YAML = Path(
    "/home/kjw1796/Desktop/yolo_test/test/pose_dataset/data.yaml"
)

PROJECT_PATH = BASE_DIR / "runs/pose"
RUN_NAME = "printed_patient_v2"

EPOCHS = 100
IMAGE_SIZE = 640
BATCH_SIZE = 2
DEVICE = 0
WORKERS = 4
PATIENCE = 20


def main():
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"모델 파일 없음: {MODEL_PATH}")

    if not DATA_YAML.exists():
        raise FileNotFoundError(f"데이터 설정 파일 없음: {DATA_YAML}")

    model = YOLO(str(MODEL_PATH))

    model.train(
        data=str(DATA_YAML),
        epochs=EPOCHS,
        imgsz=IMAGE_SIZE,
        batch=BATCH_SIZE,
        device=DEVICE,
        workers=WORKERS,
        patience=PATIENCE,
        project=str(PROJECT_PATH),
        name=RUN_NAME,
        exist_ok=False,
        save=True,
    )


if __name__ == "__main__":
    main()