#!/usr/bin/env python3
"""Physical MCP planner.

Usage:
    python backend/planner.py "Set the incubator to 37 °C and run it for 2 hours"

Reads the device (with its controls) from shared/fixtures/jobSample.json,
asks Muse Spark (via Meta's Model API) for a minimal plan, fills in the
runtime fields so the result matches the Plan type in shared/types.ts,
validates it, prints it as pretty JSON, and saves it to backend/out/plan.json.
"""

import json
import os
import sys
from pathlib import Path

from openai import OpenAI

BASE_URL = "https://api.meta.ai/v1"
MODEL = "muse-spark-1.3"

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "shared" / "fixtures" / "jobSample.json"
OUT_PATH = Path(__file__).resolve().parent / "out" / "plan.json"

SYSTEM_PROMPT = (
    "You are the planner for an agent that operates a physical device by pressing "
    "its buttons. Return JSON only. You may only use control_ids from the supplied "
    "controls. Each step has one action (press, wait or read_screen), an \"expect\" "
    "(text the screen must contain afterwards), and requires_approval true only for "
    "presses that change what the machine physically does (start, stop, eject, or "
    "any control marked risky), not for adjusting a setting on the screen like SET, "
    "▲, ▼ or ENTER. Keep the plan minimal."
)

ALLOWED_ACTIONS = {"press", "wait", "read_screen"}


def load_device():
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    device = data.get("device")
    if not isinstance(device, dict) or not isinstance(device.get("controls"), list):
        raise ValueError(f"{FIXTURE_PATH} does not contain a device with controls")
    return device


def build_messages(device, request):
    user_prompt = (
        "Plan the following request for the supplied device.\n\n"
        f"Request: {request}\n\n"
        f"Device (with controls):\n{json.dumps(device, ensure_ascii=False, indent=2)}\n\n"
        "Return JSON only, with exactly this shape and no other fields:\n"
        "{\n"
        '  "summary": "<one-line summary of the plan>",\n'
        '  "steps": [\n'
        "    {\n"
        '      "intent": "<human-readable intent>",\n'
        '      "action": {"type": "press", "control_id": "<control_id>", "count": 1}\n'
        '                | {"type": "wait", "seconds": <number>}\n'
        '                | {"type": "read_screen", "control_id": "<control_id>"},\n'
        '      "expect": {"control_id": "<screen control_id>", "text_contains": "<text the screen must contain afterwards>"},\n'
        '      "requires_approval": true | false,\n'
        '      "approval_reason": "<reason; include only when requires_approval is true>"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Use only the planning fields above (intent, action, expect, "
        "requires_approval, approval_reason). Do not include runtime fields such "
        "as step_id, index, status, attempts, timestamps, evidence, or error."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _is_response_format_rejection(exc):
    text = str(exc).lower()
    if "response_format" in text or "response format" in text:
        return True
    # A 400 from the first call is most likely the unsupported-parameter
    # rejection; retrying once without response_format is harmless.
    return getattr(exc, "status_code", None) == 400


def _extract_content(response):
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError(f"model response has no message content: {exc}") from exc
    if not content or not str(content).strip():
        raise ValueError("model returned an empty response")
    return str(content)


def call_model(client, messages):
    """Call the model; retry once without response_format if it is rejected."""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        if not _is_response_format_rejection(exc):
            raise
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
        )
    return _extract_content(response)


def parse_planning_json(text):
    """Parse JSON from the model text, tolerating fences / surrounding prose."""
    stripped = text.strip()
    # Strip a markdown code fence if present.
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("model response is not valid JSON and contains no JSON object")
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"model response is not valid JSON: {exc}") from exc


