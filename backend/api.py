#!/usr/bin/env python3
"""HTTP + WebSocket backend for the web app, speaking the contract in shared/types.ts.

    python backend/api.py               # http://127.0.0.1:8000
    python backend/api.py --port 9000

    POST /jobs                                  -> Job (status "planning")
    GET  /jobs                                  -> [{job_id, created_at, status, request}]
    GET  /jobs/:job_id                          -> Job
    GET  /jobs/:job_id/history                  -> every JobEvent so far (backend/record_run.py)
    POST /jobs/:job_id/steps/:step_id/approval  -> Job
    POST /jobs/:job_id/resume                   -> Job (after a pause for a low-confidence read)
    POST /jobs/:job_id/stop                     -> Job
    GET  /devices/:device_id                    -> Device
    WS   /jobs/:job_id/events                   -> stream of JobEvent

Same routes and event order as web/mock-server/server.ts, but with a real planner, the real
risk scorer and a Hardware object (backend/hardware.py) doing the pressing:

1. POST /jobs makes the job and starts planning in the background: the profile's plan_reads
   (else every reachable screen) get read through the MCP read_screen tool first, then
   planner.py asks Muse Spark for a plan starting from those readings, then plan.ready goes out.
   A low-confidence read, here or anywhere later, pauses the job with a reason (pause_reason)
   and nothing gets pressed until POST .../resume, which reads the screen again first.
   Events carry an optional note (shared/types.ts EventNote) saying what they are for the log,
   with the read and its confidence on every one that involves a screen read.
2. Every requires_approval step goes to the risk scorer (risk/scorer.py) right away, all at
   once, in the background. Each score lands as a step.updated. Nothing waits on them.
3. Steps run one at a time. The runner here is the MCP client: every press and read goes
   through the tools in mcp_server.py (in memory, same process). press is the approval gate:
   a requires_approval step stops at awaiting_approval inside press until a decision comes in,
   and every press is charged to the press budget. If a step's score isn't back yet, the card
   shows up anyway with the tier pending, and the score fills in when it lands.
4. Stop calls hardware.stop() and ends the job as "stopped".

Jobs live in memory. One finger, so a new job stops the one before it, like the mock.
Every event is kept per job and replayed to the first socket that connects, so nothing sent
between POST /jobs and the socket opening gets lost. Later sockets (reconnects) get live
events only and catch up with GET /jobs/:id, which is what web/lib/backend.ts does.

Keys come from the repo-root .env: MODEL_API_KEY for the planner, BASETEN_API_KEY for the
scorer. Only approval_mode "risky_steps" is acted out for now, same as the mock.
"""

import argparse
import asyncio
import copy
import json
import os
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, WebSocketException, status
from fastapi.middleware.cors import CORSMiddleware
from mcp import Client
from openai import OpenAI

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(REPO_ROOT / "risk"))

import scorer  # noqa: E402  importing it also loads the repo-root .env into os.environ
import planner  # noqa: E402
import mcp_server  # noqa: E402
from hardware import FakeHardware, HardwareStopped  # noqa: E402
from screen_numbers import find_number  # noqa: E402

PLANNER_TIMEOUT_SECONDS = 90
DEFAULT_PRESS_BUDGET = 30
DEFAULT_MAX_ATTEMPTS = 3
# a retry never presses more than this in one go. further off than that and something's
# wrong (misread, wrong menu), so the step fails instead of guessing
MAX_CORRECTION_PRESSES = 5
SECONDS_BETWEEN_READS = 0.5
FINISHED = ("succeeded", "failed", "stopped")
# placeholder stills the web app serves itself, e.g. web/public/mock/inkbird-01.svg -> /mock/inkbird-01.svg
MOCK_FRAMES_DIR = REPO_ROOT / "web" / "public" / "mock"
# control boxes for this camera (backend/calibrate.py, vision/read_screen.py --calibrate)
CALIBRATION_DIR = REPO_ROOT / "vision" / "calibration"

# how often the real rig's webcam frame gets refreshed for the web app's camera view.
# only the real rig has a webcam; on FakeHardware the camera view stays the mock still
FRAME_SECONDS = 0.5
LIVE_FRAMES = os.environ.get("HARDWARE") == "serial"

# HARDWARE=serial (env or repo-root .env) drives the real ESP32 finger + webcam, see
# backend/serial_hardware.py. Anything else keeps the fake, so the demo runs with no rig.
if os.environ.get("HARDWARE") == "serial":
    from serial_hardware import SerialHardware  # noqa: E402

    hardware = SerialHardware.from_env()
else:
    hardware = FakeHardware()
# one finger, so one hardware call at a time
hardware_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hardware")
# scores take 9 to 14 s each, so run them side by side
scoring_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="scoring")
# the live camera frames get their own thread: the webcam and the ESP32 are separate USB
# devices, so frames keep coming during a press. SerialHardware's camera lock keeps this
# thread and read_screen from grabbing the webcam at the same time
camera_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera")

