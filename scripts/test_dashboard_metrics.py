"""Tests for the parts of dashboard_metrics.py that read step.decision, job.usage, the SAM
summaries and docs/costs.md. Made-up runs in a temp folder, nothing real. Run from the repo root:
    python -m unittest scripts/test_dashboard_metrics.py -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard_metrics as dm  # noqa: E402


def decision(kind, approval_type, ms, decided_at):
    return {"decision": kind, "approval_type": approval_type, "time_to_decide_ms": ms,
            "decided_by": "Pam", "decided_at": decided_at, "on_reject": "pause" if kind == "reject" else None}


def step(step_id, status="succeeded", decided=None):
    entry = {"step_id": step_id, "index": int(step_id[1:]), "intent": "x", "requires_approval": decided is not None,
             "action": {"type": "press", "control_id": "btn_h", "count": 1}, "expect": {"control_id": "screen_t1"},
             "status": status, "attempts": 1, "started_at": "2026-11-03T10:00:01.000Z",
             "finished_at": "2026-11-03T10:00:03.000Z", "evidence": {}, "error": None}
    if decided is not None:
        entry["decision"] = decided
    return entry


def write_run(runs_dir, job_id, decisions, usage):
    """A finished job whose s1 got `decisions` in order. usage=None: recorded before job.usage."""
    events = [{"type": "job.status", "job_id": job_id, "ts": "2026-11-03T10:00:00.000Z", "status": "planning"}]
    for number, each in enumerate(decisions):
        # the decision event, then a later event of the same step carrying the same decision
        for repeat in range(2):
            events.append({"type": "step.updated", "job_id": job_id, "ts": f"2026-11-03T10:00:0{number}.{repeat}00Z",
                           "step": step("s1", "awaiting_approval", each)})
    events.append({"type": "job.finished", "job_id": job_id, "ts": "2026-11-03T10:00:09.000Z",
                   "result": {"outcome": "succeeded", "message": "done", "final_screen_text": None}})
    job = {"job_id": job_id, "created_at": "2026-11-03T10:00:00.000Z", "status": "succeeded",
           "result": {"outcome": "succeeded", "message": "done", "final_screen_text": None},
           "plan": {"steps": [step("s1", decided=decisions[-1] if decisions else None), step("s2")]}}
    if usage is not None:
        job["usage"] = usage
    folder = runs_dir / job_id
    folder.mkdir(parents=True)
    (folder / "job.json").write_text(json.dumps(job), encoding="utf-8")
    (folder / "events.json").write_text(json.dumps(events), encoding="utf-8")


def call(model, prompt, completion):
    return {"model": model, "prompt_tokens": prompt, "completion_tokens": completion, "seconds": 1.0}


class RunMetricsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.runs_dir = Path(self.folder.name)
        planner = "muse-spark-1.3"
        scorer = "meta-models/muse-spark-open-1026"
        # job a: rejected (tap, 3 s), then approved (hold, 5 s). job b: one hold approval, 2 s.
        write_run(self.runs_dir, "job_a", [decision("reject", "tap", 3000, "2026-11-03T10:00:00Z"),
                                           decision("approve", "hold", 5000, "2026-11-03T10:00:01Z")],
                  [call(planner, 1000, 2000), call(scorer, 1500, 300)])
        write_run(self.runs_dir, "job_b", [decision("approve", "hold", 2000, "2026-11-03T11:00:00Z")],
                  [call(planner, 1200, 1800), call(scorer, 1500, 200)])
        write_run(self.runs_dir, "job_c", [], None)  # recorded before job.usage and step.decision
        write_run(self.runs_dir, "job_m", [decision("approve", "tap", 900, "2026-11-03T12:00:00Z")],
                  [call(planner, 1, 1)])
        (self.runs_dir / "labels.json").write_text(
            json.dumps({"job_a": "real", "job_b": "real", "job_c": "real", "job_m": "mock"}), encoding="utf-8")
        self.runs = dm.load_runs(self.runs_dir)
        self.real = dm.run_metrics(self.runs, "real")
        self.mock = dm.run_metrics(self.runs, "mock")

    def tearDown(self):
        self.folder.cleanup()

    def test_each_decision_counts_once(self):
        # 3 real decisions (the repeats on later events don't count again): 2000, 3000, 5000 ms
        self.assertEqual(self.real["real.time_to_decide_median_ms"]["value"], 3000)
        self.assertEqual(self.real["real.time_to_decide_median_ms"]["sample"], "3 decisions with a time")
        self.assertEqual(self.real["real.approval_type_mix"]["value"], {"tap": 1, "hold": 2, "not sent": 0})
        self.assertIn("2 approvals, 1 rejections", self.real["real.time_to_decide_median_ms"]["note"])

    def test_tokens_per_job_real_only(self):
        tokens = self.real["real.tokens_per_job"]
        # job a: 2500 prompt, 2300 completion. job b: 2700, 2000. job c has no usage, left out
        self.assertEqual(tokens["value"], {"prompt": 2600, "completion": 2150, "total": 4750})
        self.assertEqual(tokens["sample"], "2 jobs")
        self.assertIn("1 jobs recorded before job.usage existed are left out", tokens["note"])
        self.assertNotIn("mock.tokens_per_job", self.mock)

    def test_mock_stays_mock(self):
        self.assertEqual(self.mock["mock.time_to_decide_median_ms"]["value"], 900)
        self.assertEqual(self.mock["mock.time_to_decide_median_ms"]["status"], "MOCK")

    def test_build_drops_the_placeholder_once_there_is_data(self):
        output = dm.build(self.runs_dir, Path(self.folder.name) / "metrics.json")
        self.assertEqual(output["metrics"]["real.time_to_decide_median_ms"]["status"], "REAL")
        self.assertNotIn("vision.time_to_decide_ms_as_sent", output["metrics"])


class SamSummaryTests(unittest.TestCase):
    def test_reads_controls_and_average_confidence(self):
        with tempfile.TemporaryDirectory() as folder:
            summary = {"device_id": "timer-01", "frame_file": "rig.jpg", "frame_size": [1280, 720],
                       "timestamp": "2026-11-03T10:00:00Z", "model": "sam-3.1", "from_saved_response": False,
                       "controls": [{"label": "button_1", "bbox": [1, 2, 3, 4], "confidence": 0.9},
                                    {"label": "screen_1", "bbox": [5, 6, 7, 8], "confidence": 0.8}]}
            (Path(folder) / "timer-01.sam_summary.json").write_text(json.dumps(summary), encoding="utf-8")
            found = dm.sam_summaries(folder)
        self.assertEqual(found["timer-01"]["controls"], 2)
        self.assertEqual(found["timer-01"]["average_confidence"], 0.85)


COSTS = """# Costs

