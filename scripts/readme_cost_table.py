#!/usr/bin/env python3
"""Writes the README's numbers from web/public/metrics.json, so the README can't drift from the data.

    python scripts/readme_cost_table.py            # prints the blocks
    python scripts/readme_cost_table.py --write    # puts them in README.md
    python scripts/readme_cost_table.py --check    # fails if README.md is out of date

Run scripts/dashboard_metrics.py first. Each block sits between two marker comments in the README:
the Cost section, plus the Reliability and Speed tables, since those quote numbers too.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS_FILE = REPO_ROOT / "web" / "public" / "metrics.json"
README_FILE = REPO_ROOT / "README.md"

# the README's own names for the rows, metric name -> label
REPLACE_ROWS = {
    "research_labs": "Replace: research lab benchtop incubator",
    "legacy_manufacturing": "Replace: legacy manufacturing HMI panel",
    "hardware_qa": "Replace: hardware QA touch screen test robot",
    "building_ops": "Replace: building ops temperature controller",
    "agriculture": "Replace: greenhouse environment controller",
    "commercial_kitchens": "Replace: commercial fryer",
    "warehouses": "Replace: warehouse parcel scale",
    "home_appliances": "Replace: home electric range",
}
# a word from the option's name in docs/costs.md -> label
RETROFIT_ROWS = {
    "Bosch Rexroth": "Retrofit with IoT: a drill press (Bosch Rexroth)",
    "typical": "Retrofit with IoT: legacy equipment, typical",
}
OUTCOME_LABELS = {
    "succeeded": "Worked",
    "stopped": "Stopped by hand",
    "refused": "Refused, nothing pressed",
    "gave_up_after_retries": "Failed, gave up after {attempts} tries",
    "hardware_error": "Failed, hardware error",
    "other_failure": "Failed, other",
}
SPEED_ROWS = {
    "real.press_time_median_s": "One press",
    "real.screen_read_time_median_s": "One screen read (2 or 3 model calls)",
    "real.planner_time_median_s": "Reading T1 and planning, together",
    "real.risk_scoring_time_median_s": "Risk score, in the background",
}
# what the Rig parts line says while the Parts table still has rows with no price. Only used
# while the entered total is still this figure, so a new price can't leave it out of date.
PARTS_ENTERED_SO_FAR = 17.02
PARTS_PRICED_SO_FAR = ("$17.02 priced so far (servos, webcam, ESP32), "
                       "plus wires, breadboard and finger materials not yet priced")


def money(amount):
    return f"${amount:,.2f}"


def price_range(value):
    if value["low"] == value["high"]:
        return money(value["low"])
    return f"{money(value['low'])} to {money(value['high'])}"


def not_sourced(entry):
    """'not sourced yet', with what's missing when the metric says."""
    if entry and entry.get("need"):
        return f"not sourced yet ({entry['need']})"
    return "not sourced yet"


def parts_line(metrics):
    entry = metrics.get("cost.hardware_parts_total") or {}
    if entry.get("status") == "REAL":
        return (f"**Rig parts:** {money(entry['counted_total'])} in parts at listed prices, "
                "plus a power bank and USB cable we already had.")
    if entry.get("entered_total") == PARTS_ENTERED_SO_FAR:
        return f"**Rig parts:** {PARTS_PRICED_SO_FAR}."
    return f"**Rig parts:** {entry.get('status', 'NEEDS INPUT')}. Still needs {entry.get('need', 'the Parts table')}."


def comparison_line(metrics):
    entry = metrics.get("cost.parts_vs_replace.research_labs") or {}
    if entry.get("status") == "REAL":
        return f"**Rig vs replacing a lab incubator:** {entry['value']} ({entry.get('note', '')})."
    return f"**Rig vs replacing a lab incubator:** {not_sourced(entry)}."


def api_cost_cell(metrics):
    entry = metrics.get("cost.api_cost_per_job_list_price") or {}
    if entry.get("status") != "REAL":
        return not_sourced(entry)
    parts = []
    for kind, amount in entry["value"].items():
        if kind != "all":
            parts.append(f"{kind} ${amount:.4f}")
    return f"${entry['value']['all']:.4f} median ({', '.join(parts)}), from {entry['sample']}"


def tokens_cell(metrics):
    entry = metrics.get("real.tokens_per_job") or {}
    if entry.get("status") != "REAL":
        return not_sourced(entry)
    parts = []
    for kind, tokens in entry["value"].items():
        if kind != "all":
            parts.append(f"{kind} {tokens['total']:g}")
    return f"{entry['value']['all']['total']:g} median ({', '.join(parts)}), from {entry['sample']}"


