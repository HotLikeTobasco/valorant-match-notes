# Validation status and next dataset

## What has actually been checked

Automated tests cover actual small-video encoding/decoding, scene-change timestamps, HUD template
matching on generated images, legal evidence ranges, geometry and shot-delay formulae, round review,
coaching thresholds/baselines, external telemetry parsing, database recovery/invalidation, interrupted
chunk resume, cached reanalysis, single-worker locking, HTTP byte-range playback, local-origin/token
checks, clip export and the preserved CSV utility. The ONNX adapter contract is tested with a stubbed
session output; this is not model inference validation.

Browser checks use `tests/generate_fixture.py`, which creates a 12-second video of generated geometry,
with conspicuous SYNTHETIC text and a known annotation sidecar. The fixture demonstrates ingestion,
playback, frame stepping, event review, saved recalculation, clip export and responsive layout. It does
not contain VALORANT footage or demonstrate enemy/game-event recognition.

`tests/fixtures/synthetic-labels.json` describes the separate 3-second black-to-white transition used
by the video ingestion tests. The expected single scene transition is at 1 second. The geometry tests
include a known 90-pixel head-location error and a 10-percentage-point alignment error. Passing these
checks establishes implementation behaviour on these cases only.

**No real-game precision, recall, timing accuracy, GPU speedup or competitive improvement result is
claimed. No manually labelled real gameplay dataset was available.**

## Build a private real-game validation set

1. Collect recordings you have rights to use, across maps, agents, HUD scales, crosshairs, resolutions,
   codecs and observer/player views. Keep recordings outside Git. Include quiet rounds and menus as
   negative examples as well as engagements.
2. Split by recording/session before choosing thresholds; do not use frames from the same match in
   training/tuning and evaluation. Keep a held-out set for final reporting.
3. Independently label visible target-head centres, actual crosshair points, first target visibility,
   shots, kills/deaths and round boundaries. Include unclear/occluded cases as unavailable, not guesses.
   Use a second reviewer for a subset and measure disagreement.
4. Record the source resolution, effective FPS/VFR status, point convention, model/config hash,
   annotator, date, hardware and sampling rate. Assign corresponding target IDs in predictions and
   labels for numerical evaluation; the current evaluator does not automatically solve identity matching.
5. Export automatic detections from `/api/sessions/{id}/detections`. Preserve their unreviewed source
   labels. Compare with a separately authored Evidence JSON through `python -m valorant_lab.validate`.
6. Report per-class counts, TP/FP/FN, precision/recall and timestamp MAE at a predeclared tolerance.
   For points, report matched/labelled counts, head-pixel MAE and alignment MAE. Report missing-label
   coverage explicitly. A null metric means not measurable, not zero error.
7. Review failure cases and confidence calibration before enabling a detector by default. UI confidence
   scores currently have no calibrated probabilistic meaning. Re-run the held-out evaluation after model,
   HUD, preprocessing or patch changes.

The evaluator uses one-to-one timestamp matches within each event class and target ID for points.
Matching is greedy by smallest timestamp difference; it is not an optimal assignment solver. Dataset
size and composition remain essential context. Do not use it alone as a benchmark leaderboard.

## Open validation work

- Real VALORANT head/crosshair/shot/HUD models and held-out evaluation.
- VFR timing and precise visibility-onset validation for first-shot estimates.
- Robust identity association across occlusions, cuts and processing chunks.
- Flick overshoot/undershoot and target-motion separation.
- Movement, peeking, exposure, utility, trades and death-cause interpretation.
- Long-recording profiling, GPU measurements, retention controls and clip-job persistence.
- Prospective testing of coaching thresholds and practice outcomes.
