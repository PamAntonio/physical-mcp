#!/usr/bin/env python3
"""Score every step of a job and print tier + reason for each.

Usage:
    python risk/score_job.py shared/fixtures/jobSample.json
    python risk/score_job.py risk/fixtures/incubator_start_90.json

Raw model replies (and the context we sent) get saved to
risk/out/<job file name>.raw.json for debugging. That folder is gitignored.
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
    for step in steps:
        score, raw = scorer.score_step_with_raw(job, step)
        raw_records.append(raw)

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