## Parts

| Item | Qty | Unit price | Total | Store | Listing URL | Note |
|---|---|---|---|---|---|---|
| SG90 servo | 2 | $3 | $6 | Amazon | https://example.com/sg90 | 2 of 10 from a $15 pack |
| USB webcam | 1 | $4 | | AliExpress | https://example.com/cam | |
| ESP32 | | | $7 | | order 113-555 | |
| Misc | | | $3 | | | |
| Finger materials | | | | | | |

## Comparison

| Option | Cost | Source URL |
|---|---|---|
| Buy a new 4-channel timer | $1,299.00 | https://example.com/timer |
| Replace the lab machine | 2000 | |
| IoT retrofit of the lab machine | | |
"""


class CostsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        path = Path(self.folder.name) / "costs.md"
        path.write_text(COSTS, encoding="utf-8")
        self.parts, self.comparison = dm.cost_tables(path)

    def tearDown(self):
        self.folder.cleanup()

    def part(self, entry, item):
        for row in entry["parts"]:
            if row["item"] == item:
                return row
        self.fail(f"no row {item}")

    def test_tables(self):
        self.assertEqual(len(self.parts), 5)
        self.assertEqual(self.parts[0]["listing url"], "https://example.com/sg90")
        self.assertEqual(self.parts[0]["note"], "2 of 10 from a $15 pack")

    def test_money_never_guesses(self):
        self.assertEqual(dm.money("$1,299.00"), 1299.0)
        self.assertEqual(dm.money("8.50"), 8.5)
        self.assertIsNone(dm.money(""))
        self.assertIsNone(dm.money("about 10"))

    def test_proven_only_with_a_listing_url(self):
        entry = dm.parts_metric(self.parts)
        self.assertTrue(self.part(entry, "SG90 servo")["proven"])
        self.assertTrue(self.part(entry, "USB webcam")["proven"])
        self.assertEqual(self.part(entry, "USB webcam")["cost"], 4.0)  # blank Total: Qty x Unit price
        self.assertFalse(self.part(entry, "ESP32")["proven"])  # text that isn't a URL doesn't count
        self.assertFalse(self.part(entry, "Misc")["proven"])
        self.assertIn("ESP32: listing isn't a URL", entry["need"])

    def test_total_stays_needs_input_until_everything_is_listed(self):
        entry = dm.parts_metric(self.parts)
        self.assertEqual(entry["status"], "NEEDS INPUT")
        self.assertIsNone(entry["value"])
        self.assertEqual(entry["entered_total"], 20.0)  # 6 + 4 + 7 + 3, finger materials have no price
        self.assertEqual(entry["proven_total"], 10.0)  # servos + webcam
        self.assertIn("a price for: Finger materials", entry["need"])

        # every row priced and listed -> REAL
        done = []
        for row in self.parts:
            if row["item"] != "Finger materials":
                done.append(dict(row, **{"listing url": "https://example.com/x"}))
        entry = dm.parts_metric(done)
        self.assertEqual(entry["status"], "REAL")
        self.assertEqual(entry["value"], 20.0)

    def test_qty_times_unit_has_to_match_the_total(self):
        rows = [{"item": "Servo", "qty": "2", "unit price": "$3", "total": "$7", "listing url": "https://example.com/s"}]
        entry = dm.parts_metric(rows)
        self.assertEqual(entry["status"], "NEEDS INPUT")
        self.assertIn("Servo: 2 x $3 isn't $7", entry["need"])

    def test_a_cost_counts_only_with_a_source(self):
        replace = dm.comparison_metric(self.comparison, "replace", "need")
        self.assertEqual(replace["status"], "REAL")
        self.assertEqual(replace["value"], [{"option": "Buy a new 4-channel timer", "cost": 1299.0,
                                             "source": "https://example.com/timer"}])
        retrofit = dm.comparison_metric(self.comparison, "retrofit", "need")
        self.assertEqual(retrofit["status"], "NEEDS INPUT")

    def test_the_repo_costs_file(self):
        parts, _comparison = dm.cost_tables(dm.REPO_ROOT / "docs" / "costs.md")
        items = []
        for row in parts:
            items.append(row["item"].lower())
        self.assertFalse(any("timer" in item for item in items), "the timer is the machine, not a part")
        self.assertFalse(any("power bank" in item for item in items), "optional, left out")
        for row in parts:
            # store and listing are for Bhavi to fill in, never made up here
            self.assertEqual((row["store"], row["listing url"]), ("", ""), row)
        entry = dm.parts_metric(parts)
        self.assertEqual(entry["entered_total"], 20.0)  # $6 + $4 + $7 + $3, as given
        self.assertEqual(entry["proven_total"], 0)


if __name__ == "__main__":
    unittest.main()
