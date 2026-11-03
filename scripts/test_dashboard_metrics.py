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


def call(kind, prompt, completion):
    model = "meta-models/muse-spark-open-1026" if kind == "scorer" else "muse-spark-1.3"
    return {"kind": kind, "model": model, "prompt_tokens": prompt, "completion_tokens": completion, "seconds": 1.0}


class RunMetricsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.runs_dir = Path(self.folder.name)
        # job a: rejected (tap, 3 s), then approved (hold, 5 s). job b: one hold approval, 2 s.
        write_run(self.runs_dir, "job_a", [decision("reject", "tap", 3000, "2026-11-03T10:00:00Z"),
                                           decision("approve", "hold", 5000, "2026-11-03T10:00:01Z")],
                  [call("planner", 1000, 2000), call("screen_read", 200, 700), call("screen_read", 200, 700),
                   call("scorer", 1500, 300)])
        write_run(self.runs_dir, "job_b", [decision("approve", "hold", 2000, "2026-11-03T11:00:00Z")],
                  [call("planner", 1200, 1800), call("screen_read", 200, 700), call("screen_read", 200, 700),
                   call("screen_read", 200, 700), call("scorer", 1500, 200)])
        write_run(self.runs_dir, "job_c", [], None)  # recorded before job.usage and step.decision
        write_run(self.runs_dir, "job_m", [decision("approve", "tap", 900, "2026-11-03T12:00:00Z")],
                  [call("planner", 1, 1)])
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
        # medians of jobs a and b (job c has no usage, left out). screen reads: a made 2 calls
        # (400 prompt, 1400 completion), b made 3 (600, 2100)
        self.assertEqual(tokens["value"], {
            "planner": {"prompt": 1100, "completion": 1900, "total": 3000},
            "screen_read": {"prompt": 500, "completion": 1750, "total": 2250},
            "scorer": {"prompt": 1500, "completion": 250, "total": 1750},
            "all": {"prompt": 3100, "completion": 3900, "total": 7000},
        })
        self.assertEqual(tokens["sample"], "2 jobs")
        self.assertIn("2 planner, 5 screen_read, 2 scorer calls", tokens["note"])
        self.assertIn("1 jobs recorded before job.usage existed are left out", tokens["note"])
        self.assertNotIn("mock.tokens_per_job", self.mock)

    def test_entries_from_before_kind_are_kept_apart(self):
        run = {"job": {"usage": [{"model": "muse-spark-1.3", "prompt_tokens": 10, "completion_tokens": 20},
                                 call("scorer", 5, 5)]}}
        tokens = dm.job_tokens(run)
        self.assertEqual(tokens["not labelled"], (10, 20, 1))
        self.assertEqual(tokens["scorer"], (5, 5, 1))
        self.assertEqual(tokens["all"], (15, 25, 2))

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

| Item | Qty | Unit price | Total | Store | Listing URL (price as listed, checked &lt;date&gt;) | Checked | Note |
|---|---|---|---|---|---|---|---|
| SG90 servo | 2 | $3 | $6 | Amazon | https://example.com/sg90 | 2026-11-02 | 2 of 10 from a $15 pack |
| USB webcam | 1 | $4 | | AliExpress | https://example.com/cam | 2026-11-02 | |
| ESP32 | | | $7 | | order 113-555 | | |
| Misc | | | $3 | | https://example.com/misc | | |
| Finger materials | | | | | | | |
| Power bank | | | | | | | already owned, not counted |

## Replacing the machine

| Card | Example machine | Replacement cost range | Source URL | Date checked |
|---|---|---|---|---|
| Research labs (incubators, shakers) | A 37 L benchtop incubator | $798.59 to $1,185.00 | https://example.com/incubators | 2026-11-02 |
| Commercial kitchens (ovens and fryers) | A 50 lb fryer | $6,765.00 | | 2026-11-02 |
| Home appliances | | | | |

## Retrofitting with IoT

