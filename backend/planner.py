#!/usr/bin/env python3
"""Physical MCP planner.

Usage:
    python backend/planner.py [--device DEVICE_ID] "<request>"
    python backend/planner.py --device incubator-01 "Set the incubator to 37 °C and run it for 2 hours"
    python backend/planner.py --device inkbird-01 "Set the heater to turn on at 80 °F"
    python backend/planner.py --device timer-01 "Set T1 to 2 hours and start it"

Reads the device (with its controls) from the matching fixture in
shared/fixtures/ and the device profile from backend/devices/<device_id>.json,
(the CLI has no camera, so the current screen is the profile's guess; api.py reads the
real screens through the MCP read_screen tool first and passes them in),
asks Muse Spark (via Meta's Model API) for a minimal plan, fills in the
runtime fields so the result matches the Plan type in shared/types.ts,
validates it, prints it as pretty JSON, and saves it to
backend/out/plan-<device_id>.json.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from openai import OpenAI

BASE_URL = "https://api.meta.ai/v1"
MODEL = "muse-spark-1.3"

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = Path(__file__).resolve().parent
PROFILES_DIR = BACKEND_DIR / "devices"
OUT_DIR = BACKEND_DIR / "out"

FIXTURES = {
    "incubator-01": REPO_ROOT / "shared" / "fixtures" / "jobSample.json",
    "inkbird-01": REPO_ROOT / "shared" / "fixtures" / "inkbirdJobSample.json",
    "timer-01": REPO_ROOT / "shared" / "fixtures" / "timerJobSample.json",
}

# step marks that always mean a press-and-hold approval, whatever the risk tier:
# turns_on_output (switches a heater, motor... on) and destructive (wipes something someone
# set or is counting, like RESET on a running timer). Same rules for both
HOLD_MARKS = ("turns_on_output", "destructive")

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


def fixture_path_for(device_id):
    try:
        return FIXTURES[device_id]
    except KeyError:
        known = ", ".join(sorted(FIXTURES))
        raise ValueError(f"unknown device {device_id!r} (known devices: {known})") from None


def load_device(device_id):
    path = fixture_path_for(device_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    device = data.get("device")
    if not isinstance(device, dict) or not isinstance(device.get("controls"), list):
        raise ValueError(f"{path} does not contain a device with controls")
    if device.get("device_id") != device_id:
        raise ValueError(
            f"{path} contains device {device.get('device_id')!r}, not {device_id!r}"
        )
    return device


def load_profile(device_id):
    path = PROFILES_DIR / f"{device_id}.json"
    if not path.is_file():
        raise ValueError(f"device profile not found: {path}")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(profile, dict):
        raise ValueError(f"{path} does not contain a device profile object")
    if profile.get("device_id") != device_id:
        raise ValueError(
            f"{path} is for device {profile.get('device_id')!r}, not {device_id!r}"
        )
    if not isinstance(profile.get("controls"), dict):
        raise ValueError(f"{path} does not contain a 'controls' object")
    return profile


def profile_controls(profile):
    if not profile or not isinstance(profile.get("controls"), dict):
        return {}
    return profile["controls"]


def is_reachable(control, profile):
    """Effective reachability: profile wins, then the control, else True."""
    entry = profile_controls(profile).get(control.get("control_id"))
    if isinstance(entry, dict) and "reachable" in entry:
        return bool(entry["reachable"])
    if "reachable" in control:
        return bool(control["reachable"])
    return True


def is_risky(control, profile):
    if control.get("risky"):
        return True
    entry = profile_controls(profile).get(control.get("control_id"))
    return bool(isinstance(entry, dict) and entry.get("risky"))


def control_has_mark(control_id, profile, mark):
    """The profile marks this control turns_on_output (an incubator's START) or destructive
    (the timer's RESET)."""
    entry = profile_controls(profile).get(control_id)
    return bool(isinstance(entry, dict) and entry.get(mark))


def needs_hold(step):
    """True if the step's approval has to be a press-and-hold, whatever the tier."""
    for mark in HOLD_MARKS:
        if step.get(mark):
            return True
    return False


def _positive_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def device_hold_ms(profile):
    """The profile's device-level hold_ms: every press on this device holds that long (the
    timer needs 1 s, a tap doesn't click). None when the device has no such setting."""
    hold = (profile or {}).get("hold_ms")
    return hold if _positive_number(hold) else None


def required_hold_ms(control_id, profile):
    """The hold a press on control_id needs: the control's own hold_ms wins, then the
    device-level one, else None (a plain tap is fine)."""
    entry = profile_controls(profile).get(control_id)
    if isinstance(entry, dict) and _positive_number(entry.get("hold_ms")):
        return entry["hold_ms"]
    return device_hold_ms(profile)


def max_hold_ms(profile):
    """Longest hold this device may get (the timer auto-repeats at ~2 s), or None for no limit."""
    most = (profile or {}).get("max_hold_ms")
    return most if _positive_number(most) else None


def planning_screens(device, profile):
    """The screens to read before planning: the profile's plan_reads (the timer reads only
    screen_t1, the rig can't use the other channels), else every reachable screen."""
    reachable = []
    for control in reachable_device(device, profile)["controls"]:
        if control.get("kind") == "screen":
            reachable.append(control)
    wanted = (profile or {}).get("plan_reads")
    if not isinstance(wanted, list):
        return reachable
    picked = []
    for control in reachable:
        if control["control_id"] in wanted:
            picked.append(control)
    return picked


def retries_if_unchanged(control_id, profile):
    """The profile says a press on this control can silently not register (the timer's H), so
    an unchanged screen afterwards means press it again. See api.plan_repeat."""
    entry = profile_controls(profile).get(control_id)
    return bool(isinstance(entry, dict) and entry.get("retry_if_unchanged"))


def refusal_reason(raw):
    """The model's one-line reason when it says the request can't be done on this rig
    ({"refusal": "..."} instead of a plan), else None."""
    if isinstance(raw, dict) and isinstance(raw.get("refusal"), str) and raw["refusal"].strip():
        return raw["refusal"].strip()
    return None


def describe_current_screen(profile, readings=None):
    """The "Current screen" part of the prompt.

    readings: what the camera read just before planning, one dict per screen:
    {"control_id", "label", "text", "confidence", "error"}. A read that failed (error set,
    or no text) or came back low confidence doesn't count, and the profile's current_screen
    goes in instead, marked as a guess. No readings at all (the CLI, no camera) = the guess."""
    guess = (profile or {}).get("current_screen", "")
    if not readings:
        return f"Current screen (a guess from the device profile, NOT read from the camera, may be stale): {guess}"

    lines = ["Current screen (read from the camera just now):"]
    any_bad = False
    for reading in readings:
        name = reading["control_id"]
        if reading.get("label"):
            name += f" ({reading['label']})"
        if reading.get("error"):
            lines.append(f"- {name}: couldn't read it ({reading['error']})")
            any_bad = True
        elif not reading.get("text"):
            lines.append(f"- {name}: couldn't read it (empty read)")
            any_bad = True
        elif reading.get("confidence") == "low":
            lines.append(f"- {name}: unsure, low-confidence read {reading['text']!r}, don't trust it")
            any_bad = True
        else:
            lines.append(f"- {name}: {reading['text']}")
    if any_bad:
        lines.append(
            "For the screens that couldn't be read, use this guess from the device profile "
            f"(NOT read from the camera, may be stale): {guess}"
        )
    return "\n".join(lines)


def reachable_device(device, profile):
    """Copy of the device with only reachable controls (missing = reachable)."""
    filtered = dict(device)
    filtered["controls"] = [
        c for c in device.get("controls", []) if isinstance(c, dict) and is_reachable(c, profile)
    ]
    # Annotate with profile facts the camera can't see (hold, risky) so the
    # model can plan hold_ms and approvals correctly.
    annotated = []
    for control in filtered["controls"]:
        entry = profile_controls(profile).get(control.get("control_id"))
        merged = dict(control)
        # the hold this control needs: its own, or the device-level one (every timer press)
        hold = required_hold_ms(control.get("control_id"), profile)
        if hold is not None and control.get("kind") not in ("screen", "indicator"):
            merged["hold_ms"] = hold
        if isinstance(entry, dict):
            if entry.get("risky"):
                merged["risky"] = True
            for mark in HOLD_MARKS:
                if entry.get(mark):
                    merged[mark] = True
        annotated.append(merged)
    filtered["controls"] = annotated
    return filtered


def build_messages(device, profile, request, screen_readings=None):
    """screen_readings: what the camera read before planning (see describe_current_screen).
    None = no camera, the profile's current_screen goes in marked as a guess."""
    profile = profile or {}
    visible_device = reachable_device(device, profile)
    current_screen = describe_current_screen(profile, screen_readings)
    notes = profile.get("notes", [])
    if isinstance(notes, list):
        notes_text = "\n".join(f"- {n}" for n in notes)
    else:
        notes_text = str(notes)

    timed_menu = profile.get("timed_menu") if isinstance(profile.get("timed_menu"), dict) else None
    menu_text = ""
    if timed_menu:
        menu_text = (
            "\nThis device has a timed menu: "
            f"press {timed_menu.get('enter_control_id')} with hold_ms "
            f"{timed_menu.get('hold_ms')} to enter, and "
            f"{timed_menu.get('exit_control_id')} with hold_ms "
            f"{timed_menu.get('hold_ms')} again to save and exit. The menu exits "
            f"without saving after {timed_menu.get('timeout_seconds')} s with no press, "
            "so no wait step and no requires_approval step may sit inside the menu "
            "(between entering and leaving it). If approval is needed for the plan, "
            "put requires_approval on the first step (entering the menu), never on "
            "a step inside the menu.\n"
        )

    user_prompt = (
        "Plan the following request for the supplied device.\n\n"
        f"Request: {request}\n\n"
        f"{current_screen}\n\n"
        f"Device notes (how the buttons behave):\n{notes_text}\n"
        f"{menu_text}\n"
        f"Device (with controls, only reachable controls are listed):\n"
        f"{json.dumps(visible_device, ensure_ascii=False, indent=2)}\n\n"
        "Compute press counts from the current value on the current screen and "
        "the step size in the notes (e.g. from 25.0 to 37.0 at 1.0 per press is "
        "12 presses). Use the display formats from the notes (e.g. 37.0, 02:00) "
        "in expect.text_contains. If a control needs a hold (see hold_ms in the "
        "controls or the notes), include hold_ms on its press action. For an "
        "indicator control, an expect may use indicator_on (true/false) instead "
        "of screen text; in that case text_contains may be an empty string.\n\n"
        "Return JSON only, with exactly this shape and no other fields:\n"
        "{\n"
        '  "summary": "<one-line summary of the plan>",\n'
        '  "steps": [\n'
        "    {\n"
        '      "intent": "<human-readable intent>",\n'
        '      "action": {"type": "press", "control_id": "<control_id>", "count": 1, "hold_ms": 3000}\n'
        '                | {"type": "wait", "seconds": <number>}\n'
        '                | {"type": "read_screen", "control_id": "<control_id>"},\n'
        '      "expect": {"control_id": "<screen control_id>", "text_contains": "<text the screen must contain afterwards>"}\n'
        '                | {"control_id": "<indicator control_id>", "text_contains": "", "indicator_on": true},\n'
        '      "requires_approval": true | false,\n'
        '      "approval_reason": "<reason; include only when requires_approval is true>",\n'
        '      "turns_on_output": true | false,\n'
        '      "destructive": true | false\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "If the request can't be done with only the controls listed above (it needs a "
        "button that isn't listed, or the device notes say it can't be done), don't make a "
        "plan: return exactly {\"refusal\": \"<one line: what can't be done and why>\"} instead. "
        "hold_ms on a press: include it whenever the control lists a hold_ms, with that "
        "value, otherwise leave it out. "
        "Set turns_on_output to true on the step that makes the machine switch an "
        "output on (a heater, a motor, a pump), as the device notes describe, or "
        "that presses a control marked turns_on_output; that step also needs "
        "requires_approval true. Every other step has turns_on_output false. "
        "Set destructive to true on a step that wipes something someone set or is "
        "counting (like resetting a running timer), as the device notes describe, or "
        "that presses a control marked destructive; that step also needs "
        "requires_approval true. Every other step has destructive false. "
        "Use only the planning fields above (intent, action, expect, "
        "requires_approval, approval_reason, turns_on_output, destructive). Do not include runtime fields such "
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


def validate_and_build_plan(raw, device, profile=None):
    """Return (plan, errors). plan is None when errors is non-empty."""
    profile = profile or {}
    errors = []
    controls = {c.get("control_id"): c for c in device.get("controls", []) if isinstance(c, dict)}

    def check_control_ref(label, cid, role):
        if cid not in controls:
            errors.append(f"{label}: {role} control_id {cid!r} does not exist on the device")
            return False
        if not is_reachable(controls[cid], profile):
            errors.append(f"{label}: {role} control_id {cid!r} is unreachable on this rig")
            return False
        return True

    if not isinstance(raw, dict):
        return None, ["model output must be a JSON object with 'summary' and 'steps'"]
    reason = refusal_reason(raw)
    if reason:
        return None, [f"the planner refused: {reason}"]

    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("plan: 'summary' must be a non-empty string")

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        errors.append("plan: 'steps' must be a non-empty list")
        return None, errors

    # Timed-menu tracking (e.g. Inkbird): entering is a hold-press on the
    # enter control, leaving is the same hold-press again. Approvals are
    # decided before a step runs, so a step inside the menu (including the
    # exit step) must not require approval, and no wait may sit inside.
    timed_menu = profile.get("timed_menu") if isinstance(profile.get("timed_menu"), dict) else None
    in_menu = False

    def is_menu_toggle(action):
        if not timed_menu or not isinstance(action, dict):
            return False
        if action.get("type") != "press":
            return False
        cid = action.get("control_id")
        expected = timed_menu.get("enter_control_id") if not in_menu else timed_menu.get("exit_control_id")
        if cid != expected:
            return False
        hold = action.get("hold_ms")
        required = timed_menu.get("hold_ms") or 0
        return isinstance(hold, (int, float)) and not isinstance(hold, bool) and hold >= required

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
            check_control_ref(label, cid, "action")
            count = action.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                errors.append(f"{label}: press 'count' must be an integer >= 1")
            if "hold_ms" in action and action.get("hold_ms") is not None:
                hold = action.get("hold_ms")
                if isinstance(hold, bool) or not isinstance(hold, (int, float)) or hold <= 0:
                    errors.append(f"{label}: press 'hold_ms' must be a number > 0")
            needed = required_hold_ms(cid, profile)
            # a device where every press is a hold (the timer): the model doesn't have to
            # remember it, a press with no hold_ms gets the device's. a control that needs its
            # own special hold (the Inkbird's SET) still has to say so in the plan
            if needed is not None and device_hold_ms(profile) is not None and action.get("hold_ms") is None:
                action = dict(action)
                action["hold_ms"] = needed
            most = max_hold_ms(profile)
            hold = action.get("hold_ms")
            if most is not None and _positive_number(hold) and hold > most:
                errors.append(
                    f"{label}: hold_ms {hold} on {cid!r} is over this device's limit of "
                    f"{most} ms (a longer hold auto-repeats)"
                )
            if needed is not None:
                hold = action.get("hold_ms")
                if isinstance(hold, bool) or not isinstance(hold, (int, float)):
                    errors.append(
                        f"{label}: control {cid!r} needs a hold of {needed} ms "
                        "but the press has no hold_ms"
                    )
                elif hold < needed:
                    errors.append(
                        f"{label}: control {cid!r} needs a hold of {needed} ms "
                        f"but hold_ms is {hold}"
                    )
        elif action_type == "read_screen":
            check_control_ref(label, action.get("control_id"), "action")
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
            check_control_ref(label, ecid, "expect")
            has_indicator = "indicator_on" in expect
            if has_indicator and not isinstance(expect.get("indicator_on"), bool):
                errors.append(f"{label}: expect 'indicator_on' must be a boolean")
            if has_indicator and ecid in controls and controls[ecid].get("kind") != "indicator":
                errors.append(
                    f"{label}: expect 'indicator_on' is only valid on an indicator control, "
                    f"not {ecid!r} (kind {controls[ecid].get('kind')!r})"
                )
            tc = expect.get("text_contains")
            if has_indicator:
                if tc is not None and not isinstance(tc, str):
                    errors.append(f"{label}: expect 'text_contains' must be a string")
            elif not isinstance(tc, str) or not tc:
                errors.append(f"{label}: expect 'text_contains' must be a non-empty string")

        requires_approval = raw_step.get("requires_approval")
        if not isinstance(requires_approval, bool):
            errors.append(f"{label}: 'requires_approval' must be a boolean")
            requires_approval = False

        # A step that switches an output on (heater, motor...) or wipes something (RESET on a
        # running timer) always waits for a human, and that approval is a press-and-hold
        # whatever the risk score says. The plan can mark it (per the device notes) or the
        # profile can, per control. Either one only ever adds an approval, never takes one away.
        marks = {}
        for mark in HOLD_MARKS:
            value = raw_step.get(mark, False)
            if not isinstance(value, bool):
                errors.append(f"{label}: '{mark}' must be a boolean")
                value = False
            if action_type == "press" and control_has_mark(action.get("control_id"), profile, mark):
                value = True
            if value:
                requires_approval = True
            marks[mark] = value

        # Any step that presses a risky control must require approval.
        if action_type == "press":
            control = controls.get(action.get("control_id"))
            if control is not None and is_risky(control, profile) and requires_approval is not True:
                errors.append(
                    f"{label}: presses risky control {action.get('control_id')!r} "
                    "but requires_approval is not true"
                )

        # Timed menu: nothing that pauses may sit inside the menu.
        if timed_menu and in_menu:
            if requires_approval:
                errors.append(
                    f"{label}: requires_approval inside a timed menu "
                    f"(menu exits without saving after {timed_menu.get('timeout_seconds')} s)"
                )
            if action_type == "wait":
                errors.append(
                    f"{label}: wait step inside a timed menu "
                    f"(menu exits without saving after {timed_menu.get('timeout_seconds')} s)"
                )
        if timed_menu and is_menu_toggle(action):
            in_menu = not in_menu

        # Normalise expect so the result always matches the Expectation type.
        norm_expect = dict(expect) if isinstance(expect, dict) else {}
        if "indicator_on" in norm_expect and norm_expect.get("text_contains") is None:
            norm_expect["text_contains"] = ""

        step = {
            "step_id": f"s{pos}",
            "index": pos,
            "intent": intent if isinstance(intent, str) else "",
            "action": action,
            "expect": norm_expect,
            "requires_approval": requires_approval,
        }
        approval_reason = raw_step.get("approval_reason")
        if isinstance(approval_reason, str) and approval_reason.strip():
            step["approval_reason"] = approval_reason
        for mark in HOLD_MARKS:
            if marks[mark]:
                step[mark] = True
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

    if timed_menu and in_menu:
        errors.append("plan: ends inside a timed menu without saving and exiting")

    if errors:
        return None, errors

    plan = {"planner": MODEL, "summary": summary, "steps": steps}
    return plan, []


def out_path_for(device_id):
    return OUT_DIR / f"plan-{device_id}.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Plan a physical-device job with Muse Spark.")
    parser.add_argument("--device", default="incubator-01", help="device_id (default: incubator-01)")
    parser.add_argument("request", help="the job request, in quotes")
    args = parser.parse_args(argv)
    device_id = args.device
    request = args.request
    if not request or not request.strip():
        parser.error("request must not be empty")

    api_key = os.environ.get("MODEL_API_KEY")
    if not api_key:
        print("Error: MODEL_API_KEY environment variable is not set.", file=sys.stderr)
        return 1

    try:
        device = load_device(device_id)
        profile = load_profile(device_id)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error: could not load device/profile for {device_id!r}: {exc}", file=sys.stderr)
        return 1

    client = OpenAI(base_url=BASE_URL, api_key=api_key)

    try:
        content = call_model(client, build_messages(device, profile, request))
    except Exception as exc:
        print(f"Error: model call failed: {exc}", file=sys.stderr)
        return 1

    try:
        raw = parse_planning_json(content)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(f"Raw model output:\n{content}", file=sys.stderr)
        return 1

    reason = refusal_reason(raw)
    if reason:
        print(f"Refused, no plan: {reason}", file=sys.stderr)
        return 1

    plan, errors = validate_and_build_plan(raw, device, profile)
    if errors:
        print("Error: plan failed validation:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    output = json.dumps(plan, indent=2, ensure_ascii=False)
    print(output)

    out_path = out_path_for(device_id)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Error: could not write {out_path}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
