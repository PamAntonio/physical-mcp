"""Tests for readme_cost_table.py. Made-up metrics, plus one check that the real README is in sync.
    python -m unittest scripts/test_readme_cost_table.py -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import readme_cost_table as table  # noqa: E402


def waiting_metrics():
    metrics = {
        "cost.hardware_parts_total": {"status": "NEEDS INPUT", "value": None, "need": "a price for: Wires"},
        "cost.parts_vs_replace.research_labs": {"status": "NEEDS INPUT", "value": None, "need": "the parts total"},
        "cost.api_cost_per_job_list_price": {"status": "NEEDS DATA", "value": None, "need": "runs with usage"},
        "real.tokens_per_job": {"status": "NEEDS DATA", "value": None, "need": "runs with usage"},
        "cost.iot_retrofit": {"status": "REAL", "value": [
            {"option": "Bosch Rexroth retrofit", "cost": "about $2,000"},
            {"option": "Retrofitting legacy equipment, typical range", "cost": "up to $20,000"}]},
    }
    for slug in table.REPLACE_ROWS:
        metrics[f"cost.replace_machine.{slug}"] = {"status": "REAL", "value": {"low": 1718.0, "high": 1718.0}}
    metrics["cost.replace_machine.research_labs"]["value"] = {"low": 798.59, "high": 1185.0}
    return metrics


class CostBlockTests(unittest.TestCase):
    def test_waiting_says_so_and_shows_no_number(self):
        block = table.cost_block(waiting_metrics())
        self.assertIn("**Rig parts:** NEEDS INPUT. Still needs a price for: Wires.", block)
        self.assertIn("not sourced yet (the parts total)", block)
        self.assertNotIn("cheaper", block)
        self.assertIn("| API cost per job, at list price | not sourced yet (runs with usage) |", block)
        self.assertIn("| Tokens per job, real runs | not sourced yet (runs with usage) |", block)

    def test_keeps_the_eight_replacement_rows_and_two_retrofit_rows(self):
        block = table.cost_block(waiting_metrics())
        self.assertEqual(block.count("| Replace: "), 8)
        self.assertEqual(block.count("| Retrofit with IoT: "), 2)
        self.assertIn("| Replace: research lab benchtop incubator | $798.59 to $1,185.00 |", block)
        self.assertIn("| Replace: commercial fryer | $1,718.00 |", block)
        self.assertIn("| Retrofit with IoT: a drill press (Bosch Rexroth) | about $2,000 |", block)

    def test_says_hackathon_credits_once(self):
        self.assertEqual(table.cost_block(waiting_metrics()).count("hackathon credits"), 1)

    def test_real_numbers_show_up(self):
        metrics = waiting_metrics()
        metrics["cost.hardware_parts_total"] = {"status": "REAL", "value": "x", "counted_total": 20.5}
        metrics["cost.parts_vs_replace.research_labs"] = {"status": "REAL", "value": "38x cheaper", "note": "the math"}
        metrics["cost.api_cost_per_job_list_price"] = {
            "status": "REAL", "sample": "3 jobs", "value": {"all": 0.0123, "planner": 0.01, "scorer": 0.0023}}
        metrics["real.tokens_per_job"] = {
            "status": "REAL", "sample": "3 jobs", "value": {"planner": {"total": 4000.0}, "all": {"total": 9000.0}}}
        block = table.cost_block(metrics)
        self.assertIn("$20.50 in parts at listed prices, plus a power bank and USB cable we already had", block)
        self.assertNotIn("we paid", block)
        self.assertIn("38x cheaper", block)
        self.assertIn("$0.0123 median (planner $0.0100, scorer $0.0023), from 3 jobs", block)
        self.assertIn("9000 median (planner 4000), from 3 jobs", block)


class RunBlocksTests(unittest.TestCase):
    def test_no_real_runs(self):
        block = table.reliability_block({})
        self.assertIn("no recorded runs", block)
        self.assertIn("not measured yet", table.speed_block({}))

    def test_outcomes_and_speed_come_from_the_metrics(self):
        metrics = {
            "bench.press_success": {"rows": [{"test": "H", "ok": 9, "tried": 10}]},
            "real.job_success_rate": {"value": "1/4"},
            "real.job_outcomes": {"value": {"succeeded": 1, "stopped": 2, "gave_up_after_retries": 1}},
            "config.max_attempts_per_step": {"value": 7},
            "real.screen_read_time_median_s": {"status": "REAL", "value": 12.5, "sample": "6 reads", "note": "how"},
        }
        block = table.reliability_block(metrics)
        self.assertIn("| H | 9 | 10 |", block)
        self.assertIn("**1/4 worked**", block)
        self.assertIn("| Failed, gave up after 7 tries | 1 |", block)
        self.assertNotIn("hardware error", block)
        self.assertIn("| One screen read (2 or 3 model calls) | 12.5 s | 6 reads | how |", table.speed_block(metrics))


class WriteTests(unittest.TestCase):
    def test_put_block_needs_its_markers(self):
        with self.assertRaises(ValueError):
            table.put_block("no markers here", "cost", "x")

    def test_write_is_stable_and_check_catches_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            readme = Path(folder) / "README.md"
            parts = ["# Title\n"]
            for name in ("cost", "reliability", "speed"):
                parts.append(f"<!-- {name}:start, written by scripts/readme_cost_table.py -->\nold\n<!-- {name}:end -->\n")
            readme.write_text("".join(parts), encoding="utf-8")
            arguments = ["--metrics", str(table.METRICS_FILE), "--readme", str(readme)]

            self.assertEqual(table.main(arguments + ["--check"]), 1)
            self.assertEqual(table.main(arguments + ["--write"]), 0)
            once = readme.read_text(encoding="utf-8")
            self.assertEqual(table.main(arguments + ["--write"]), 0)
            self.assertEqual(readme.read_text(encoding="utf-8"), once)
            self.assertEqual(table.main(arguments + ["--check"]), 0)

    def test_the_repo_readme_matches_the_repo_metrics(self):
        self.assertEqual(table.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