# the MCP tools reach the runs, the hardware and the approval flow through this module
# (sys.modules, so it's this copy even when api.py runs as a script)
mcp_server.use_backend(sys.modules[__name__])

async def keep_frames_fresh():
    """Real rig only: save a webcam frame about every FRAME_SECONDS so the web app's camera view
    is live (it reloads web/public/frames/<device>-latest.jpg). Runs on its own camera thread,
    not the hardware worker, so it keeps going during a press. It never touches the serial port,
    and the camera lock inside SerialHardware keeps it from grabbing while read_screen does."""
    loop = asyncio.get_running_loop()
    last_problem = None
    while True:
        started = loop.time()
        try:
            await loop.run_in_executor(camera_pool, hardware.save_frame)
            if last_problem is not None:
                log("camera frames are coming in again")
                last_problem = None
        except Exception as err:
            # a missing camera would say this twice a second, so only log when it changes
            problem = f"{type(err).__name__}: {err}"
            if problem != last_problem:
                log(f"couldn't save a camera frame, will keep trying: {problem}")
                last_problem = problem
        await asyncio.sleep(max(0.0, FRAME_SECONDS - (loop.time() - started)))


@asynccontextmanager
async def lifespan(app):
    frames_task = None
    if LIVE_FRAMES:
        frames_task = asyncio.create_task(keep_frames_fresh())
        log(f"live camera: saving a frame every {FRAME_SECONDS} s")
    yield
    if frames_task:
        frames_task.cancel()


app = FastAPI(title="Physical MCP backend", lifespan=lifespan)
# the web app runs on :3000, let the browser call us
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class JobRun:
    """A job plus everything the server needs to drive it."""

    def __init__(self, job):
        self.job = job
        self.events = []  # everything sent so far, replayed to the first watcher
        self.watchers = []  # one asyncio.Queue per open socket
        self.had_watcher = False
        self.task = None  # the run itself
        self.scoring_tasks = []
        self.decision = None  # future that POST .../approval resolves
        self.waiting_step_id = None
        self.approved_step_ids = set()  # so a retry press doesn't ask again
        self.active_step = None
        self.presses_used = 0
        self.profile = {}  # backend/devices/<device_id>.json, loaded when the run starts
        self.resume = None  # future that POST .../resume resolves, while paused on an unclear read
        self.last_reads = {}  # control_id -> the newest ScreenRead of it


runs = {}  # job_id -> JobRun
current_run = None


def log(message):
    print(f"[api] {message}", flush=True)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def is_finished(run):
    return run.job["status"] in FINISHED


# ---- events ----


def emit(run, event_type, note=None, **fields):
    """note: what this event means for the log (EventNote in shared/types.ts), so a retry, a
    skip, a rejection, a refusal... each look different. Left off when there's nothing to say."""
    event = {"type": event_type, "job_id": run.job["job_id"], "ts": now()}
    event.update(fields)
    if note is not None:
        event["note"] = note
    run.job["updated_at"] = event["ts"]
    run.events.append(event)
    for queue in run.watchers:
        queue.put_nowait(event)


def make_note(kind, message, read=None):
    note = {"kind": kind, "message": message}
    if read is not None:
        note["read"] = dict(read)
    return note


def emit_status(run, job_status, reason=None, note=None):
    """reason: only for "paused", why. It's kept on the job too (pause_reason) for reconnects."""
    run.job["status"] = job_status
    run.job["pause_reason"] = reason if job_status == "paused" else None
    if reason is not None:
        emit(run, "job.status", note=note, status=job_status, reason=reason)
    else:
        emit(run, "job.status", note=note, status=job_status)


def emit_step(run, step, note=None):
    emit(run, "step.updated", note=note, step=copy.deepcopy(step))


def finish(run, outcome, message, final_screen_text, note=None):
    if is_finished(run):
        return
    result = {"outcome": outcome, "message": message, "final_screen_text": final_screen_text}
    run.job["status"] = outcome
    run.job["result"] = result
    run.job["pause_reason"] = None
    emit(run, "job.finished", note=note, result=result)
    log(f"job {run.job['job_id']} {outcome}: {message}")


# ---- planning and scoring (both block, so they run on threads) ----


class PlanRefused(Exception):
    """The planner said the request can't be done on this rig. Nothing gets pressed.
    unreadable: it's refused because the screen couldn't be read before planning (a camera
    problem), not because the request is impossible, so the message says that instead."""

    def __init__(self, reason, unreadable=False):
        super().__init__(reason)
        self.unreadable = unreadable


