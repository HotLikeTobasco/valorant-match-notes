# Evidence and detector contracts

All timestamps are seconds relative to the recording start; all image points are normalised
0..1 coordinates with origin at the top left. Values must be finite. Event timestamps and
observations must fit the video duration. Target IDs identify a visible target run, not a player account.

A minimal sidecar or API review body:

```json
{
  "schema_version": 1,
  "map_name": "Unknown",
  "note": "Review notes",
  "events": [
    {"time": 1.2, "kind": "shot", "source": "manual", "confidence": 0.9, "target_id": "enemy-1", "note": "Reviewed first shot"}
  ],
  "observations": [
    {"time": 1.0, "head_x": 0.52, "head_y": 0.48, "crosshair_x": 0.5, "crosshair_y": 0.5,
     "crosshair_source": "manual", "target_id": "enemy-1", "source": "manual", "confidence": 0.9},
    {"time": 1.2, "head_x": 0.51, "head_y": 0.49, "crosshair_x": 0.5, "crosshair_y": 0.5,
     "crosshair_source": "manual", "target_id": "enemy-1", "source": "manual", "confidence": 0.9}
  ]
}
```

This is a schema example, not a real match. Never import it as evidence for unrelated footage.

Supported events: scene_change, round_start, round_end, match_start, match_end, kill, death,
shot, utility, engagement, note. Optional reviewed tactical tags: low_crosshair, wide_swing,
utility_late, untraded, movement_shot. A match event is a reviewed marker; the application does
not automatically split a multi-match recording into separate sessions.

`crosshair_source` is manual, detected or assumed_center. The default is assumed_center, which
forces estimated status. The current UI supports manual marking, not automatic crosshair detection.
Confidence means the source's confidence/score, not empirical accuracy. Review API inputs must use
manual event/observation sources; detector outputs are generated internally.

A sidecar is bounded to 20 MB. Evidence supports at most 20,000 events and 100,000 observations.
Duplicate (timestamp, target_id) observations are rejected. GUI Save is rejected while a job is
queued/processing to prevent lost updates. The browser leaves unsaved evidence intact on polling.

## HUD templates

Add entries to the `templates` configuration list:

```json
{
  "path": "/absolute/path/to/your/round-banner.png",
  "roi": [0.3, 0.05, 0.4, 0.25],
  "kind": "round_end",
  "threshold": 0.95,
  "cooldown": 3
}
```

ROI is x,y,width,height in normalised coordinates. Templates must be non-uniform grayscale-readable
images cropped at the configured analysis resolution, and smaller than the ROI. A theme, HUD scale,
language or resolution change can invalidate them. Per-template cooldown is used during detection;
report assembly also debounces same-kind/source candidates within three seconds. Candidates are
not automatically treated as reviewed round statistics. Validate and review them first.

## ONNX head adapter

Install the optional runtime with `pip install -e '.[onnx]'` and configure `onnx_model` to a local file.
No model is downloaded automatically. The adapter expects **exactly this export contract**:

- Single RGB float32 input `[1,3,640,640]` in range 0..1. The image is stretched to 640×640.
- First output: float array `[N,6]`, each row `[x1,y1,x2,y2,score,class]`.
- Bounding coordinates normalised to the original image axes; class 0 is an enemy **head**.
- Model must perform its own NMS. The adapter retains score >= 0.8; this threshold is unvalidated.
- All coordinates and scores finite, score <= 1, and boxes inside the image.

A generic YOLO export or person detector does not satisfy this contract without conversion and
VALORANT-specific validation. Short proximity-based IDs can switch on occlusion, crossing targets,
fast motion or camera cuts; they are not identity tracking. Runs restart per processing chunk.
Detector output defaults to an assumed-centre crosshair and is always labelled an estimate.
The runtime provider preference is CUDA, CoreML, CPU, restricted to available providers.

Add a detector by implementing `detect(frame, time) -> (events, observations)` and constructing
it in the pipeline. Bump PIPELINE_VERSION when semantics change. Detector errors fail the job
with an explicit message rather than silently returning invented or successful empty analysis.

## Optional mouse telemetry

`telemetry.load_telemetry(path, video_offset=...)` reads an externally generated JSONL file:

```json
{"time":1.0,"dx":3,"dy":4,"fired":false}
{"time":1.1,"dx":0,"dy":0,"fired":true}
```

Times must be nondecreasing. Synchronise the offset using a visible known action; the system
cannot infer it. `mouse_distance(samples, degrees_per_count=None)` returns path length in raw
counts, and angular path only when an external calibration is supplied. It does not infer in-game
sensitivity, flick accuracy or movement velocity. This is an offline library interface, not a live
recorder or dashboard feature; no hooks or game-memory access are present.
