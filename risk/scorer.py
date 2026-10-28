"""Risk scorer for plan steps, using Muse Spark Open on Baseten.

score_step(job, step) -> {"risk_tier": "low"|"medium"|"high",
                          "risk_reason": "one short line",
                          "risk_scored_by": MODEL}

It judges each step in context (the request, the device, what's on screen, what's
already been done), not by button name. START at 37 °C to run an incubator is fine,
START at 90 °C is not.

Safety rules:
- if anything goes wrong (no key, timeout, API error, junk back) the step is "high".
  We never fall back to "low".
- the scorer only adds a tier and a reason. It never touches requires_approval, so a
  gated step stays gated whatever the model says.
"""

import json
import os
import re
from pathlib import Path

from openai import OpenAI, BadRequestError, UnprocessableEntityError

# ---- API details, from the Baseten docs ----

BASE_URL = "https://inference.baseten.co/v1"
MODEL = "meta-models/muse-spark-open-1026"  # organizer-provided, use exactly this
API_KEY_ENV = "BASETEN_API_KEY"
TIMEOUT_SECONDS = 30

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO_ROOT / "backend" / "devices"

FALLBACK_REASON = "Couldn't score this step, treating it as high risk."
TIERS = ["low", "medium", "high"]
MAX_REASON_CHARS = 120

RISK_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_tier": {"type": "string", "enum": TIERS},
        "risk_reason": {"type": "string"},
    },
    "required": ["risk_tier", "risk_reason"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You score the risk of ONE step before a robot finger presses a button on a physical lab device.

Judge the step in context, never by the button's name alone:
- Look at what the person asked for, what is on the screen right now, and what has already been done.
- Work out what pressing this button will actually do to the machine with those settings.
- A step that starts or runs the machine is fine if the settings are sane for what the person asked
  (e.g. an incubator at 37 °C for cell culture). It is high risk if the settings are unsafe or
  outside the device's normal range (e.g. an incubator at 90 °C can cook samples and damage the unit).
- Changing a value (UP/DOWN) is low risk unless it pushes the value past a sane limit for this device.
- If the settings on screen don't match what the person asked for (e.g. they asked for 90 °C and the
  screen says 37.0), a step that starts or commits those settings is at least medium, even if the
  settings themselves are safe. The person needs to notice the mismatch. Say so in the reason.
- If you can't tell what the step will do, rate it higher.

Tiers:
- low: nothing happens to the physical world, or the change is small, expected and easy to undo.
- medium: changes the machine's state in a sane way, or something is slightly off and worth a look.
- high: could damage samples, the device or people, or clearly doesn't match the request.

risk_reason: ONE short line (under 15 words) a person can read in 2 seconds. Mention the actual value
when it matters, e.g. "Starts heating to 90 °C, far above a normal incubator range."
"""

JSON_ONLY_INSTRUCTIONS = (
    "\n\nReply with JSON only, no other text, exactly this shape:\n"
    '{"risk_tier": "low" | "medium" | "high", "risk_reason": "one short line"}'
)

# which way of asking for JSON worked: "json_schema" (structured outputs) or "prompt_json".
# None until the first successful call. Once structured outputs get rejected we stop trying them.
working_path = None
structured_outputs_rejected = False


def load_dotenv():
    """Read KEY=value lines from the repo-root .env. Real env vars win."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()

client = None


def get_client():
    global client
    if client is None:
        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            raise RuntimeError(f"{API_KEY_ENV} is not set")
        # no retries: worst case is one TIMEOUT_SECONDS, callers (the mock server) wait a bit longer than that
        client = OpenAI(base_url=BASE_URL, api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0)
    return client


def fallback_score():
    return {"risk_tier": "high", "risk_reason": FALLBACK_REASON, "risk_scored_by": MODEL}


# ---- building the context ----


def load_device_notes(device_id):
    """Notes and the idle screen from backend/devices/<id>.json, if there is one."""
    profile_path = PROFILES_DIR / f"{device_id}.json"
    if not profile_path.exists():
        return [], None
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], None
    return profile.get("notes", []), profile.get("current_screen")


def describe_action(action, controls_by_id):
    kind = action.get("type")
    if kind == "press":
        control_id = action.get("control_id")
        control = controls_by_id.get(control_id, {})
        label = control.get("label", "?")
        text = f'press {control_id} ("{label}") x{action.get("count", 1)}'
        if action.get("hold_ms"):
            text += f", hold {action['hold_ms']} ms"
        return text
    if kind == "wait":
        return f"wait {action.get('seconds')} s"
    if kind == "read_screen":
        return f"read screen {action.get('control_id')}"
    return json.dumps(action)


