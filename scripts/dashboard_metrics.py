#!/usr/bin/env python3
"""Every number for the results dashboard, computed from files in this repo. Nothing typed in.

    python scripts/dashboard_metrics.py                 # writes metrics.json at the repo root
    python scripts/dashboard_metrics.py --runs runs --out metrics.json

Rules this script sticks to:
- a metric only gets a value if it was computed from a file. Anything else is null with a
  status (NEEDS DATA / NEEDS INPUT) and what's missing.
- recorded runs are split into "real" (the ESP32 rig) and "mock" (FakeHardware). They never
  get mixed into one number.
- the risk/out/*.scored.json files are real Muse Spark Open calls, but on fixture jobs (the
  incubator and the Inkbird), not on the timer demo. They're reported as their own group.

Real vs mock for a run: put a runs/labels.json next to the run folders, like
    {"job_1a2b3c4d": "real", "job_9f8e7d6c": "mock"}
That wins. With no label, the script guesses from the reads: FakeHardware reads have no
confidence (null), the rig's vision reads always have one. A guess is marked "inferred".
(FAKE_CONFIDENCE fakes confidences, so label anything filmed with it.)
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# the scorer's fallback reason (risk/scorer.py FALLBACK_REASON). Its fallback keeps the model
# id in risk_scored_by on some paths, so the reason is the reliable tell, not the id alone
FALLBACK_REASON = "Couldn't score this step, treating it as high risk."
REAL_SCORER_ID = "meta-models/muse-spark-open-1026"

# messages api.py writes when a job ends in "stopped", so a STOP press can be told apart
STOP_BY_USER = "Stopped by you"


# ---- small helpers ----


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def parse_ts(text):
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def seconds_between(start, end):
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def rel(path):
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def average(values):
    if not values:
        return None
    total = 0
    for value in values:
        total += value
    return round(total / len(values), 2)


def median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return round(ordered[middle], 2)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 2)


def metric(name, value, sample, source, status="REAL", note=None, group=None):
    entry = {"value": value, "sample": sample, "source": source, "status": status}
    if note:
        entry["note"] = note
    if group:
        entry["group"] = group
    return name, entry


# ---- recorded runs ----


def load_runs(runs_dir):
    """[{job_id, folder, job, events, label, label_source}] for every complete run folder."""
    runs = []
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return runs
    labels = {}
    labels_file = runs_dir / "labels.json"
    if labels_file.is_file():
        labels = load_json(labels_file)

    for folder in sorted(runs_dir.iterdir()):
        job_file = folder / "job.json"
        events_file = folder / "events.json"
        if not folder.is_dir() or not job_file.is_file() or not events_file.is_file():
            continue  # half-written or not a run
        job = load_json(job_file)
        events = load_json(events_file)
        label = labels.get(job["job_id"])
        label_source = "labels.json"
        if label not in ("real", "mock"):
            label = guess_label(events)
            label_source = "inferred from read confidence"
        runs.append({"job_id": job["job_id"], "folder": folder, "job": job, "events": events,
                     "label": label, "label_source": label_source})
    return runs


def guess_label(events):
    saw_confidence = False
    saw_read = False
    for event in events:
        note = event.get("note") or {}
        read = note.get("read")
        if read is not None:
            saw_read = True
            if read.get("confidence") in ("high", "medium", "low"):
                saw_confidence = True
    if saw_read and saw_confidence:
        return "real"
    return "mock"


def final_steps(run):
    return ((run["job"].get("plan") or {}).get("steps")) or []


def all_notes(run):
    notes = []
    for event in run["events"]:
        note = event.get("note")
        if note:
            notes.append((event, note))
    return notes


def run_metrics(runs, label):
    """Every run-based metric for one group (real or mock). Returns a dict of name -> entry."""
    group = [run for run in runs if run["label"] == label]
    out = {}
    source = f"runs/*/job.json + events.json ({label})"
    if not group:
        return out

    # ---- reliability ----
    finished = 0
    succeeded = 0
    press_steps = 0
    first_try = 0
    retries_per_job = []
    retry_count_total = 0
    caught_misses = 0
    for run in group:
        result = run["job"].get("result") or {}
        outcome = result.get("outcome")
        if outcome in ("succeeded", "failed", "stopped"):
            finished += 1
        if outcome == "succeeded":
            succeeded += 1
        job_retries = 0
        for step in final_steps(run):
            if step["action"]["type"] != "press" or step["status"] not in ("succeeded", "failed"):
                continue  # only presses that actually ran
            press_steps += 1
            if step["status"] == "succeeded" and step.get("attempts") == 1:
                first_try += 1
            step_retries = len(step.get("retries") or [])
            job_retries += step_retries
        retries_per_job.append(job_retries)
        retry_count_total += job_retries
        # a retry note = the camera read didn't match and the backend pressed again
        for _event, note in all_notes(run):
            if note.get("kind") == "retry":
                caught_misses += 1

    out.update(dict([
        metric(f"{label}.job_success_rate", f"{succeeded}/{finished}", f"{finished} finished jobs", source,
               note="result.outcome == succeeded"),
        metric(f"{label}.first_try_verify_rate", f"{first_try}/{press_steps}", f"{press_steps} press steps that ran", source,
               note="steps with status succeeded and attempts == 1"),
        metric(f"{label}.retries_per_job_avg", average(retries_per_job), f"{len(group)} jobs", source,
               note=f"{retry_count_total} retries total, from step.retries"),
        metric(f"{label}.missed_presses_caught", caught_misses, f"{len(group)} jobs", source,
               note="events with note.kind == retry"),
    ]))

    # ---- speed ----
    job_seconds = []
    step_seconds = []
    plan_seconds = []
    score_seconds = []
    approve_seconds = []
    for run in group:
        events = run["events"]
        created = parse_ts(run["job"].get("created_at"))
        finished_at = None
        plan_ready_at = None
        for event in events:
            if event["type"] == "job.finished":
                finished_at = parse_ts(event["ts"])
            if event["type"] == "plan.ready" and plan_ready_at is None:
                plan_ready_at = parse_ts(event["ts"])
        if (run["job"].get("result") or {}).get("outcome") == "succeeded":
            took = seconds_between(created, finished_at)
            if took is not None:
                job_seconds.append(took)
        # planner time includes the screen read before planning (that's what the user waits through)
        took = seconds_between(created, plan_ready_at)
        if took is not None:
            plan_seconds.append(took)

        for step in final_steps(run):
            took = seconds_between(parse_ts(step.get("started_at")), parse_ts(step.get("finished_at")))
            if took is not None and step["status"] == "succeeded":
                step_seconds.append(took)

        # scoring time: plan.ready to the first step.updated that carries that step's risk_tier
        scored_already = set()
        for event in events:
            if event["type"] != "step.updated":
                continue
            step = event["step"]
            if step.get("risk_tier") and step["step_id"] not in scored_already:
                scored_already.add(step["step_id"])
                took = seconds_between(plan_ready_at, parse_ts(event["ts"]))
                if took is not None:
                    score_seconds.append(took)

        # approval time: job.status awaiting_approval to the next job.status running (approve).
        # time_to_decide_ms itself only goes to the backend's console log, not the recording
        waiting_since = None
        for event in events:
            if event["type"] != "job.status":
                continue
            if event["status"] == "awaiting_approval":
                waiting_since = parse_ts(event["ts"])
            elif event["status"] == "running" and waiting_since is not None:
                approve_seconds.append(seconds_between(waiting_since, parse_ts(event["ts"])))
                waiting_since = None

    out.update(dict([
        metric(f"{label}.time_per_job_median_s", median(job_seconds), f"{len(job_seconds)} succeeded jobs", source,
               note="job.created_at to job.finished ts, includes the human's approval wait"),
        metric(f"{label}.time_per_step_median_s", median(step_seconds), f"{len(step_seconds)} succeeded steps", source,
               note="step.started_at to step.finished_at"),
        metric(f"{label}.planner_time_median_s", median(plan_seconds), f"{len(plan_seconds)} jobs", source,
               note="job.created_at to plan.ready ts, includes the pre-plan screen read"),
        metric(f"{label}.risk_scoring_time_median_s", median(score_seconds), f"{len(score_seconds)} scored steps", source,
               note="plan.ready to the first step.updated with risk_tier (upper bound, it's an event ts)"),
        metric(f"{label}.human_approval_time_median_s", median(approve_seconds), f"{len(approve_seconds)} approvals", source,
               note="awaiting_approval to running, from job.status events"),
    ]))

    # ---- vision ----
    confidence_mix = {"high": 0, "medium": 0, "low": 0, "none": 0}
    low_pauses = 0
    for run in group:
        for _event, note in all_notes(run):
            read = note.get("read")
            if read is not None:
                level = read.get("confidence")
                if level in confidence_mix:
                    confidence_mix[level] += 1
                else:
                    confidence_mix["none"] += 1
            if note.get("kind") == "read_unclear":
                low_pauses += 1
    reads_total = confidence_mix["high"] + confidence_mix["medium"] + confidence_mix["low"] + confidence_mix["none"]
    out.update(dict([
        metric(f"{label}.screen_read_confidence_mix", confidence_mix, f"{reads_total} reads on log notes", source,
               note="note.read.confidence. A read can show up on more than one note"),
        metric(f"{label}.low_confidence_pauses", low_pauses, f"{len(group)} jobs", source,
               note="note.kind == read_unclear"),
    ]))

    # ---- safety ----
    steps_total = 0
    gated = 0
    tiers = {"low": 0, "medium": 0, "high": 0}
    scored_real = 0
    scored_fallback = 0
    scored_other = 0
    for run in group:
        for step in final_steps(run):
            steps_total += 1
            if step.get("requires_approval"):
                gated += 1
            tier = step.get("risk_tier")
            if tier in tiers:
                tiers[tier] += 1
            by = step.get("risk_scored_by")
            if by is None:
                continue
            if by.startswith("fallback") or step.get("risk_reason") == FALLBACK_REASON:
                scored_fallback += 1
            elif by == REAL_SCORER_ID:
                scored_real += 1
            else:
                scored_other += 1  # e.g. the web mock's "(recorded score, scorer not running)"

    approvals = 0
    rejections = 0
    refusals = 0
    stops = 0
    for run in group:
        for _event, note in all_notes(run):
            if note.get("kind") == "rejected":
                rejections += 1
            if note.get("kind") == "planner_refused":
                refusals += 1
        # an approval = a job.status running right after awaiting_approval
        waiting = False
        for event in run["events"]:
            if event["type"] != "job.status":
                continue
            if event["status"] == "awaiting_approval":
                waiting = True
            elif event["status"] == "running" and waiting:
                approvals += 1
                waiting = False
        result = run["job"].get("result") or {}
        if result.get("outcome") == "stopped" and (result.get("message") or "").startswith(STOP_BY_USER):
            stops += 1

    scored_total = scored_real + scored_fallback + scored_other
    out.update(dict([
        metric(f"{label}.steps_needing_approval", f"{gated}/{steps_total}", f"{steps_total} planned steps", source,
               note="step.requires_approval"),
        metric(f"{label}.risk_tier_mix", tiers, f"{tiers['low'] + tiers['medium'] + tiers['high']} scored steps", source),
        metric(f"{label}.approvals_vs_rejections", {"approved": approvals, "rejected": rejections},
               f"{approvals + rejections} decisions", source),
        metric(f"{label}.plans_refused", refusals, f"{len(group)} jobs", source, note="note.kind == planner_refused"),
        metric(f"{label}.stop_presses", stops, f"{len(group)} jobs", source,
               note=f"result.outcome stopped with message starting '{STOP_BY_USER}'"),
        metric(f"{label}.scored_by_real_spark_open", f"{scored_real}/{scored_total}", f"{scored_total} scored steps", source,
               note=f"fallback {scored_fallback}, other {scored_other} (step.risk_scored_by + risk_reason)"),
    ]))
    # computed from data, but FakeHardware data: never let these read as rig numbers
    if label == "mock":
        for entry in out.values():
            entry["status"] = "MOCK"
    return out


# ---- reliability table in docs/hardware.md ----


def bench_presses(hardware_md):
    """Rows of the 'Reliability so far' table: [(test, ok, total)]."""
    rows = []
    if not hardware_md.is_file():
        return rows
    in_table = False
    for line in hardware_md.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Reliability so far"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 2:
            continue
        found = re.fullmatch(r"(\d+)\s*/\s*(\d+)", cells[1])
        if found:
            rows.append((cells[0], int(found.group(1)), int(found.group(2))))
    return rows


# ---- static files ----


def device_profiles(devices_dir):
    profiles = {}
    for path in sorted(Path(devices_dir).glob("*.json")):
        profile = load_json(path)
        controls = profile.get("controls") or {}
        buttons = 0
        reachable_buttons = 0
        screens = 0
        risky = 0
        for control_id, control in controls.items():
            if control_id.startswith("screen_") or control_id.startswith("led_"):
                screens += 1
                continue
            buttons += 1
            if control.get("reachable", True):
                reachable_buttons += 1
            if control.get("risky") or control.get("turns_on_output") or control.get("destructive"):
                risky += 1
        placeholder = "placeholder" in json.dumps(profile.get("_comment", "")).lower() or \
                      "placeholder" in json.dumps(profile.get("notes", "")).lower()
        profiles[profile["device_id"]] = {
            "file": rel(path), "controls": len(controls), "buttons": buttons,
            "buttons_with_a_finger": reachable_buttons, "screens_and_leds": screens,
            "risky_buttons": risky, "says_placeholder": placeholder,
        }
    return profiles


def calibration_files(calibration_dir):
    files = {}
    for path in sorted(Path(calibration_dir).glob("*.boxes.json")):
        calibration = load_json(path)
        comment = str(calibration.get("_comment", ""))
        files[calibration["device_id"]] = {
            "file": rel(path), "boxes": len(calibration.get("boxes") or {}),
            "frame_size": calibration.get("frame_size"),
            "placeholder": comment.upper().startswith("PLACEHOLDER"),
        }
    return files


def scored_fixtures(risk_out):
    """The committed Spark Open scores. Real model calls, fixture jobs (not the timer demo)."""
    jobs = []
    for path in sorted(Path(risk_out).glob("*.scored.json")):
        job = load_json(path)
        steps = (job.get("plan") or {}).get("steps") or []
        real = 0
        fallback = 0
        tiers = {"low": 0, "medium": 0, "high": 0}
        for step in steps:
            if step.get("risk_reason") == FALLBACK_REASON or (step.get("risk_scored_by") or "").startswith("fallback"):
                fallback += 1
            elif step.get("risk_scored_by") == REAL_SCORER_ID:
                real += 1
            if step.get("risk_tier") in tiers:
                tiers[step["risk_tier"]] += 1
        jobs.append({"file": rel(path), "device_id": job["device"]["device_id"], "request": job.get("request"),
                     "steps": len(steps), "real": real, "fallback": fallback, "tiers": tiers,
                     "steps_detail": [(s["step_id"], s.get("requires_approval"), s.get("risk_tier")) for s in steps]})
    return jobs


def caps(backend_dir):
    """Press budget, max attempts, correction cap from api.py, hold limits from the timer profile."""
    found = {}
    api_text = (Path(backend_dir) / "api.py").read_text(encoding="utf-8")
    for name in ("DEFAULT_PRESS_BUDGET", "DEFAULT_MAX_ATTEMPTS", "MAX_CORRECTION_PRESSES"):
        hit = re.search(rf"^{name}\s*=\s*(\d+)", api_text, re.MULTILINE)
        found[name] = int(hit.group(1)) if hit else None
    timer = load_json(Path(backend_dir) / "devices" / "timer-01.json")
    found["timer_hold_ms"] = timer.get("hold_ms")
    found["timer_max_hold_ms"] = timer.get("max_hold_ms")
    return found


# ---- putting it together ----


def build(runs_dir, out_path):
    metrics = {}
    notes = []

    # recorded runs
    runs = load_runs(runs_dir)
    real_runs = [run for run in runs if run["label"] == "real"]
    mock_runs = [run for run in runs if run["label"] == "mock"]
    metrics["runs.found"] = {"value": {"real": len(real_runs), "mock": len(mock_runs)},
                             "sample": f"{len(runs)} run folders", "source": rel(runs_dir),
                             "status": "REAL" if runs else "NEEDS DATA",
                             "labels": {run["job_id"]: f"{run['label']} ({run['label_source']})" for run in runs}}
    if not runs:
        notes.append(f"No recorded runs in {rel(runs_dir)}. runs/ is gitignored, so record with "
                     "backend/record_run.py on the rig laptop and run this script there.")
    metrics.update(run_metrics(runs, "real"))
    metrics.update(run_metrics(runs, "mock"))

    # bench presses (Python, no AI in the loop)
    rows = bench_presses(REPO_ROOT / "docs" / "hardware.md")
    ok_total = 0
    tried_total = 0
    for _test, ok, tried in rows:
        ok_total += ok
        tried_total += tried
    if rows:
        metrics["bench.press_success"] = {
            "value": f"{ok_total}/{tried_total}", "sample": f"{len(rows)} bench tests", "status": "REAL",
            "source": "docs/hardware.md 'Reliability so far'",
            "note": "presses sent straight from serial_hardware.py, not through the AI loop",
            "rows": [{"test": test, "ok": ok, "tried": tried} for test, ok, tried in rows],
        }
        rebuilt_ok = 0
        rebuilt_tried = 0
        for test, ok, tried in rows:
            if "rebuilt" in test.lower():
                rebuilt_ok += ok
                rebuilt_tried += tried
        metrics["bench.press_success_rebuilt_rig"] = {
            "value": f"{rebuilt_ok}/{rebuilt_tried}", "sample": "rows mentioning the rebuilt rig (Nov 2)",
            "status": "REAL", "source": "docs/hardware.md 'Reliability so far'",
            "note": "the rig in the video. ST/SP counted on video, H by reading T1",
        }

    # safety caps (config, not measurements)
    limits = caps(REPO_ROOT / "backend")
    metrics["config.press_budget_per_job"] = {"value": limits["DEFAULT_PRESS_BUDGET"], "sample": "config",
                                              "status": "REAL", "source": "backend/api.py DEFAULT_PRESS_BUDGET"}
    metrics["config.max_attempts_per_step"] = {"value": limits["DEFAULT_MAX_ATTEMPTS"], "sample": "config",
                                               "status": "REAL", "source": "backend/api.py DEFAULT_MAX_ATTEMPTS"}
    metrics["config.max_correction_presses"] = {"value": limits["MAX_CORRECTION_PRESSES"], "sample": "config",
                                                "status": "REAL", "source": "backend/api.py MAX_CORRECTION_PRESSES"}
    metrics["config.hold_ms_timer"] = {"value": {"hold_ms": limits["timer_hold_ms"], "max_hold_ms": limits["timer_max_hold_ms"]},
                                       "sample": "config", "status": "REAL",
                                       "source": "backend/devices/timer-01.json hold_ms, max_hold_ms"}

    # scale: device profiles and calibration
    profiles = device_profiles(REPO_ROOT / "backend" / "devices")
    metrics["scale.device_profiles"] = {"value": len(profiles), "sample": f"{len(profiles)} files", "status": "REAL",
                                        "source": "backend/devices/*.json", "devices": profiles,
                                        "note": "incubator-01 is a placeholder device, inkbird-01 is cut from the demo"}
    for device_id, info in profiles.items():
        metrics[f"scale.controls.{device_id}"] = {
            "value": info["controls"], "sample": "1 profile", "status": "REAL", "source": info["file"] + " controls",
            "note": f"{info['buttons']} buttons ({info['buttons_with_a_finger']} with a finger), "
                    f"{info['screens_and_leds']} screens/LEDs, {info['risky_buttons']} risky",
        }
    calibration = calibration_files(REPO_ROOT / "vision" / "calibration")
    for device_id, info in calibration.items():
        metrics[f"vision.calibrated_boxes.{device_id}"] = {
            "value": info["boxes"], "sample": f"frame {info['frame_size']}", "source": info["file"] + " boxes",
            "status": "REAL" if not info["placeholder"] else "NEEDS DATA",
            "note": "file says PLACEHOLDER (hand-held photo / mock frame), not a rig frame" if info["placeholder"] else None,
        }

    # risk scorer on fixtures
    fixtures = scored_fixtures(REPO_ROOT / "risk" / "out")
    real_scores = 0
    fallback_scores = 0
    for job in fixtures:
        real_scores += job["real"]
        fallback_scores += job["fallback"]
    metrics["risk_fixtures.scored_by_real_spark_open"] = {
        "value": f"{real_scores}/{real_scores + fallback_scores}", "sample": f"{len(fixtures)} fixture jobs",
        "status": "REAL", "source": "risk/out/*.scored.json risk_scored_by + risk_reason",
        "note": "real Spark Open calls, but on fixture jobs (incubator, Inkbird), not the timer demo",
        "jobs": fixtures,
    }
    context_pair = {}
    for job in fixtures:
        for step_id, gated, tier in job["steps_detail"]:
            if gated and job["device_id"] == "incubator-01":
                context_pair[job["request"]] = tier
    metrics["risk_fixtures.same_button_different_risk"] = {
        "value": context_pair, "sample": "START step in 2 fixture jobs", "status": "REAL",
        "source": "risk/out/incubator_start_37.scored.json, incubator_start_90.scored.json (s6 risk_tier)",
        "note": "same START button, the model scores it by context",
    }

    # things the repo can't give us
    for name, what in (
        ("cost.hardware_parts_total", "receipts (servos, ESP32, webcam, power bank, timer). Nothing in the repo"),
        ("cost.api_spend_per_job", "Meta Model API + Baseten billing pages for the days of the runs, divided by real jobs. "
                                   "The backend doesn't log tokens"),
        ("cost.replace_machine", "a cited price for a new 4-channel timer or the lab machine it stands in for"),
        ("cost.iot_retrofit", "a cited source (plan has the Bosch drill press ~$2,000 and A3's ranges)"),
        ("vision.sam_detection_confidence", "SAM 3.1 raw outputs (*.controls.json, *.sam_raw.json) are gitignored, "
                                            "and the device controls' confidence values come from fixtures"),
        ("vision.time_to_decide_ms_as_sent", "api.py only prints time_to_decide_ms to the console. "
                                             "Event timestamps above are the recorded proxy"),
    ):
        metrics[name] = {"value": None, "sample": None, "status": "NEEDS INPUT", "source": None, "need": what}

    output = {"generated_by": "scripts/dashboard_metrics.py", "notes": notes, "metrics": metrics}
    Path(out_path).write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compute dashboard metrics from the repo's files")
    parser.add_argument("--runs", type=Path, default=REPO_ROOT / "runs")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "metrics.json")
    args = parser.parse_args(argv)
    output = build(args.runs, args.out)
    for line in output["notes"]:
        print("NOTE:", line)
    for name, entry in output["metrics"].items():
        shown = entry["value"]
        if isinstance(shown, (dict, list)):
            shown = json.dumps(shown, ensure_ascii=False)
        print(f"{entry['status']:<12} {name:<48} {shown}   [{entry.get('sample')}]")
    print(f"\nwrote {rel(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
