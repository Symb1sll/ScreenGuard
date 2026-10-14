from pathlib import Path
import cv2
import numpy as np

BASE = Path(__file__).resolve().parents[1] / 'src/task2_video_panel'
OUT = BASE / 'sample_media' / 'demo_city_screen.mp4'
OUT.parent.mkdir(parents=True, exist_ok=True)

fourcc = cv2.VideoWriter_fourcc(*'mp4v')
writer = cv2.VideoWriter(str(OUT), fourcc, 8.0, (640, 360))
scenes = [
    ((40, 70, 140), 'NORMAL PUBLIC INFO', 24),
    ((40, 190, 50), 'SUSPECTED GAMBLING AD', 24),
    ((30, 30, 220), 'SUSPECTED VIOLENCE / BLOOD', 24),
    ((190, 40, 190), 'SUSPECTED VULGAR CONTENT', 24),
    ((245, 245, 245), 'ID CARD / PRIVACY INFO', 24),
]
for color, text, n in scenes:
    for i in range(n):
        frame = np.full((360, 640, 3), color, dtype=np.uint8)
        cv2.rectangle(frame, (30, 60), (610, 300), (255, 255, 255), 3)
        cv2.putText(frame, text, (55, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(frame, f'frame {i+1}', (55, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2, cv2.LINE_AA)
        writer.write(frame)
writer.release()
print(OUT)