def build_context(job, step):
    """Everything the model needs to judge this one step, as plain text."""
    device = job.get("device") or {}
    controls = device.get("controls") or []
    controls_by_id = {}
    for control in controls:
        controls_by_id[control.get("control_id")] = control

    device_id = device.get("device_id", "unknown")
    notes, idle_screen = load_device_notes(device_id)

    all_steps = (job.get("plan") or {}).get("steps") or []
    step_index = step.get("index", 0)

    # steps that already ran, in order
    done_steps = []
    for other in all_steps:
        if other.get("index", 0) < step_index and other.get("status") == "succeeded":
            done_steps.append(other)
    done_steps.sort(key=lambda s: s.get("index", 0))

    # what's on screen right before this press: what the last finished step left there,
    # else the device's idle screen
    current_screen = None
    if done_steps:
        current_screen = (done_steps[-1].get("evidence") or {}).get("screen_text")
    if not current_screen:
        current_screen = idle_screen or "unknown"

    lines = []
    lines.append(f"Job request: {job.get('request', '')}")
    lines.append("")
    lines.append(f"Device: {device.get('name', '')} ({device_id})")
    if notes:
        lines.append("How the device works:")
        for note in notes:
            lines.append(f"- {note}")
    lines.append("Controls:")
    for control in controls:
        flags = " [changes machine state]" if control.get("risky") else ""
        lines.append(f'- {control.get("control_id")}: "{control.get("label")}" ({control.get("kind")}){flags}')
    lines.append("")
    lines.append(f"Screen right now: {current_screen}")
    lines.append("")
    if done_steps:
        lines.append("Steps already done:")
        for done in done_steps:
            screen_after = (done.get("evidence") or {}).get("screen_text") or "?"
            action_text = describe_action(done.get("action", {}), controls_by_id)
            lines.append(f"{done.get('index')}. {done.get('intent')}: {action_text} -> screen: {screen_after}")
    else:
        lines.append("Steps already done: none")
    lines.append("")
    lines.append("Step to score:")
    lines.append(f"{step_index}. {step.get('intent')}: {describe_action(step.get('action', {}), controls_by_id)}")
    expect = step.get("expect") or {}
    if expect.get("text_contains"):
        lines.append(f"Expected screen afterwards contains: {expect['text_contains']}")
    return "\n".join(lines)


# ---- talking to the model ----


def ask_structured(context):
    response = get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "risk_score", "strict": True, "schema": RISK_SCHEMA},
        },
        temperature=0,
    )
    return response.choices[0].message.content


def ask_prompt_json(context):
    response = get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + JSON_ONLY_INSTRUCTIONS},
            {"role": "user", "content": context},
        ],
        temperature=0,
    )
    return response.choices[0].message.content


def parse_score(text):
    """Pull {risk_tier, risk_reason} out of the model's reply. None if it's junk."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # maybe it wrapped the JSON in prose or a code fence, grab the first {...}
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None

    tier = str(data.get("risk_tier", "")).strip().lower()
    if tier not in TIERS:
        return None

    reason = data.get("risk_reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    reason = " ".join(reason.split())  # one line
    if len(reason) > MAX_REASON_CHARS:
        reason = reason[: MAX_REASON_CHARS - 1].rstrip() + "…"

    return {"risk_tier": tier, "risk_reason": reason, "risk_scored_by": MODEL}


def score_step_with_raw(job, step):
    """Same as score_step, but also returns a debug record with the raw model reply."""
    global working_path, structured_outputs_rejected

    raw = {"step_id": step.get("step_id"), "path": None, "raw_reply": None, "error": None}

    try:
        context = build_context(job, step)
    except Exception as err:  # malformed job, don't guess
        raw["error"] = f"couldn't build context: {err}"
        return fallback_score(), raw
    raw["context"] = context

    try:
        if not structured_outputs_rejected:
            try:
                raw["path"] = "json_schema"
                raw["raw_reply"] = ask_structured(context)
            except (BadRequestError, UnprocessableEntityError) as err:
                # the model/endpoint doesn't take a JSON schema, ask in the prompt from now on
                structured_outputs_rejected = True
                raw["structured_outputs_error"] = str(err)
        if structured_outputs_rejected:
            raw["path"] = "prompt_json"
            raw["raw_reply"] = ask_prompt_json(context)
    except Exception as err:  # timeout, auth, network, 5xx, missing key...
        raw["error"] = f"{type(err).__name__}: {err}"
        return fallback_score(), raw

    score = parse_score(raw["raw_reply"])
    if score is None:
        raw["error"] = "junk reply, couldn't parse a tier and reason"
        return fallback_score(), raw

    working_path = raw["path"]
    return score, raw


def score_step(job, step):
    score, _raw = score_step_with_raw(job, step)
    return score


def find_step(job, step_id):
    steps = (job.get("plan") or {}).get("steps") or []
    for step in steps:
        if step.get("step_id") == step_id:
            return step
    return None