def make_plan(device_id, request, screen_readings=None):
    """screen_readings: what the camera read just before planning (read_screens_for_planning)."""
    api_key = os.environ.get("MODEL_API_KEY")
    if not api_key:
        raise RuntimeError("MODEL_API_KEY is not set (put it in the repo-root .env)")
    device = planner.load_device(device_id)
    profile = planner.load_profile(device_id)
    client = OpenAI(base_url=planner.BASE_URL, api_key=api_key, timeout=PLANNER_TIMEOUT_SECONDS)
    messages = planner.build_messages(device, profile, request, screen_readings)
    content = planner.call_model(client, messages)
    raw = planner.parse_planning_json(content)
    reason = planner.refusal_reason(raw)
    if reason:
        raise PlanRefused(reason)
    plan, errors = planner.validate_and_build_plan(raw, device, profile)
    if errors:
        raise ValueError("plan failed validation: " + "; ".join(errors))
    # the model's arithmetic gets checked in code: simulate the plan from the real read before
    # planning (timer-01: H = +1 hour), refuse it if an expect or the final time doesn't add up
    problem = planner.check_plan(plan, screen_readings, request, profile)
    if problem:
        raise PlanRefused(problem, unreadable=planner.plan_check_start(screen_readings, profile) is None)
    return plan


async def score_in_background(run, step):
    loop = asyncio.get_running_loop()
    # the scorer gets its own copies, the run keeps changing the real ones
    job_copy = copy.deepcopy(run.job)
    step_copy = copy.deepcopy(step)
    log(f"scoring {step['step_id']}")
    try:
        score, raw = await loop.run_in_executor(scoring_pool, scorer.score_step_with_raw, job_copy, step_copy)
    except Exception as err:
        score = scorer.fallback_score()
        raw = {"error": f"{type(err).__name__}: {err}"}
    if raw.get("error"):
        # still high, but say it's a fallback so nobody reads it as a real score
        log(f"scoring {step['step_id']} failed, sent high: {raw['error']}")
        score = dict(score)
        score["risk_scored_by"] = "fallback (scorer error)"
    else:
        log(f"{step['step_id']} risk: {score['risk_tier']} \"{score['risk_reason']}\"")

    # stopped or replaced while we were waiting, nobody's looking anymore
    if is_finished(run):
        return
    # only adds tier + reason, requires_approval stays whatever the plan said
    step["risk_tier"] = score["risk_tier"]
    step["risk_reason"] = score["risk_reason"]
    step["risk_scored_by"] = score["risk_scored_by"]
    emit_step(run, step)


# ---- the run ----


