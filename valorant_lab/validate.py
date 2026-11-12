"""Evaluate detector output against separate human labels; no bundled real-game claims."""
import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict
import json
import math
from pathlib import Path
import statistics

from .analysis import evaluate_events
from .schema import Evidence


def evaluate(predicted, truth, width, height, tolerance=.25):
    if width <= 0 or height <= 0 or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("Dimensions must be positive and tolerance finite/non-negative")
    errors, head_errors, used = [], [], set()
    by_target = defaultdict(list)
    for ti, observation in enumerate(truth.observations):
        by_target[observation.target_id].append((observation.time, ti))
    for values in by_target.values():
        values.sort()
    time_index = {key: [t for t, _ in values] for key, values in by_target.items()}
    candidates = []
    for pi, prediction in enumerate(predicted.observations):
        times = time_index.get(prediction.target_id, [])
        values = by_target[prediction.target_id]
        for offset in range(bisect_left(times, prediction.time-tolerance), bisect_right(times, prediction.time+tolerance)):
            timestamp, ti = values[offset]
            candidates.append((abs(prediction.time-timestamp), pi, ti))
    candidates.sort()
    matched_predictions = set()
    for _, pi, ti in candidates:
        if pi in matched_predictions or ti in used:
            continue
        matched_predictions.add(pi); used.add(ti)
        p,t = predicted.observations[pi],truth.observations[ti]
        def alignment(o):
            return math.hypot((o.crosshair_x-o.head_x)*width/height, o.crosshair_y-o.head_y)*100
        errors.append(abs(alignment(p)-alignment(t)))
        head_errors.append(math.hypot((p.head_x-t.head_x)*width,(p.head_y-t.head_y)*height))
    return dict(events=evaluate_events(predicted.events,truth.events,tolerance),
                aim=dict(matched=len(errors), predicted=len(predicted.observations), labelled=len(truth.observations),
                         alignment_mae_percent_frame_height=statistics.mean(errors) if errors else None,
                         head_location_mae_pixels=statistics.mean(head_errors) if head_errors else None),
                limitation="Only supplied labels; matching uses target_id and timestamp. Report dataset composition separately. No claim of real-game accuracy from synthetic fixtures.")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('predictions',type=Path)
    parser.add_argument('labels',type=Path)
    parser.add_argument('--width',type=int,required=True)
    parser.add_argument('--height',type=int,required=True)
    parser.add_argument('--tolerance',type=float,default=.25)
    args=parser.parse_args()
    try:
        predicted=Evidence.model_validate_json(args.predictions.read_text())
        labels=Evidence.model_validate_json(args.labels.read_text())
        print(json.dumps(evaluate(predicted,labels,args.width,args.height,args.tolerance),indent=2,allow_nan=False))
    except (ValueError,OSError) as error:
        parser.exit(2,f'Validation error: {error}\n')


if __name__=='__main__':
    main()
