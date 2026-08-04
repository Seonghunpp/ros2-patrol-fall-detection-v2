from ultralytics import YOLO
from pathlib import Path
import cv2

model = YOLO("yolov8n-pose.pt")

images = [
    "/home/kjw1796/Downloads/image-1785484909179.jpg",
    "/home/kjw1796/Downloads/image-1785484919232.jpg",
    "/home/kjw1796/Downloads/image-1785484927041.jpg",
    "/home/kjw1796/Downloads/image-1785484939427.jpg",
    "/home/kjw1796/Downloads/image-1785484947886.jpg",
]

save_dir = Path("results")
save_dir.mkdir(exist_ok=True)

results = model(images, device=0, conf=0.25)

for i, result in enumerate(results, 1):
    cv2.imwrite(str(save_dir / f"result_{i}.jpg"), result.plot())