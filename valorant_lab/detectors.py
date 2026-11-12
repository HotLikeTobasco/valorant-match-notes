"""Replaceable detectors. Scores are model/similarity scores, NOT calibrated probabilities."""

from pathlib import Path
from typing import Protocol
import cv2
import numpy as np

from .schema import Event, Observation


class Detector(Protocol):
    def detect(
        self, frame: np.ndarray, time: float
    ) -> tuple[list[Event], list[Observation]]: ...


class SceneDetector:
    def __init__(self, threshold=0.35):
        self.previous = None
        self.threshold = threshold

    def detect(self, frame, time):
        grey = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 36)).astype(
            float
        )
        score = (
            0
            if self.previous is None
            else float(np.abs(grey - self.previous).mean() / 255)
        )
        self.previous = grey
        events = []
        if score >= self.threshold:
            events.append(
                Event(
                    time=time,
                    kind="scene_change",
                    confidence=score,
                    source="frame_difference",
                    note="Large image change. Not a confirmed round, kill, death or match boundary.",
                )
            )
        return events, []


class TemplateDetector:
    """Explicit per-HUD templates in normalised ROIs; disabled until configured."""

    def __init__(self, templates):
        self.templates = []
        self.last = {}
        for entry in templates:
            image = cv2.imread(
                str(Path(entry["path"]).expanduser()), cv2.IMREAD_GRAYSCALE
            )
            if image is None or image.std() < 1:
                raise ValueError("HUD templates must be readable, non-uniform images")
            x, y, w, h = entry["roi"]
            if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1 or y + h > 1:
                raise ValueError("template ROI must fit the normalised frame")
            Event(time=0, kind=entry["kind"])
            threshold = float(entry.get("threshold", 0.92))
            if not 0 < threshold <= 1 or entry.get("cooldown", 3) < 0:
                raise ValueError("invalid template threshold/cooldown")
            self.templates.append((entry, image))

    def detect(self, frame, time):
        result = []
        height, width = frame.shape[:2]
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for index, (entry, image) in enumerate(self.templates):
            x, y, w, h = entry["roi"]
            crop = grey[
                int(y * height) : int((y + h) * height),
                int(x * width) : int((x + w) * width),
            ]
            if crop.shape[0] < image.shape[0] or crop.shape[1] < image.shape[1]:
                raise ValueError(
                    "HUD template is larger than its ROI at analysis resolution"
                )
            score = float(cv2.matchTemplate(crop, image, cv2.TM_CCOEFF_NORMED).max())
            if score >= entry.get("threshold", 0.92) and time - self.last.get(
                index, -1e9
            ) >= entry.get("cooldown", 3):
                self.last[index] = time
                result.append(
                    Event(
                        time=time,
                        kind=entry["kind"],
                        confidence=score,
                        source="hud_template_unvalidated",
                        note="Template candidate; review in VOD. Score is similarity, not probability.",
                    )
                )
        return result, []


class ONNXHeadDetector:
    """Adapter contract: RGB float32 NCHW 640x640 -> Nx6 normalised xyxy, score, class.

    Class 0 must be a VALORANT enemy head, not a generic person. No weights bundled.
    Target IDs below are short proximity tracks, not persistent player identities.
    """

    def __init__(self, path):
        import onnxruntime as ort

        available = ort.get_available_providers()
        providers = [
            p
            for p in (
                "CUDAExecutionProvider",
                "CoreMLExecutionProvider",
                "CPUExecutionProvider",
            )
            if p in available
        ]
        self.session = ort.InferenceSession(
            str(Path(path).expanduser()), providers=providers
        )
        self.input = self.session.get_inputs()[0].name
        self.tracks = {}
        self.counter = 0

    def detect(self, frame, time):
        image = cv2.cvtColor(cv2.resize(frame, (640, 640)), cv2.COLOR_BGR2RGB)
        tensor = np.transpose(image.astype(np.float32) / 255, (2, 0, 1))[None]
        output = np.asarray(self.session.run(None, {self.input: tensor})[0])
        if output.ndim != 2 or output.shape[1] != 6 or not np.isfinite(output).all():
            raise ValueError(
                "Model must return finite Nx6 head detections; see adapter contract"
            )
        observations = []
        used = set()
        for x1, y1, x2, y2, score, label in output:
            if label != 0 or score < 0.8:
                continue
            if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1 and score <= 1):
                raise ValueError("Model returned an invalid normalised box")
            x, y = float((x1 + x2) / 2), float((y1 + y2) / 2)
            candidates = [
                (np.hypot(x - tx, y - ty), key)
                for key, (tx, ty, last) in self.tracks.items()
                if key not in used and 0 <= time - last <= 0.5
            ]
            distance, key = min(candidates, default=(1, ""))
            if distance > 0.08:
                self.counter += 1
                key = f"head-{self.counter}"
            used.add(key)
            self.tracks[key] = (x, y, time)
            observations.append(
                Observation(
                    time=time,
                    head_x=x,
                    head_y=y,
                    confidence=float(score),
                    target_id=key,
                    source="onnx",
                )
            )
        self.tracks = {k: v for k, v in self.tracks.items() if time - v[2] <= 0.5}
        return [], observations