def validate_and_build_plan(raw, device):
    """Return (plan, errors). plan is None when errors is non-empty."""
    errors = []
    controls = {c.get("control_id"): c for c in device.get("controls", []) if isinstance(c, dict)}

    if not isinstance(raw, dict):
        return None, ["model output must be a JSON object with 'summary' and 'steps'"]

    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("plan: 'summary' must be a non-empty string")

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        errors.append("plan: 'steps' must be a non-empty list")
        return None, errors

    steps = []
    for pos, raw_step in enumerate(raw_steps, start=1):
        label = f"step {pos}"
        if not isinstance(raw_step, dict):
            errors.append(f"{label}: must be an object")
            continue

        intent = raw_step.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            errors.append(f"{label}: 'intent' must be a non-empty string")

        action = raw_step.get("action")
        if not isinstance(action, dict):
            errors.append(f"{label}: 'action' must be an object")
            action = {}
        action_type = action.get("type")
        if action_type not in ALLOWED_ACTIONS:
            errors.append(
                f"{label}: action type {action_type!r} is not allowed "
                f"(allowed: press, wait, read_screen)"
            )
        elif action_type == "press":
            cid = action.get("control_id")
            if cid not in controls:
                errors.append(f"{label}: control_id {cid!r} does not exist on the device")
            count = action.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                errors.append(f"{label}: press 'count' must be an integer >= 1")
        elif action_type == "read_screen":
            cid = action.get("control_id")
            if cid not in controls:
                errors.append(f"{label}: control_id {cid!r} does not exist on the device")
        elif action_type == "wait":
            seconds = action.get("seconds")
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
                errors.append(f"{label}: wait 'seconds' must be a number > 0")

        expect = raw_step.get("expect")
        if not isinstance(expect, dict):
            errors.append(f"{label}: 'expect' must be an object with control_id and text_contains")
            expect = {}
        else:
            ecid = expect.get("control_id")
            if ecid not in controls:
                errors.append(f"{label}: expect control_id {ecid!r} does not exist on the device")
            tc = expect.get("text_contains")
            if not isinstance(tc, str) or not tc:
                errors.append(f"{label}: expect 'text_contains' must be a non-empty string")

        requires_approval = raw_step.get("requires_approval")
        if not isinstance(requires_approval, bool):
            errors.append(f"{label}: 'requires_approval' must be a boolean")
            requires_approval = False

        # Any step that presses a risky control must require approval.
        if action_type == "press":
            control = controls.get(action.get("control_id"))
            if control is not None and control.get("risky") and requires_approval is not True:
                errors.append(
                    f"{label}: presses risky control {action.get('control_id')!r} "
                    "but requires_approval is not true"
                )

        step = {
            "step_id": f"s{pos}",
            "index": pos,
            "intent": intent if isinstance(intent, str) else "",
            "action": action,
            "expect": expect,
            "requires_approval": requires_approval,
        }
        approval_reason = raw_step.get("approval_reason")
        if isinstance(approval_reason, str) and approval_reason.strip():
            step["approval_reason"] = approval_reason
        step.update(
            {
                "status": "pending",
                "attempts": 0,
                "started_at": None,
                "finished_at": None,
                "evidence": {
                    "before_frame_url": None,
                    "after_frame_url": None,
                    "screen_text": None,
                },
                "error": None,
            }
        )
        steps.append(step)

    if errors:
        return None, errors

    plan = {"planner": MODEL, "summary": summary, "steps": steps}
    return plan, []


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or not argv[0].strip():
        print('Usage: python backend/planner.py "<request>"', file=sys.stderr)
        return 2
    request = argv[0]

    api_key = os.environ.get("MODEL_API_KEY")
    if not api_key:
        print("Error: MODEL_API_KEY environment variable is not set.", file=sys.stderr)
        return 1

    try:
        device = load_device()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: could not load device from {FIXTURE_PATH}: {exc}", file=sys.stderr)
        return 1

    client = OpenAI(base_url=BASE_URL, api_key=api_key)

    try:
        content = call_model(client, build_messages(device, request))
    except Exception as exc:
        print(f"Error: model call failed: {exc}", file=sys.stderr)
        return 1

    try:
        raw = parse_planning_json(content)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(f"Raw model output:\n{content}", file=sys.stderr)
        return 1

    plan, errors = validate_and_build_plan(raw, device)
    if errors:
        print("Error: plan failed validation:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    output = json.dumps(plan, indent=2, ensure_ascii=False)
    print(output)

    try:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(output + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Error: could not write {OUT_PATH}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