async def on_hardware(method, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(hardware_pool, method, *args)


def screen_matches(expect, text):
    text = text or ""
    if expect.get("indicator_on") is not None:
        light_on = text.strip().upper() == "ON"
        return light_on == expect["indicator_on"]
    return (expect.get("text_contains") or "") in text


def describe_expect(expect):
    if expect.get("indicator_on") is not None:
        return "ON" if expect["indicator_on"] else "OFF"
    return repr(expect.get("text_contains") or "")


def up_down_controls(device):
    """(up_id, down_id) for the device's ▲/▼ buttons, either can be None."""
    up_id = None
    down_id = None
    for control in device.get("controls", []):
        if control.get("label") == "▲" or control.get("control_id") == "btn_up":
            up_id = control["control_id"]
        if control.get("label") == "▼" or control.get("control_id") == "btn_down":
            down_id = control["control_id"]
    return up_id, down_id


def plan_correction(run, step, screen_text):
    """What to press to fix an up/down step that landed on the wrong number.

    Returns (correction, problem, error):
    - correction: a press Action, or None when there's nothing safe to press (not an
      up/down step, a number didn't parse, no step_size). Then we only re-read.
    - problem: one plain line, "Screen says 79.0, wanted 80.0".
    - error: set when it's off by more than one retry may fix. The step should fail.
    """
    action = step["action"]
    up_id, down_id = up_down_controls(run.job["device"])
    if action["type"] != "press" or action["control_id"] not in (up_id, down_id):
        return None, None, None

    on_screen = find_number(screen_text)
    wanted = find_number(step["expect"].get("text_contains"))
    step_size = run.profile.get("step_size")
    if on_screen is None or wanted is None:
        return None, None, None
    if isinstance(step_size, bool) or not isinstance(step_size, (int, float)) or step_size <= 0:
        return None, None, None

    difference = wanted[0] - on_screen[0]
    presses = round(abs(difference) / step_size)
    if presses == 0:
        return None, None, None  # off by less than one press, pressing won't help

    problem = f"Screen says {on_screen[1]}, wanted {wanted[1]}"
    if presses > MAX_CORRECTION_PRESSES:
        error = (
            f"{problem}: that's {presses} presses off, more than the {MAX_CORRECTION_PRESSES} "
            "a retry may correct. Nothing pressed."
        )
        return None, problem, error

    control_id = up_id if difference > 0 else down_id
    if control_id is None:
        return None, None, None
    return {"type": "press", "control_id": control_id, "count": presses}, problem, None


def same_text(a, b):
    return " ".join((a or "").split()) == " ".join((b or "").split())


def plan_repeat(run, step, read, before_read, plain_reads):
    """The timer's missed press: H didn't register, so the screen still shows exactly what it
    showed before the press. Only for a control the profile marks retry_if_unchanged, only
    after two reads in a row agree nothing changed (a slow screen gets a second look first),
    and never on a step that needs approval (pressing ST/SP twice would stop what it started).

    Returns (correction, problem), or (None, None) when it isn't safe to press again. A screen
    that changed, just not to what we wanted, is never "fixed" this way: no guessing."""
    action = step["action"]
    if action["type"] != "press" or not planner.retries_if_unchanged(action["control_id"], run.profile):
        return None, None
    if step.get("requires_approval"):
        return None, None
    # the before read has to be a real read of the same screen, not the profile's guess
    if before_read is None or before_read["control_id"] != read["control_id"]:
        return None, None
    if plain_reads < 2 or not same_text(read["text"], before_read["text"]):
        return None, None
    problem = (f"{read['control_id']} still reads {read['text']!r}, same as before the press, "
               "so the press didn't register")
    return {"type": "press", "control_id": action["control_id"], "count": action["count"]}, problem


def describe_read(read):
    return f"{read['control_id']} reads {read['text']!r} ({read['confidence'] or 'no vision vote'})"


async def read_once(run, client, control_id):
    """One read through the MCP read_screen tool, as a ScreenRead {control_id, text, confidence}.
    The newest read of every screen is kept in run.last_reads."""
    result = await mcp_server.call(client, "read_screen", {"control_id": control_id})
    read = {"control_id": control_id, "text": result.get("text") or "", "confidence": result.get("confidence")}
    run.last_reads[control_id] = read
    return read


async def wait_out_unclear_read(run, client, read, step=None):
    """A low-confidence read is never acted on. The job pauses with the reason, nothing gets
    pressed, and it waits for POST /jobs/:id/resume (Stop ends it). Resuming reads the screen
    again first, and if that one's low too it pauses again. Returns the first read that isn't low."""
    status_after = run.job["status"]  # "planning" before planning, "running" in a step
    while read["confidence"] == "low":
        where = f"step {step['index']}" if step is not None else "before planning"
        reason = (f"Couldn't read {read['control_id']} clearly ({where}): low confidence, read "
                  f"{read['text']!r}. Nothing was pressed. Check the camera and the screen, then "
                  "resume to read it again.")
        if step is not None:
            step["evidence"]["screen_text"] = read["text"]
            step["evidence"]["screen_confidence"] = read["confidence"]
            emit_step(run, step)
        log(reason)
        emit_status(run, "paused", reason=reason, note=make_note("read_unclear", reason, read))

        run.resume = asyncio.get_running_loop().create_future()
        try:
            body = await run.resume
        finally:
            run.resume = None
        who = body.get("decided_by") or "you"
        emit_status(run, status_after, note=make_note("resumed", f"Resumed by {who}, reading {read['control_id']} again"))
        read = await read_once(run, client, read["control_id"])
        log(f"after resuming, {describe_read(read)}")
    return read


async def read_checked(run, client, control_id, step=None):
    """read_once, but a low-confidence read pauses the job until a clear read comes back."""
    read = await read_once(run, client, control_id)
    return await wait_out_unclear_read(run, client, read, step)


async def read_screens_for_planning(run, client, device, profile):
    """Read the screens the planner starts from (the profile's plan_reads, the timer's is just
    screen_t1, else every reachable screen) through the MCP read_screen tool, so it plans from
    what the device really shows, not the profile's fixed current_screen.

    A low-confidence read pauses the job before planning (wait_out_unclear_read): planning from
    a read we don't trust is pressing blind. A read that fails outright (camera error) comes back
    with "error" set: the planner gets the profile's guess for it, marked as one, and the first
    step's verify catches it if the guess was wrong."""
    readings = []
    for control in planner.planning_screens(device, profile):
        reading = {"control_id": control["control_id"], "label": control.get("label", ""),
                   "text": "", "confidence": None, "error": None}
        try:
            read = await read_checked(run, client, control["control_id"])
            reading["text"] = read["text"].strip()
            reading["confidence"] = read["confidence"]
        except Exception as err:
            reading["error"] = str(err) or type(err).__name__
        if reading["error"]:
            log(f"before planning, couldn't read {control['control_id']}: {reading['error']}")
        else:
            log(f"before planning, {control['control_id']} reads {reading['text']!r} (confidence {reading['confidence']})")
        readings.append(reading)
    return readings


def planning_note(readings):
    """The note on plan.ready: which read the plan started from, with its confidence."""
    good = []
    failed = []
    for reading in readings:
        if reading["error"] or not reading["text"]:
            failed.append(reading["control_id"])
        else:
            good.append(reading)
    parts = []
    if good:
        reads = []
        for reading in good:
            reads.append(describe_read(reading))
        parts.append("Planned from " + "; ".join(reads))
    if failed:
        parts.append(f"Couldn't read {', '.join(failed)}, planned from the profile's guess for it")
    if not parts:
        return None
    read = None
    if good:
        read = {"control_id": good[0]["control_id"], "text": good[0]["text"], "confidence": good[0]["confidence"]}
    return make_note("screen_read", ". ".join(parts), read)


async def run_job(run):
    job = run.job
    device_id = job["device"]["device_id"]
    run.profile = planner.load_profile(device_id)
    await on_hardware(hardware.prepare_job, copy.deepcopy(run.profile))
    # every press and read goes through the MCP tools, in memory in this same process
    async with Client(mcp_server.mcp) as client:
        readings = await read_screens_for_planning(run, client, job["device"], run.profile)
        try:
            plan = await asyncio.to_thread(make_plan, device_id, job["request"], readings)
        except PlanRefused as err:
            log(f"planner refused: {err}")
            reason = str(err).strip()
            if reason and reason[-1] not in ".!?":
                reason += "."  # the model's reasons don't always end in one
            if err.unreadable:
                message = f"{reason} Nothing was pressed."  # a camera problem, not an impossible plan
            else:
                message = f"Can't do this on this rig: {reason} Nothing was pressed."
            finish(run, "failed", message, None, note=make_note("planner_refused", message))
            return
        except Exception as err:
            log(f"planning failed: {err}")
            finish(run, "failed", f"Planning failed: {err}", None)
            return

        job["plan"] = plan
        emit(run, "plan.ready", note=planning_note(readings), plan=copy.deepcopy(plan))
        log(f"plan ready, {len(plan['steps'])} steps: {plan['summary']}")

        # score every gated step now, all at once, without holding up the run
        for step in plan["steps"]:
            if step.get("requires_approval"):
                run.scoring_tasks.append(asyncio.create_task(score_in_background(run, step)))

        emit_status(run, "running")

        screen_text = run.profile.get("current_screen")
        for step in plan["steps"]:
            # what the step's screen showed last (a real read, with its confidence), else the
            # profile's guess with no confidence. the approval card shows this one
            before_read = run.last_reads.get(step["expect"].get("control_id"))
            if before_read is not None and not before_read["text"].strip():
                before_read = None  # an empty read says nothing, the guess is better than ""
            keep_going = await run_step(run, client, step, screen_text, before_read)
            if not keep_going:
                return
            screen_text = step["evidence"]["screen_text"] or screen_text

    finish(run, "succeeded", f"Job done, all {len(plan['steps'])} steps verified.", screen_text)


async def wait_for_approval(run, step, screen_before):
    """True once approved. False if it was rejected with abort (the job is over then)."""
    step["status"] = "awaiting_approval"
    emit_step(run, step)
    emit_status(run, "awaiting_approval")

    # keep asking until it's approved or aborted, a pause just holds here
    while True:
        run.decision = asyncio.get_running_loop().create_future()
        run.waiting_step_id = step["step_id"]
        try:
            decision = await run.decision
        finally:
            run.decision = None
            run.waiting_step_id = None

        if decision.get("decision") == "approve":
            emit_status(run, "running")
            return True

        behavior = decision.get("on_reject") or "pause"  # contract default
        # the rejection itself is one log entry, what happens to the step after it is another
        rejected = f"Step {step['index']} rejected by {decision.get('decided_by') or 'you'} ({behavior})"
        if decision.get("note"):
            rejected += f": {decision['note']}"
        emit_step(run, step, note=make_note("rejected", rejected))

        if behavior == "abort":
            step["status"] = "skipped"
            step["error"] = decision.get("note") or "Rejected at approval"
            step["finished_at"] = now()
            emit_step(run, step, note=make_note("step_skipped", f"Step {step['index']} skipped, the finger never moved"))
            finish(run, "stopped", f"Stopped: step {step['index']} was rejected, the finger never moved.", screen_before)
            return False

        # "pause", and "replan" for now (no replanner yet, so it holds like a pause).
        # the step stays awaiting_approval: approving it later resumes, Stop ends the job
        if behavior == "replan":
            log("replan requested (no replanner yet, holding like a pause)")
        emit_status(run, "paused",
                    reason=f"Step {step['index']} was rejected, holding here. Nothing was pressed. "
                           "Approve it to carry on, or Stop.")


def fail_step(run, step, error, screen_text, read=None):
    """read: the ScreenRead the failure is about, if there is one (its confidence goes on the step)."""
    step["status"] = "failed"
    step["error"] = error
    step["finished_at"] = now()
    step["evidence"]["screen_text"] = screen_text
    note = None
    if read is not None:
        step["evidence"]["screen_confidence"] = read["confidence"]
        note = make_note("screen_read", f"Failed after reading: {describe_read(read)}", read)
    emit_step(run, step, note=note)
    finish(run, "failed", f"Job failed at step {step['index']}: {error}", screen_text)


def start_step(run, step):
    """A step's first go: running, attempt 1, and a heads-up to the hardware about it.
    The press tool calls this right before a step's first press, the runner before a
    wait or read step."""
    step["status"] = "running"
    step["attempts"] = 1
    step["started_at"] = now()
    emit_step(run, step)
    hardware.prepare_step(copy.deepcopy(step), copy.deepcopy(run.profile))


def show_read(step, read):
    step["evidence"]["screen_text"] = read["text"]
    step["evidence"]["screen_confidence"] = read["confidence"]


async def run_step(run, client, step, screen_before, before_read=None):
    """Returns False if the job should end here. Presses and reads go through the MCP
    tools on `client`; the approval card and the press budget live inside press.
    before_read: the newest real read of this step's screen (a ScreenRead), or None when
    there's only the profile's guess. A low-confidence read anywhere in here pauses the job
    (read_checked), so nothing is ever pressed or verified on a read we don't trust."""
    run.active_step = step
    if before_read is not None:
        show_read(step, before_read)  # what the approval card shows, with its confidence
    else:
        step["evidence"]["screen_text"] = screen_before
        step["evidence"]["screen_confidence"] = None
    action = step["action"]

    try:
        if action["type"] == "press":
            # press waits for approval if the step needs it, charges the budget, then presses
            arguments = {
                "job_id": run.job["job_id"],
                "step_id": step["step_id"],
                "control_id": action["control_id"],
                "count": action["count"],
                "hold_ms": action.get("hold_ms"),
            }
            try:
                await mcp_server.call(client, "press", arguments)
            except mcp_server.ToolRefused as err:
                if is_finished(run):
                    return False  # rejected with abort, or stopped while the card was up
                fail_step(run, step, str(err), step["evidence"]["screen_text"])
                return False
        else:
            # nothing to press, but a step that needs approval still waits for it (same gate as press)
            if not await mcp_server.ensure_approved(run, step):
                return False
            start_step(run, step)
            if action["type"] == "wait":
                await asyncio.sleep(action["seconds"])
            # read_screen: the read below is the whole step

        step["status"] = "verifying"
        emit_step(run, step)

        expect = step["expect"]
        max_attempts = step.get("max_attempts") or DEFAULT_MAX_ATTEMPTS
        read = await read_checked(run, client, expect["control_id"], step)
        plain_reads = 1
        while not screen_matches(expect, read["text"]):
            correction, problem, error = plan_correction(run, step, read["text"])
            if error:
                fail_step(run, step, error, read["text"], read)
                return False
            if correction is None:
                correction, problem = plan_repeat(run, step, read, before_read, plain_reads)

            if correction is None:
                # nothing safe to press (no numbers to go on, or not an up/down step),
                # so just look again in case the screen is slow. never a blind press
                if plain_reads >= max_attempts:
                    break
                # every read gets one log entry: this one is "not it yet, looking again"
                show_read(step, read)
                message = f"{describe_read(read)}, wanted {describe_expect(expect)}, looking again"
                emit_step(run, step, note=make_note("screen_read", message, read))
                await asyncio.sleep(SECONDS_BETWEEN_READS)
                read = await read_checked(run, client, expect["control_id"], step)
                plain_reads += 1
                continue

            if step["attempts"] >= max_attempts:
                break

            # a self-correction: note what went wrong and what we're pressing about it
            retries = step.get("retries") or []
            retries.append({"attempt": step["attempts"], "screen_text": read["text"],
                            "screen_confidence": read["confidence"], "problem": problem, "correction": correction})
            step["retries"] = retries
            step["attempts"] += 1
            step["status"] = "running"
            show_read(step, read)
            message = f"Retry: {problem}. Pressing {correction['control_id']} x{correction['count']} again"
            emit_step(run, step, note=make_note("retry", message, read))
            log(f"{step['step_id']} retry: {problem}, pressing {correction['control_id']} x{correction['count']}")

            # through the same gate as every other press (so it's charged to the budget too)
            arguments = {
                "job_id": run.job["job_id"],
                "step_id": step["step_id"],
                "control_id": correction["control_id"],
                "count": correction["count"],
                "hold_ms": None,
            }
            try:
                await mcp_server.call(client, "press", arguments)
            except mcp_server.ToolRefused as err:
                if is_finished(run):
                    return False
                fail_step(run, step, f"{problem}, but the correction press was refused: {err}", read["text"], read)
                return False

            step["status"] = "verifying"
            emit_step(run, step)
            read = await read_checked(run, client, expect["control_id"], step)
            plain_reads = 1
    except HardwareStopped:
        # usually the stop handler got there first and the job's already over,
        # but if the rig stopped itself, end the job here
        stop_run(run, "Stopped: the hardware stopped mid-step.")
        return False
    except Exception as err:
        if is_finished(run):
            return False
        fail_step(run, step, f"Hardware error: {err}", screen_before)
        return False

    if not screen_matches(expect, read["text"]):
        error = f"Expected {describe_expect(expect)} on {expect['control_id']}, read {read['text']!r}."
        if step.get("retries"):
            error = (
                f"Still reads {read['text']!r} after {step['attempts']} attempts, wanted {describe_expect(expect)}."
                " Gave up so it doesn't overshoot."
            )
        fail_step(run, step, error, read["text"], read)
        return False

    step["status"] = "succeeded"
    step["finished_at"] = now()
    show_read(step, read)
    emit_step(run, step, note=make_note("screen_read", f"Verified: {describe_read(read)}", read))
    return True


async def run_job_safely(run):
    try:
        await run_job(run)
    except asyncio.CancelledError:
        pass  # stopped, the stop handler already tidied up
    except Exception as err:
        log(f"run crashed: {type(err).__name__}: {err}")
        finish(run, "failed", f"Backend error: {err}", None)


def stop_run(run, message):
    """Ends the job as stopped. Doesn't touch the hardware, the caller does that."""
    if is_finished(run):
        return
    if run.task:
        run.task.cancel()
    if run.decision and not run.decision.done():
        run.decision.cancel()
    if run.resume and not run.resume.done():
        run.resume.cancel()

    # whatever step was mid-press gets marked, so the UI doesn't spin forever
    step = run.active_step
    if step and step["status"] in ("running", "verifying", "awaiting_approval"):
        step["status"] = "skipped"
        step["error"] = "Stopped mid-step"
        step["finished_at"] = now()
        emit_step(run, step)
    finish(run, "stopped", message, None)


async def stop_job_now(run, message):
    """Stop button and the stop tool: end the job, then retract the finger."""
    stop_run(run, message)
    # only the current job can have the finger, don't yank it out from under another one
    if run is current_run:
        await asyncio.to_thread(hardware.stop)


# ---- routes ----


def frame_url_for(device_id):
    # a real camera frame wins. FALLBACK until there's a camera: the web app's placeholder
    # still for this device, or "" (the app shows "No camera frame yet") if there isn't one
    url = hardware.latest_frame_url(device_id)
    if url:
        return url
    if (MOCK_FRAMES_DIR / f"{device_id}.svg").is_file():
        return f"/mock/{device_id}.svg"
    return ""


def apply_calibration(device):
    """Boxes from vision/calibration/<device>.boxes.json win over the fixture's, so the web app
    draws them where they are on this camera. The frame size comes along, since boxes are in
    that frame's pixels. No file (or a broken one) = the fixture's boxes, like before."""
    path = CALIBRATION_DIR / f"{device['device_id']}.boxes.json"
    if not path.is_file():
        return device
    try:
        calibration = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        log(f"ignoring {path.name}, it isn't valid JSON: {err}")
        return device
    boxes = calibration.get("boxes") or {}
    for control in device["controls"]:
        box = boxes.get(control["control_id"])
        if box is not None:
            control["bbox"] = list(box)
    frame_size = calibration.get("frame_size")
    if frame_size:
        device["camera"]["frame_width"] = frame_size[0]
        device["camera"]["frame_height"] = frame_size[1]
    return device


def load_device_with_frame(device_id):
    """The device from the fixture, with the calibrated boxes and a camera frame the app can
    actually load. Raises ValueError for an unknown device."""
    device = apply_calibration(planner.load_device(device_id))
    device["camera"]["latest_frame_url"] = frame_url_for(device_id)
    return device


def get_run(job_id):
    run = runs.get(job_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    return run


async def read_body(http_request):
    try:
        body = await http_request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="body isn't JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body should be a JSON object")
    return body


@app.get("/devices/{device_id}")
async def get_device(device_id: str):
    try:
        return load_device_with_frame(device_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))


