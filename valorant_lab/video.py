from pathlib import Path
import math
import os
import subprocess
import threading
import uuid

import cv2
import imageio_ffmpeg

FORMATS = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm", ".m4v"}


def fingerprint(path):
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def probe(path):
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not cap.isOpened() or not math.isfinite(fps) or fps <= 0 or frames <= 0 or width <= 0 or height <= 0:
            raise ValueError("Cannot read video metadata; file may be incomplete or codec unsupported")
        return {"duration": frames / fps, "fps": fps, "width": width, "height": height,
                "timing": "container/frame-rate estimate; variable-frame-rate timing may drift"}
    finally:
        cap.release()


def frames(path, start, end, sample_fps, width, stop):
    """Decode sequentially; analyse only sampled frames. RAM is constant per frame."""
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("Cannot open recording")
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        next_time = start
        while not stop.is_set():
            ok = cap.grab()
            if not ok:
                break
            timestamp = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            # Some decoders return no timestamps. This fallback is explicitly estimated.
            if timestamp <= 0 and cap.get(cv2.CAP_PROP_POS_FRAMES) > 1:
                timestamp = (cap.get(cv2.CAP_PROP_POS_FRAMES) - 1) / fps
            if timestamp >= end:
                break
            if timestamp + 1e-6 < next_time:
                continue
            next_time = timestamp + 1 / sample_fps
            ok, frame = cap.retrieve()
            if not ok:
                raise ValueError("Sampled frame could not be decoded")
            h, w = frame.shape[:2]
            if w > width:
                frame = cv2.resize(frame, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
            yield timestamp, frame
    finally:
        cap.release()


def transcode(source, target, stop, hwaccel="none", start=None, duration=None):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.stem + f"-{uuid.uuid4().hex}.partial.mp4")
    ffmpeg = os.environ.get("FFMPEG_BINARY") or imageio_ffmpeg.get_ffmpeg_exe()
    base = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if hwaccel != "none":
        base += ["-hwaccel", hwaccel]
    if start is not None:
        base += ["-ss", str(start)]
    base += ["-i", str(source)]
    if duration is not None:
        base += ["-t", str(duration)]
    base += ["-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale='min(1280,iw)':-2",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-c:a", "aac", "-movflags", "+faststart", str(partial)]
    # File-backed stderr avoids a pipe deadlock and unbounded memory on long files.
    import tempfile
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(base, stdout=subprocess.DEVNULL, stderr=errors)
            try:
                while process.poll() is None:
                    if stop.wait(.2):
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        raise InterruptedError("Video conversion interrupted; retry on restart")
                if process.returncode:
                    errors.seek(0)
                    raise RuntimeError("FFmpeg conversion failed: " + errors.read(3000).decode(errors="replace"))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
