from dataclasses import asdict, dataclass, field
from pathlib import Path
import hashlib
import json
import math

from . import PIPELINE_VERSION


@dataclass
class Config:
    recordings_dir: Path = field(default_factory=lambda: Path.home() / "Videos" / "VALORANT")
    data_dir: Path = Path(".lab")
    poll_seconds: float = 5
    settle_seconds: float = 30
    sample_fps: float = 5
    analysis_width: int = 640
    chunk_seconds: float = 60
    scene_threshold: float = .35
    hwaccel: str = "none"
    templates: list = field(default_factory=list)
    onnx_model: str | None = None

    def __post_init__(self):
        self.recordings_dir = Path(self.recordings_dir).expanduser().resolve()
        self.data_dir = Path(self.data_dir).expanduser().resolve()
        for name in ("poll_seconds", "settle_seconds", "sample_fps", "chunk_seconds", "scene_threshold"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if not 0 < self.sample_fps <= 30 or not 160 <= self.analysis_width <= 1920:
            raise ValueError("sample_fps must be (0,30]; analysis_width must be 160..1920")
        if self.poll_seconds < .1 or self.settle_seconds < 0 or self.chunk_seconds < 1:
            raise ValueError("invalid polling, settling or chunk interval")
        if not 0 < self.scene_threshold <= 1:
            raise ValueError("scene_threshold must be (0,1]")
        if self.hwaccel not in {"none", "auto", "videotoolbox", "cuda", "d3d11va", "vaapi"}:
            raise ValueError("unsupported hardware decoder")
        if self.data_dir == self.recordings_dir or self.recordings_dir in self.data_dir.parents:
            raise ValueError("data_dir must be outside recordings_dir (avoids ingesting proxies)")

    @classmethod
    def load(cls, path=None):
        return cls(**json.loads(Path(path).read_text())) if path else cls()

    def signature(self):
        settings = asdict(self)
        for key in ("recordings_dir", "data_dir", "poll_seconds", "settle_seconds"):
            settings.pop(key)
        assets = [t["path"] for t in self.templates]
        if self.onnx_model:
            assets.append(self.onnx_model)
        hashes = {}
        for file in assets:
            h = hashlib.sha256()
            with open(Path(file).expanduser(), "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    h.update(chunk)
            hashes[file] = h.hexdigest()
        return hashlib.sha256(json.dumps([PIPELINE_VERSION, settings, hashes], sort_keys=True).encode()).hexdigest()