@app.post("/jobs", status_code=201)
async def create_job(http_request: Request):
    global current_run
    body = await read_body(http_request)
    device_id = body.get("device_id")
    request_text = body.get("request")
    if not isinstance(device_id, str) or not isinstance(request_text, str) or not request_text.strip():
        raise HTTPException(status_code=400, detail="need device_id and request")
    try:
        device = load_device_with_frame(device_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))

    # one finger: a new job replaces whatever was running
    if current_run and not is_finished(current_run):
        stop_run(current_run, "Stopped: a new job started.")
        await asyncio.to_thread(hardware.stop)

    created = now()
    job = {
        "job_id": "job_" + secrets.token_hex(4),
        "created_at": created,
        "updated_at": created,
        "request": request_text,
        "approval_mode": body.get("approval_mode") or "risky_steps",
        "press_budget": DEFAULT_PRESS_BUDGET,
        "status": "planning",
        "device": device,
        "plan": None,
        "result": None,
        "pause_reason": None,
    }
    run = JobRun(job)
    runs[job["job_id"]] = run
    current_run = run
    emit_status(run, "planning")
    log(f"job {job['job_id']} created: \"{request_text}\"")

    run.task = asyncio.create_task(run_job_safely(run))
    return job


@app.get("/jobs")
async def list_jobs():
    """Every job this server has seen, oldest first. record_run.py uses it to find the next job."""
    jobs = []
    for run in runs.values():
        job = run.job
        jobs.append({"job_id": job["job_id"], "created_at": job["created_at"], "status": job["status"], "request": job["request"]})
    return jobs


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    return get_run(job_id).job


