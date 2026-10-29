#!/usr/bin/env python3
"""Score every step of a job and print tier + reason for each.

Usage:
    python risk/score_job.py shared/fixtures/jobSample.json
    python risk/score_job.py risk/fixtures/incubator_start_90.json

The job with risk_tier / risk_reason / risk_scored_by added to each step gets saved to
risk/out/<job file name>.scored.json (committed). Raw model replies (and the context we
sent) go to risk/out/<job file name>.raw.json for debugging (gitignored).
"""

import json
import sys
from pathlib import Path

import scorer

OUT_DIR = Path(__file__).resolve().parent / "out"


def main():
    if len(sys.argv) != 2:
        print("usage: python risk/score_job.py <job.json>", file=sys.stderr)
        return 2

    job_path = Path(sys.argv[1])
    job = json.loads(job_path.read_text(encoding="utf-8"))
    steps = (job.get("plan") or {}).get("steps") or []

    print(f"Job: {job.get('request')}")
    print(f"Scored by: {scorer.MODEL}")
    print()

    raw_records = []
    scored_steps = []
    fallback_count = 0
    for step in steps:
        score, raw = scorer.score_step_with_raw(job, step)
        raw_records.append(raw)

        # copy the step and tack the score on, the plan's own fields stay as they were
        scored_step = dict(step)
        scored_step.update(score)
        scored_steps.append(scored_step)

        # a fallback still says MODEL in risk_scored_by, so check the error and reason too
        if raw.get("error") or score["risk_reason"] == scorer.FALLBACK_REASON:
            fallback_count += 1

        # the scorer can't remove a human check: requires_approval comes straight from the plan
        gate = "  [needs approval]" if step.get("requires_approval") else ""
        tier = score["risk_tier"].upper()
        print(f"{step.get('step_id'):>4}  {tier:<6}  {step.get('intent')}{gate}")
        print(f"      {score['risk_reason']}")
        if raw.get("error"):
            print(f"      (scorer error: {raw['error']})")

    print()
    print(f"JSON path that worked: {scorer.working_path or 'none (every call failed)'}")

    OUT_DIR.mkdir(exist_ok=True)
    raw_path = OUT_DIR / f"{job_path.stem}.raw.json"
    raw_path.write_text(json.dumps(raw_records, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Raw replies saved to {raw_path}")

    scored_job = dict(job)
    scored_job["plan"] = dict(job.get("plan") or {})
    scored_job["plan"]["steps"] = scored_steps
    scored_path = OUT_DIR / f"{job_path.stem}.scored.json"
    scored_path.write_text(json.dumps(scored_job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Scored job saved to {scored_path}")

    if fallback_count:
        print(f"WARNING: {fallback_count} step(s) fell back to high, see the raw file", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
