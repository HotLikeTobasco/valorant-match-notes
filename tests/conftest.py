from pathlib import Path
import cv2
import numpy as np
import pytest
from valorant_lab.config import Config


@pytest.fixture
def recording(tmp_path):
    root = tmp_path / "recordings"
    root.mkdir()
    path = root / "synthetic.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 180))
    assert writer.isOpened()
    for n in range(30):
        writer.write(np.full((180, 320, 3), 0 if n < 10 else 255, dtype=np.uint8))
    writer.release()
    return path


@pytest.fixture
def config(recording, tmp_path):
    return Config(
        recordings_dir=recording.parent,
        data_dir=tmp_path / "data",
        settle_seconds=0,
        chunk_seconds=1,
        sample_fps=10,
        analysis_width=320,
    )