@app.get("/jobs/{job_id}/history")
async def job_history(job_id: str):
    """Every JobEvent the job has sent so far, in order: what the WebSocket sent, from the start.
    (A socket that connects late only gets live events, this has all of them.)"""
    return get_run(job_id).events


@app.post("/jobs/{job_id}/steps/{step_id}/approval")
async def decide(job_id: str, step_id: str, http_request: Request):
    run = get_run(job_id)
    body = await read_body(http_request)
    decision = body.get("decision")
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail='decision must be "approve" or "reject"')
    if run.decision is None or run.decision.done() or run.waiting_step_id != step_id:
        raise HTTPException(status_code=409, detail=f"step {step_id} isn't waiting for approval")
    # a step that switches something on, or wipes something (RESET on a running timer), needs a
    # press-and-hold, not a tap, whatever its risk tier. the web app knows (it reads
    # turns_on_output / destructive), this is so nothing else can skip it
    waiting_step = None
    for step in (run.job.get("plan") or {}).get("steps") or []:
        if step["step_id"] == step_id:
            waiting_step = step
    if (decision == "approve" and waiting_step is not None and planner.needs_hold(waiting_step)
            and body.get("approval_type") != "hold"):
        what = "turns something on" if waiting_step.get("turns_on_output") else "wipes something"
        raise HTTPException(
            status_code=400,
            detail=f"step {step_id} {what}: approve it with a press-and-hold (approval_type \"hold\")",
        )

    what = decision
    if decision == "reject":
        what += f" -> {body.get('on_reject') or 'pause'}"
    log(
        f"step {step_id}: {what} ({body.get('approval_type', '?')}, {body.get('time_to_decide_ms', '?')}ms,"
        f" by {body.get('decided_by', '?')} at {body.get('decided_at', '?')})"
    )
    run.decision.set_result(body)
    await asyncio.sleep(0)  # let the run pick it up so the job we send back is current
    return run.job


