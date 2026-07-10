"""샘플 동영상 생성기 — 불꽃놀이 장면의 짧은 mp4."""
import random
from pathlib import Path

import cv2
import numpy as np

OUT = Path(__file__).resolve().parent.parent / "photos" / "fireworks_festival.mp4"
W, H, FPS, SECONDS = 640, 480, 15, 4

random.seed(7)


def main():
    writer = cv2.VideoWriter(
        str(OUT), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H)
    )
    bursts = []
    for f in range(FPS * SECONDS):
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        frame[:, :] = (40, 15, 10)  # 어두운 밤하늘 (BGR)
        # 도시 스카이라인
        cv2.rectangle(frame, (0, H - 60), (W, H), (30, 25, 20), -1)
        if f % 10 == 0:
            bursts.append({
                "x": random.randint(80, W - 80),
                "y": random.randint(60, H // 2),
                "age": 0,
                "color": random.choice([(80, 80, 255), (80, 255, 255), (255, 120, 80), (180, 80, 255)]),
            })
        for b in bursts:
            r = 5 + b["age"] * 6
            for ang in range(0, 360, 20):
                px = int(b["x"] + r * np.cos(np.radians(ang)))
                py = int(b["y"] + r * np.sin(np.radians(ang)))
                if 0 <= px < W and 0 <= py < H:
                    cv2.circle(frame, (px, py), 3, b["color"], -1)
            b["age"] += 1
        bursts = [b for b in bursts if b["age"] < 12]
        writer.write(frame)
    writer.release()
    print("created", OUT)


if __name__ == "__main__":
    main()
