"""Evidence-first measurements and transparent coaching rules, not an LLM."""
from collections import defaultdict
import math
import statistics

from .schema import Event, Observation


def metric(value, unit, n, basis, confidence, limitation):
    return dict(value=value, unit=unit, samples=n, basis=basis, confidence=confidence, limitation=limitation)


def analyse(events, observations, metadata, sample_fps):
    events = sorted(events, key=lambda e: e.time)
    observations = sorted(observations, key=lambda o: (o.time, o.target_id))
    aspect = metadata["width"] / metadata["height"]
    # Convert x distance to frame-height units so Euclidean distances respect aspect ratio.
    points = [{**o.model_dump(), "error": math.hypot((o.crosshair_x-o.head_x)*aspect, o.crosshair_y-o.head_y)*100,
               "vertical": (o.crosshair_y-o.head_y)*100} for o in observations]
    groups = defaultdict(list)
    for p in points:
        groups[p["target_id"]].append(p)
    engagements = []
    gap = max(.5, 2/sample_fps)
    for target, samples in groups.items():
        runs = []
        for sample in samples:
            if not runs or sample["time"]-runs[-1][-1]["time"] > gap:
                runs.append([])
            runs[-1].append(sample)
        for run in runs:
            engagements.append(dict(target_id=target, start=run[0]["time"], end=run[-1]["time"],
                                    samples=len(run), mean_error=statistics.mean(p["error"] for p in run),
                                    below_fraction=sum(p["vertical"] > 2 for p in run)/len(run),
                                    confidence=min(p["confidence"] for p in run)))
    engagements.sort(key=lambda e: e["start"])
    confidence = min((p["confidence"] for p in points), default=0)
    estimated = any(p["source"] != "manual" or p["crosshair_source"] == "assumed_center" for p in points)
    basis = "estimate" if estimated else "measured from supplied annotations"
    limitation = "Visible annotated/detected samples only; not a whole-match skill score. Sampling and annotation errors apply."
    if estimated:
        limitation += " Includes assumed screen-centre crosshair or unvalidated model outputs; confidence is an uncalibrated input score."
    errors = [p["error"] for p in points]
    metrics = {
        "alignment_error": metric(statistics.mean(errors) if errors else None, "% frame height", len(points), basis, confidence, limitation),
        "vertical_offset": metric(statistics.mean(p["vertical"] for p in points) if points else None, "% frame height (+ below)", len(points), basis, confidence, limitation),
        "alignment_variability": metric(statistics.pstdev(errors) if len(errors)>1 else None, "% frame height", len(points), basis, confidence,
                                        "Variation of target-relative error across supplied samples; not isolated tracking skill."),
    }
    delays = []
    shot_errors = []
    for engagement in engagements:
        matching = [e for e in events if e.kind == "shot" and e.source == "manual" and e.target_id == engagement["target_id"]
                    and engagement["start"] <= e.time <= engagement["end"]]
        if matching:
            first = matching[0]
            delays.append((first.time-engagement["start"])*1000)
            nearby = [p for p in points if p["target_id"] == first.target_id and abs(p["time"]-first.time) <= 1/sample_fps]
            if nearby:
                shot_errors.append(min(nearby, key=lambda p: abs(p["time"]-first.time))["error"])
    metrics["first_shot_delay"] = metric(statistics.mean(delays) if delays else None, "ms", len(delays), "estimate", confidence,
        f"From first supplied visible-target sample to first manually linked shot, not reaction time. Sampling uncertainty at least {1000/sample_fps:.0f} ms; annotations may omit earlier visibility.")
    metrics["shot_alignment"] = metric(statistics.mean(shot_errors) if shot_errors else None, "% frame height", len(shot_errors), "estimate", confidence,
        "Nearest supplied observation within one sampling interval of the first manually linked shot per engagement.")
    rounds = []
    starts = [e for e in events if e.kind == "round_start" and e.source == "manual"]
    for index, start in enumerate(starts):
        end = starts[index+1].time if index+1 < len(starts) else metadata["duration"]
        rounds.append(dict(number=index+1, start=start.time, end=end,
                           kills=sum(e.kind == "kill" and e.source == "manual" and start.time <= e.time < end for e in events),
                           deaths=sum(e.kind == "death" and e.source == "manual" and start.time <= e.time < end for e in events)))
    coaching = []
    low = [e for e in engagements if e["samples"] >= 3 and e["below_fraction"] >= .6]
    if len(low) >= 3:
        coaching.append(dict(title="Review crosshair height", priority=1, frequency=len(low), denominator=len(engagements),
                             confidence=min(e["confidence"] for e in low), basis=basis,
                             evidence=[e["start"] for e in low],
                             finding="Crosshair was below the supplied head position by more than 2% of frame height in at least 60% of samples in these engagements.",
                             practice="In a custom game, pause before each peek and check head-height placement against a known reference."))
    practices = {"wide_swing": "Review each tagged peek; practise clearing one angle at a time in a custom game.",
                 "utility_late": "Choose a utility cue before the round and review whether it was used before contact.",
                 "untraded": "Review spacing and agree on a trade plan with a teammate.",
                 "movement_shot": "Review the tagged shots frame by frame; practise stop-and-fire bursts in the range."}
    for tag, practice in practices.items():
        matches = [e for e in events if e.tag == tag and e.source == "manual"]
        if len(matches) >= 3:
            coaching.append(dict(title=tag.replace("_", " ").capitalize(), priority=2, frequency=len(matches),
                                 denominator=sum(e.source == "manual" and e.tag is not None for e in events),
                                 confidence=min(e.confidence for e in matches), basis="reviewer interpretation",
                                 evidence=[e.time for e in matches], finding="Repeated manually tagged review issue; not automatically verified.", practice=practice))
    return dict(metrics=metrics, events=[e.model_dump() for e in events], points=points, engagements=engagements,
                rounds=rounds, coaching=sorted(coaching, key=lambda c: (c["priority"], -c["frequency"])),
                unavailable=["validated automatic kills/deaths/rounds", "flick overshoot and angular displacement", "world movement and counter-strafing",
                             "spray control", "angle exposure and trade availability", "automatic utility classification"],
                confidence_note="Confidence values are input reviewer/model/similarity scores, not calibrated probabilities.",
                coaching_engine="transparent rules v1; no generative AI", sample_fps=sample_fps)


def evaluate_events(predictions, truth, tolerance=.25):
    """One-to-one nearest timestamp matching per event class. Both inputs must be labelled."""
    result = {}
    for kind in sorted({e.kind for e in predictions + truth}):
        actual = [e.time for e in truth if e.kind == kind]
        predicted = [e.time for e in predictions if e.kind == kind]
        candidates = sorted((abs(p-t), pi, ti) for pi, p in enumerate(predicted) for ti, t in enumerate(actual) if abs(p-t) <= tolerance)
        used_p, used_t = set(), set()
        errors = []
        for error, pi, ti in candidates:
            if pi not in used_p and ti not in used_t:
                used_p.add(pi); used_t.add(ti); errors.append(error)
        tp = len(used_p)
        result[kind] = dict(tp=tp, fp=len(predicted)-tp, fn=len(actual)-tp,
                            precision=tp/len(predicted) if predicted else None,
                            recall=tp/len(actual) if actual else None,
                            timing_mae_seconds=statistics.mean(errors) if errors else None)
    return result