@app.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str, http_request: Request):
    """Carry on after a pause for a low-confidence read: the screen gets read again first, and
    if that's still low the job pauses again. Body optional, e.g. {"decided_by": "Pam"}.
    (A step rejected with "pause" resumes by approving it, POST .../approval, not this.)"""
    run = get_run(job_id)
    try:
        body = await http_request.json()
    except ValueError:
        body = {}  # no body is fine
    if not isinstance(body, dict):
        body = {}
    if run.resume is None or run.resume.done():
        raise HTTPException(status_code=409, detail=f"job {job_id} isn't paused on an unclear read")
    log(f"job {job_id}: resume (by {body.get('decided_by', '?')}), reading the screen again")
    run.resume.set_result(body)
    await asyncio.sleep(0)  # let the run pick it up so the job we send back is current
    return run.job


@app.post("/jobs/{job_id}/stop")
async def stop_job(job_id: str):
    run = get_run(job_id)
    await stop_job_now(run, "Stopped by you. Finger retracted.")
    return run.job


async def send_events(websocket, queue):
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except Exception:
        pass  # socket's gone, the receive loop notices and cleans up


@app.websocket("/jobs/{job_id}/events")
async def job_events(websocket: WebSocket, job_id: str):
    run = runs.get(job_id)
    if run is None:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason=f"no job {job_id}")
    await websocket.accept()

    queue = asyncio.Queue()
    # first watcher gets everything so far, reconnects use GET /jobs/:id instead
    if not run.had_watcher:
        run.had_watcher = True
        for event in run.events:
            queue.put_nowait(event)
    run.watchers.append(queue)
    log(f"ws connected to {job_id} ({len(run.watchers)} watching)")
    sender = asyncio.create_task(send_events(websocket, queue))
    try:
        # we don't expect messages, this just notices the socket closing
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        run.watchers.remove(queue)
        sender.cancel()


def main():
    parser = argparse.ArgumentParser(description="Physical MCP backend (FastAPI)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