def retrofit_label(option):
    for word, label in RETROFIT_ROWS.items():
        if word in option:
            return label
    return f"Retrofit with IoT: {option}"


def cost_block(metrics):
    lines = [parts_line(metrics), "", comparison_line(metrics), "",
             "Model calls were paid with hackathon credits, so the API figures are cost at list price, "
             "not what we spent.", "", "| | Cost |", "|---|---|",
             f"| API cost per job, at list price | {api_cost_cell(metrics)} |",
             f"| Tokens per job, real runs | {tokens_cell(metrics)} |"]
    for slug, label in REPLACE_ROWS.items():
        entry = metrics.get(f"cost.replace_machine.{slug}") or {}
        if entry.get("status") == "REAL":
            lines.append(f"| {label} | {price_range(entry['value'])} |")
        else:
            lines.append(f"| {label} | {not_sourced(entry)} |")
    retrofit = metrics.get("cost.iot_retrofit") or {}
    if retrofit.get("status") == "REAL":
        for row in retrofit["value"]:
            lines.append(f"| {retrofit_label(row['option'])} | {row['cost']} |")
    else:
        lines.append(f"| Retrofit with IoT | {not_sourced(retrofit)} |")
    return "\n".join(lines)


def reliability_block(metrics):
    lines = ["Presses sent directly from Python, with no AI in the loop:", "", "| Test | Worked | Tried |", "|---|---|---|"]
    bench = metrics.get("bench.press_success") or {}
    for row in bench.get("rows") or []:
        lines.append(f"| {row['test']} | {row['ok']} | {row['tried']} |")
    jobs = metrics.get("real.job_success_rate")
    if jobs is None:
        lines.extend(["", "Full jobs through the app on the real rig: no recorded runs in `runs/` yet."])
        return "\n".join(lines)
    outcomes = (metrics.get("real.job_outcomes") or {}).get("value") or {}
    attempts = (metrics.get("config.max_attempts_per_step") or {}).get("value")
    lines.extend(["", f"Full jobs through the app on the real rig: **{jobs['value']} worked** (`real.job_success_rate`).",
                  "", "| Result | Jobs |", "|---|---|"])
    for kind, label in OUTCOME_LABELS.items():
        count = outcomes.get(kind, 0)
        if count or kind == "succeeded":
            lines.append(f"| {label.format(attempts=attempts)} | {count} |")
    return "\n".join(lines)


def speed_block(metrics):
    lines = ["| Part | Median | Sample | How it's measured |", "|---|---|---|---|"]
    for name, label in SPEED_ROWS.items():
        entry = metrics.get(name)
        if entry is None or entry.get("status") != "REAL":
            lines.append(f"| {label} | not measured yet | | |")
        else:
            lines.append(f"| {label} | {entry['value']} s | {entry['sample']} | {entry.get('note', '')} |")
    return "\n".join(lines)


def all_blocks(metrics):
    return {"cost": cost_block(metrics), "reliability": reliability_block(metrics), "speed": speed_block(metrics)}


def put_block(readme, name, block):
    """README text with the block between its markers. The markers have to be there already."""
    start = f"<!-- {name}:start, written by scripts/readme_cost_table.py -->"
    end = f"<!-- {name}:end -->"
    if readme.count(start) != 1 or readme.count(end) != 1:
        raise ValueError(f"README needs one {start} and one {end}")
    before = readme.split(start)[0]
    after = readme.split(end)[1]
    return f"{before}{start}\n{block}\n{end}{after}"


def filled_readme(readme, metrics):
    for name, block in all_blocks(metrics).items():
        readme = put_block(readme, name, block)
    return readme


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the README's numbers from metrics.json")
    parser.add_argument("--write", action="store_true", help="update README.md")
    parser.add_argument("--check", action="store_true", help="fail if README.md is out of date")
    parser.add_argument("--metrics", type=Path, default=METRICS_FILE)
    parser.add_argument("--readme", type=Path, default=README_FILE)
    args = parser.parse_args(argv)

    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))["metrics"]
    if not args.write and not args.check:
        for name, block in all_blocks(metrics).items():
            print(f"--- {name} ---\n{block}\n")
        return 0

    readme = args.readme.read_text(encoding="utf-8")
    updated = filled_readme(readme, metrics)
    if args.check:
        if updated != readme:
            print("README.md is out of date, run scripts/readme_cost_table.py --write", file=sys.stderr)
            return 1
        print("README.md matches metrics.json")
        return 0
    with open(args.readme, "w", encoding="utf-8", newline="") as handle:
        handle.write(updated)
    print(f"wrote {args.readme.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
