import hashlib
import json
import logging
from pathlib import Path
import threading
import time
from filelock import FileLock

from .analysis import analyse
from .db import Store
from .detectors import ONNXHeadDetector, SceneDetector, TemplateDetector
from .schema import Evidence, Event, Observation
from .video import FORMATS, fingerprint, frames, probe, transcode

log = logging.getLogger(__name__)


def source_signature(path):
    sidecar = Path(str(path) + ".evidence.json")
    evidence_hash = ""
    if sidecar.exists():
        if sidecar.stat().st_size > 20_000_000:
            raise ValueError("Evidence sidecar exceeds 20 MB")
        evidence_hash = hashlib.sha256(sidecar.read_bytes()).hexdigest()
    return fingerprint(path) + ":" + evidence_hash


class Pipeline:
    def __init__(self, config):
        self.config = config
        self.store = Store(config.data_dir / "sessions.sqlite")
        self.stop = threading.Event()
        self.seen = {}
        self.thread = None
        self.scan_error = None
        self.signature = config.signature()
        self.process_lock = threading.Lock()
        self.instance_lock = FileLock(config.data_dir / "worker.lock")

    def scan(self, now=None):
        now = time.time() if now is None else now
        root = self.config.recordings_dir
        if not root.is_dir():
            self.scan_error = f"Recording folder not found: {root}. Set recordings_dir in config.json."
            return 0
        found = 0
        present = set()
        existing_by_path = {s["path"]: s for s in self.store.list()}
        self.scan_error = None
        for file in root.rglob("*"):
            if not file.is_file() or file.suffix.lower() not in FORMATS:
                continue
            path = file.resolve()
            if root not in path.parents:  # Do not ingest symlinks outside the configured folder.
                continue
            present.add(path)
            try:
                stat = path.stat()
                stamp = fingerprint(path)
                previous = self.seen.get(path)
                if not previous or previous[0] != stamp:
                    self.seen[path] = (stamp, now)
                    continue
                if not stat.st_size or now-previous[1] < self.config.settle_seconds or now-stat.st_mtime < self.config.settle_seconds:
                    continue
                signature = source_signature(path)
                existing = existing_by_path.get(str(path))
                if existing and existing["state"] == "processing":
                    continue
                self.store.enqueue(path, signature, self.signature)
                found += 1
            except (OSError, ValueError) as error:
                self.scan_error = str(error)
        self.seen = {k: v for k, v in self.seen.items() if k in present}
        return found

    def artifact_dir(self, session):
        digest = hashlib.sha256((session["fingerprint"]+session["pipeline"]).encode()).hexdigest()[:16]
        return self.config.data_dir / "artifacts" / str(session["id"]) / digest

    def process_next(self):
        with self.process_lock:
            session = self.store.claim()
            if not session:
                return False
            sid, path = session["id"], Path(session["path"])
            try:
                self.process(session, path)
            except InterruptedError:
                self.store.update(sid, state="queued", error="Interrupted; processing resumes when the app restarts")
            except Exception as error:
                log.exception("Processing failed for session %s", sid)
                self.store.update(sid, state="failed", error=str(error)[:3000])
            return True

    def process(self, session, path):
        cfg, sid = self.config, session["id"]
        if source_signature(path) != session["fingerprint"]:
            raise ValueError("Recording changed after discovery; the watcher will queue its new version")
        metadata = probe(path)
        self.store.update(sid, metadata=json.dumps(metadata))
        duration = metadata["duration"]
        sidecar = Path(str(path) + ".evidence.json")
        if session["evidence"]:
            evidence = Evidence.model_validate_json(session["evidence"])
        elif sidecar.exists():
            evidence = Evidence.model_validate_json(sidecar.read_text())
        else:
            evidence = Evidence()
        evidence.within(duration)
        start = session["checkpoint"]
        while start < duration:
            if self.stop.is_set():
                raise InterruptedError()
            end = min(duration, start+cfg.chunk_seconds)
            detectors = [SceneDetector(cfg.scene_threshold), TemplateDetector(cfg.templates)]
            if cfg.onnx_model:
                detectors.append(ONNXHeadDetector(cfg.onnx_model))
            events, observations = [], []
            count = 0
            for timestamp, frame in frames(path, max(0, start-3), end, cfg.sample_fps, cfg.analysis_width, self.stop):
                count += 1
                for detector in detectors:
                    es, obs = detector.detect(frame, timestamp)
                    if timestamp + 1e-6 >= start:
                        events.extend(e.model_dump() for e in es)
                        for o in obs:
                            o.target_id = f"chunk-{start:.3f}/{o.target_id}"
                            observations.append(o.model_dump())
            if self.stop.is_set():
                raise InterruptedError()
            if count == 0:
                raise ValueError(f"No decodable frames at {start:.1f}s; recording may be truncated")
            if source_signature(path) != session["fingerprint"]:
                raise ValueError("Recording changed during processing; retry after recording ends")
            self.store.checkpoint(sid, start, end, duration, dict(events=events, observations=observations, frames=count))
            start = end
        events, observations = list(evidence.events), list(evidence.observations)
        use_reviewed_points = bool(evidence.observations)
        chunks = self.store.chunks(sid)
        manual_keys = {(o.time, o.target_id) for o in observations}
        last = {}
        for chunk in chunks:
            for raw in chunk["events"]:
                event = Event.model_validate(raw)
                key = (event.kind, event.source)
                cooldown = 0.5 if event.kind == "scene_change" else 3
                if event.time-last.get(key, -1e9) >= cooldown:
                    events.append(event)
                    last[key] = event.time
            if not use_reviewed_points:
                observations.extend(Observation.model_validate(o) for o in chunk["observations"] if (o["time"], o["target_id"]) not in manual_keys)
        report = analyse(events, observations, metadata, cfg.sample_fps)
        report["map_name"] = evidence.map_name
        report["note"] = evidence.note
        report["processed_frames"] = sum(c["frames"] for c in chunks)
        proxy = self.artifact_dir(session) / "playback.mp4"
        if not proxy.exists():
            try:
                transcode(path, proxy, self.stop, cfg.hwaccel)
            except RuntimeError:
                if cfg.hwaccel == "none":
                    raise
                report["decoder_warning"] = "Hardware decode failed; playback proxy used CPU fallback."
                transcode(path, proxy, self.stop, "none")
        if source_signature(path) != session["fingerprint"]:
            raise ValueError("Recording changed before completion; watcher will rescan")
        self.store.update(sid, report=json.dumps(report), evidence=evidence.model_dump_json(), state="complete", progress=1, error=None)

    def run(self):
        while not self.stop.is_set():
            try:
                self.scan()
                if self.process_next():
                    continue
            except Exception as error:
                log.exception("Watcher error")
                self.scan_error = str(error)
            self.stop.wait(self.config.poll_seconds)

    def start(self):
        self.instance_lock.acquire(timeout=0)
        self.store.recover()
        self.thread = threading.Thread(target=self.run, name="vod-worker", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=10)
        if not self.thread or not self.thread.is_alive():
            self.instance_lock.release()