| Option | Cost added | Source URL | Date checked | What the source says |
|---|---|---|---|---|
| Drill press retrofit | about $2,000 | https://example.com/drill | 2026-11-02 | "about $2,000" |
| Some other retrofit | a lot | https://example.com/other | 2026-11-02 | |
"""


class CostsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        path = Path(self.folder.name) / "costs.md"
        path.write_text(COSTS, encoding="utf-8")
        self.tables = dm.cost_tables(path)
        self.parts = self.tables["parts"]

    def tearDown(self):
        self.folder.cleanup()

    def part(self, entry, item):
        for row in entry["parts"]:
            if row["item"] == item:
                return row
        self.fail(f"no row {item}")

    def test_tables(self):
        self.assertEqual(len(self.parts), 6)
        self.assertEqual(dm.listing_cell(self.parts[0]), "https://example.com/sg90")
        self.assertEqual(self.parts[0]["checked"], "2026-11-02")
        self.assertEqual(self.parts[0]["note"], "2 of 10 from a $15 pack")

    def test_money_never_guesses(self):
        self.assertEqual(dm.money("$1,299.00"), 1299.0)
        self.assertEqual(dm.money("8.50"), 8.5)
        self.assertIsNone(dm.money(""))
        self.assertIsNone(dm.money("about 10"))

    def test_proven_only_with_a_listing_url_and_a_checked_date(self):
        entry = dm.parts_metric(self.parts)
        self.assertTrue(self.part(entry, "SG90 servo")["proven"])
        self.assertTrue(self.part(entry, "USB webcam")["proven"])
        self.assertEqual(self.part(entry, "USB webcam")["cost"], 4.0)  # blank Total: Qty x Unit price
        self.assertFalse(self.part(entry, "ESP32")["proven"])  # text that isn't a URL doesn't count
        self.assertFalse(self.part(entry, "Misc")["proven"])  # a URL but no checked date
        self.assertIn("ESP32: listing isn't a URL", entry["need"])
        self.assertIn("a listing URL and a checked date for: ESP32, Misc", entry["need"])

        rows = [{"item": "Servo", "total": "$6", "listing url (price as listed)": "https://example.com/s",
                 "checked": "last week"}]
        self.assertIn("checked date isn't YYYY-MM-DD", dm.parts_metric(rows)["need"])

    def test_already_owned_is_left_out_not_blocking(self):
        entry = dm.parts_metric(self.parts)
        self.assertEqual(entry["already_owned"], ["Power bank"])
        self.assertNotIn("Power bank", entry["need"])  # no price or listing asked for
        self.assertIn("1 item already owned", entry["sample"])

    def test_already_owned_note_wording(self):
        self.assertTrue(dm.is_already_owned("already owned (we had them at home), not counted"))
        self.assertTrue(dm.is_already_owned("already owned, not counted"))
        self.assertFalse(dm.is_already_owned("plus $6.39 shipping, not counted"))
        self.assertFalse(dm.is_already_owned(""))

    def test_total_stays_needs_input_until_everything_is_listed(self):
        entry = dm.parts_metric(self.parts)
        self.assertEqual(entry["status"], "NEEDS INPUT")
        self.assertIsNone(entry["value"])
        self.assertEqual(entry["entered_total"], 20.0)  # 6 + 4 + 7 + 3, finger materials have no price
        self.assertEqual(entry["proven_total"], 10.0)  # servos + webcam
        self.assertIn("a price for: Finger materials", entry["need"])

        # every counted row priced, listed and checked -> REAL, the owned one doesn't hold it back
        done = []
        for row in self.parts:
            if row["item"] == "Finger materials":
                continue
            if row["item"] != "Power bank":
                row = dict(row, **{"listing url (price as listed, checked &lt;date&gt;)": "https://example.com/x",
                                   "checked": "2026-11-02"})
            done.append(row)
        entry = dm.parts_metric(done)
        self.assertEqual(entry["status"], "REAL")
        self.assertEqual(entry["value"], "$20.00 counted + 1 item already owned")
        self.assertEqual(entry["counted_total"], 20.0)

    def test_qty_times_unit_has_to_match_the_total(self):
        rows = [{"item": "Servo", "qty": "2", "unit price": "$3", "total": "$7", "listing url": "https://example.com/s",
                 "checked": "2026-11-02"}]
        entry = dm.parts_metric(rows)
        self.assertEqual(entry["status"], "NEEDS INPUT")
        self.assertIn("Servo: 2 x $3 isn't $7", entry["need"])

    def test_dollar_amounts_only_take_dollar_figures(self):
        self.assertEqual(dm.dollar_amounts("$798.59 to $1,185.00"), [798.59, 1185.0])
        self.assertEqual(dm.dollar_amounts("a couple thousand dollars, up to $10,000 to $20,000"), [10000.0, 20000.0])
        self.assertEqual(dm.dollar_amounts('50 lb fryer, 10" panel'), [])
        self.assertEqual(dm.card_slug("Research labs (incubators, shakers)"), "research_labs")
        self.assertEqual(dm.card_slug("Hardware QA (pressing device buttons)"), "hardware_qa")

    def test_one_replacement_metric_per_card(self):
        found = dm.replace_metrics(self.tables["replace"])
        self.assertEqual(sorted(found), ["cost.replace_machine.commercial_kitchens",
                                         "cost.replace_machine.home_appliances",
                                         "cost.replace_machine.research_labs"])
        labs = found["cost.replace_machine.research_labs"]
        self.assertEqual(labs["status"], "REAL")
        self.assertEqual(labs["value"], {"low": 798.59, "high": 1185.0})
        # a price with no source URL doesn't count, and a blank row stays blank
        kitchens = found["cost.replace_machine.commercial_kitchens"]
        self.assertEqual(kitchens["status"], "NEEDS INPUT")
        self.assertIn("a source URL", kitchens["need"])
        self.assertIsNone(found["cost.replace_machine.home_appliances"]["value"])

    def test_retrofit_needs_a_figure_and_a_source(self):
        entry = dm.retrofit_metric(self.tables["retrofit"])
        self.assertEqual(entry["status"], "REAL")
        self.assertEqual(len(entry["value"]), 1)
        self.assertEqual(entry["value"][0]["amounts"], [2000.0])
        self.assertIn("Some other retrofit", entry["note"])  # "a lot" isn't a figure

    def test_cheaper_only_when_both_sides_are_real(self):
        labs = dm.replace_metrics(self.tables["replace"])["cost.replace_machine.research_labs"]
        # the parts total here is NEEDS INPUT (no listings everywhere), so no ratio
        waiting = dm.cheaper_by(dm.parts_metric(self.parts), labs, "research_labs")
        self.assertEqual(waiting["status"], "NEEDS INPUT")
        self.assertIsNone(waiting["value"])
        self.assertIn("parts total", waiting["need"])

        parts_total = {"status": "REAL", "value": "$20.00 counted + 2 items already owned", "counted_total": 20.0}
        entry = dm.cheaper_by(parts_total, labs, "research_labs")
        self.assertEqual(entry["status"], "REAL")
        self.assertEqual(entry["value"], "40x cheaper")  # 798.59 / 20
        self.assertEqual(entry["ratio"], 39.93)
        self.assertEqual(dm.cheaper_by(parts_total, {"status": "REAL", "value": {"low": 50.0}, "example": "x"},
                                       "x")["value"], "2.5x cheaper")
        self.assertEqual(dm.cheaper_by(parts_total, None, "nope")["status"], "NEEDS INPUT")

    def test_the_repo_costs_file(self):
        tables = dm.cost_tables(dm.REPO_ROOT / "docs" / "costs.md")
        parts = tables["parts"]
        items = []
        for row in parts:
            items.append(row["item"].lower())
        self.assertFalse(any("timer" in item for item in items), "the timer is the machine, not a part")
        entry = dm.parts_metric(parts)
        # a filled-in listing always comes with its date, a half-filled row is a typo
        for row in parts:
            self.assertEqual(bool(dm.listing_cell(row)), bool(row["checked"]), row)
        self.assertEqual(len(entry["already_owned"]), 2)  # the power bank and the cut USB cable
        # the total only goes REAL with no row left waiting
        if entry["status"] != "REAL":
            self.assertIsNone(entry["value"])
            self.assertTrue(entry["need"])

        # one row per Where It Fits card, every one with a price and a source URL
        cards = dm.replace_metrics(tables["replace"])
        self.assertEqual(len(cards), 8)
        for name, card in cards.items():
            self.assertEqual(card["status"], "REAL", name)
            self.assertTrue(card["source"].startswith("https://"), name)
        # no struck-through list price: the warehouses row is the parcel scale's real price
        self.assertEqual(cards["cost.replace_machine.warehouses"]["value"], {"low": 945.0, "high": 945.0})


if __name__ == "__main__":
    unittest.main()
