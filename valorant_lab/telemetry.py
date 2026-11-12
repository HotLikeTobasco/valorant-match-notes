"""Optional offline mouse telemetry import. Does not capture input or contact a game."""

import json
import math
from pathlib import Path
from pydantic import BaseModel, Field


class MouseSample(BaseModel):
    time: float = Field(ge=0, allow_inf_nan=False)
    dx: float = Field(allow_inf_nan=False)
    dy: float = Field(allow_inf_nan=False)
    fired: bool = False


def load_telemetry(path: Path, video_offset=0):
    """Convert externally recorded timestamps to video time; offset must be calibrated by user."""
    if not math.isfinite(video_offset):
        raise ValueError("video_offset must be finite")
    previous = -1
    with open(path, encoding="utf-8") as source:
        for line in source:
            sample = MouseSample.model_validate_json(line)
            if sample.time < previous:
                raise ValueError("Mouse telemetry must be sorted by timestamp")
            previous = sample.time
            video_time = sample.time + video_offset
            if video_time >= 0:
                yield sample.model_copy(update={"time": video_time})


def mouse_distance(samples, degrees_per_count=None):
    if degrees_per_count is not None and (
        not math.isfinite(degrees_per_count) or degrees_per_count <= 0
    ):
        raise ValueError("degrees_per_count must be finite and positive")
    distance = sum(math.hypot(s.dx, s.dy) for s in samples)
    return {
        "mouse_path_counts": distance,
        "angular_path_degrees": (
            distance * degrees_per_count if degrees_per_count is not None else None
        ),
        "basis": "external telemetry; angular path requires user-supplied calibration, not video inference",
    }
