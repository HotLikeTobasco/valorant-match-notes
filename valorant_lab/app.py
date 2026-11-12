from contextlib import asynccontextmanager
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from .analysis import coaching_progress
from .config import Config
from .pipeline import Pipeline
from .schema import Evidence
from .video import transcode

STATIC = Path(__file__).parent / "static"


class ClipRequest(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)


def create_app(config=None, run_worker=True):
    config = config or Config()
    pipeline = Pipeline(config)
    token = secrets.token_urlsafe(32)
    clips = {}
    clip_lock = threading.Lock()
    clip_threads = []

    @asynccontextmanager
    async def lifespan(app):
        if run_worker:
            pipeline.start()
        yield
        pipeline.close()
        for thread in clip_threads:
            thread.join(timeout=6)

    app = FastAPI(title="Valorant Review Lab", lifespan=lifespan)
    app.state.pipeline = pipeline
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin access is disabled"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("x-lab-token") != token:
                return JSONResponse({"detail": "Missing local session token; reload dashboard"}, status_code=403)
            if int(request.headers.get("content-length", "0")) > 20_000_000:
                return JSONResponse({"detail": "Evidence exceeds 20 MB"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; media-src 'self'; frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    def require(sid):
        session = pipeline.store.get(sid)
        if not session:
            raise HTTPException(404, "Session not found")
        return session

    def public(session, detailed=False):
        data = {k: session[k] for k in ("id", "state", "progress", "error", "created", "updated")}
        data["name"] = Path(session["path"]).name
        data["metadata"] = json.loads(session["metadata"])
        report = json.loads(session["report"]) if session["report"] else None
        data["map_name"] = report.get("map_name", "Unknown") if report else "Unknown"
        if detailed:
            previous = None
            if report and report.get("map_name", "Unknown").lower() != "unknown":
                for old in pipeline.store.list():
                    if (old["created"], old["id"]) >= (session["created"], session["id"]) or old["pipeline"] != session["pipeline"] or not old["report"] or old["state"] != "complete":
                        continue
                    candidate = json.loads(old["report"])
                    if candidate.get("map_name") == report.get("map_name"):
                        previous = candidate
                        break
            data["report"] = coaching_progress(report, previous) if report else None
            data["evidence"] = json.loads(session["evidence"]) if session["evidence"] else Evidence().model_dump()
        elif report:
            data["metrics"] = report["metrics"]
        return data

    @app.get("/api/status")
    def status():
        return {"token": token, "recordings_dir": str(config.recordings_dir), "watcher_error": pipeline.scan_error,
                "sample_fps": config.sample_fps, "pipeline": pipeline.signature[:12],
                "detectors": ["scene changes"] + (["HUD templates (unvalidated)"] if config.templates else []) + (["ONNX heads (unvalidated)"] if config.onnx_model else []),
                "validation": "No real-game detector validation dataset supplied."}

    @app.get("/api/sessions")
    def sessions():
        return [public(s) for s in pipeline.store.list()]

    @app.get("/api/sessions/{sid}")
    def session(sid: int):
        return public(require(sid), True)

    @app.post("/api/sessions/{sid}/retry")
    def retry(sid: int, reset: bool = False):
        require(sid)
        if not pipeline.store.retry(sid, reset):
            raise HTTPException(409, "Session is already queued or processing")
        return {"state": "queued"}

    @app.put("/api/sessions/{sid}/evidence")
    def evidence(sid: int, body: Evidence):
        row = require(sid)
        metadata = json.loads(row["metadata"])
        if "duration" not in metadata:
            raise HTTPException(409, "Video metadata is not available yet")
        try:
            body.within(metadata["duration"])
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if any(e.source != "manual" for e in body.events) or any(o.source != "manual" for o in body.observations):
            raise HTTPException(422, "Review input must use manual source; model results come from the pipeline")
        if not pipeline.store.save_evidence(sid, body.model_dump()):
            raise HTTPException(409, "Wait for processing to finish before editing evidence")
        return {"state": "queued"}

    @app.get("/api/sessions/{sid}/video")
    def video(sid: int):
        row = require(sid)
        path = pipeline.artifact_dir(row) / "playback.mp4"
        if not path.exists():
            raise HTTPException(409, "Playback proxy is not ready")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/api/sessions/{sid}/detections")
    def detections(sid: int):
        row = require(sid)
        if row["state"] != "complete":
            raise HTTPException(409, "Finish processing before exporting detections")
        chunks = pipeline.store.chunks(sid)
        return Evidence.model_validate({"events": [e for c in chunks for e in c["events"]],
                                        "observations": [o for c in chunks for o in c["observations"]]}).model_dump()

    @app.get("/api/trends")
    def trends():
        rows = []
        for s in reversed(pipeline.store.list()):
            if not s["report"] or s["state"] != "complete":
                continue
            r = json.loads(s["report"])
            m = r["metrics"]["alignment_error"]
            if m["value"] is None:
                continue
            comparable = [p for p in rows if p["pipeline"] == s["pipeline"] and p["map_name"] == r["map_name"] and p["basis"] == m["basis"]]
            previous = comparable[-1]["value"] if comparable and r["map_name"].lower() != "unknown" else None
            rows.append(dict(id=s["id"], name=Path(s["path"]).name, value=m["value"], samples=m["samples"],
                             basis=m["basis"], map_name=r["map_name"], pipeline=s["pipeline"],
                             delta=m["value"]-previous if previous is not None else None))
        return {"points": rows, "limitation": "Only supplied samples. Delta compares the previous session with the same pipeline, map label and measurement basis; sampling bias remains."}

    @app.post("/api/sessions/{sid}/clips", status_code=202)
    def clip(sid: int, body: ClipRequest):
        row = require(sid)
        if row["state"] != "complete":
            raise HTTPException(409, "Finish processing before exporting clips")
        duration = json.loads(row["metadata"])["duration"]
        if body.end <= body.start or body.end > duration or body.end-body.start > 120:
            raise HTTPException(422, "Clip must be within the recording and between 0 and 120 seconds long")
        with clip_lock:
            if any(j["state"] == "processing" for j in clips.values()):
                raise HTTPException(409, "One clip is already being exported")
            job_id = secrets.token_hex(12)
            target = pipeline.artifact_dir(row) / "clips" / f"{job_id}.mp4"
            clips[job_id] = dict(state="processing", path=target, error=None)
        def work():
            try:
                transcode(pipeline.artifact_dir(row) / "playback.mp4", target, pipeline.stop, start=body.start, duration=body.end-body.start)
                with clip_lock:
                    clips[job_id]["state"] = "complete"
            except Exception as error:
                with clip_lock:
                    clips[job_id].update(state="failed", error=str(error))
        thread = threading.Thread(target=work, daemon=True)
        clip_threads.append(thread)
        thread.start()
        return {"id": job_id}

    @app.get("/api/clips/{job_id}")
    def clip_status(job_id: str):
        with clip_lock:
            job = clips.get(job_id)
            if not job:
                raise HTTPException(404, "Clip job not found; export jobs are not retained after restart")
            return {"state": job["state"], "error": job["error"], "url": f"/api/clips/{job_id}/download" if job["state"] == "complete" else None}

    @app.get("/api/clips/{job_id}/download")
    def clip_download(job_id: str):
        with clip_lock:
            job = clips.get(job_id)
            if not job or job["state"] != "complete":
                raise HTTPException(404, "Clip not ready")
            return FileResponse(job["path"], media_type="video/mp4", filename=f"review-{job_id}.mp4")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app
