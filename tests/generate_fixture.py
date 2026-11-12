"""Generate a clearly labelled video for integration/UI checks, not gameplay validation."""

import json
import sys
from pathlib import Path
import cv2
import numpy as np


def generate(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "SYNTHETIC-pipeline-check.mp4"
    width, height, fps, duration = 960, 540, 30, 12
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    assert writer.isOpened()
    for n in range(fps * duration):
        frame = np.full((height, width, 3), (24, 19, 15), dtype=np.uint8)
        cv2.putText(
            frame,
            "SYNTHETIC PIPELINE TEST",
            (45, 65),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (185, 225, 210),
            2,
        )
        cv2.putText(
            frame,
            "Not VALORANT footage - no gameplay accuracy claim",
            (45, 105),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (140, 155, 170),
            1,
        )
        t = n / fps
        x = int(width * (0.5 + 0.08 * np.sin(t)))
        y = int(height * 0.38)
        cv2.circle(frame, (x, y), 12, (140, 220, 180), -1)
        cv2.line(
            frame,
            (width // 2 - 8, height // 2),
            (width // 2 + 8, height // 2),
            (235, 240, 245),
            1,
        )
        cv2.line(
            frame,
            (width // 2, height // 2 - 8),
            (width // 2, height // 2 + 8),
            (235, 240, 245),
            1,
        )
        cv2.putText(
            frame,
            f"{t:05.2f}s",
            (45, 485),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (150, 170, 180),
            1,
        )
        writer.write(frame)
    writer.release()
    observations = []
    for base in (1, 4, 7):
        for offset in (0, 0.2, 0.4, 0.6, 0.8):
            t = base + offset
            observations.append(
                dict(
                    time=t,
                    head_x=int(width * (0.5 + 0.08 * np.sin(t))) / width,
                    head_y=int(height * 0.38) / height,
                    crosshair_x=0.5,
                    crosshair_y=0.5,
                    crosshair_source="manual",
                    target_id=f"fixture-{base}",
                    confidence=1,
                    source="manual",
                )
            )
    events = [
        dict(
            time=0,
            kind="round_start",
            note="Synthetic test boundary",
            source="manual",
            confidence=1,
        )
    ]
    events += [
        dict(
            time=base + 0.6,
            kind="shot",
            target_id=f"fixture-{base}",
            note="Synthetic shot label",
            source="manual",
            confidence=1,
        )
        for base in (1, 4, 7)
    ]
    evidence = dict(
        schema_version=1,
        map_name="Synthetic fixture",
        note="Generated geometry, not gameplay.",
        events=events,
        observations=observations,
    )
    Path(str(video) + ".evidence.json").write_text(json.dumps(evidence, indent=2))
    return video


if __name__ == "__main__":
    print(generate(sys.argv[1]))
