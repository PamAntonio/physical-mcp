#!/usr/bin/env python3
"""HTTP + WebSocket backend for the web app, speaking the contract in shared/types.ts.

    python backend/api.py               # http://127.0.0.1:8000
    python backend/api.py --port 9000

    POST /jobs                                  -> Job (status "planning")
    GET  /jobs/:job_id                          -> Job
    POST /jobs/:job_id/steps/:step_id/approval  -> Job
    POST /jobs/:job_id/stop                     -> Job
    GET  /devices/:device_id                    -> Device
    WS   /jobs/:job_id/events                   -> stream of JobEvent

Same routes and event order as web/mock-server/server.ts, but with a real planner, the real
risk scorer and a Hardware object (backend/hardware.py) doing the pressing:

1. POST /jobs makes the job and starts planning in the background: planner.py asks Muse
   Spark for a plan, then plan.ready goes out.
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


runs = {}  # job_id -> JobRun
current_run = None


def log(message):
    print(f"[api] {message}", flush=True)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def is_finished(run):
    return run.job["status"] in FINISHED


# ---- events ----


def emit(run, event_type, **fields):
    event = {"type": event_type, "job_id": run.job["job_id"], "ts": now()}
    event.update(fields)
    run.job["updated_at"] = event["ts"]
    run.events.append(event)
    for queue in run.watchers:
        queue.put_nowait(event)


def emit_status(run, job_status):
    run.job["status"] = job_status
    emit(run, "job.status", status=job_status)


def emit_step(run, step):
    emit(run, "step.updated", step=copy.deepcopy(step))


def finish(run, outcome, message, final_screen_text):
    if is_finished(run):
        return
    result = {"outcome": outcome, "message": message, "final_screen_text": final_screen_text}
    run.job["status"] = outcome
    run.job["result"] = result
    emit(run, "job.finished", result=result)
    log(f"job {run.job['job_id']} {outcome}: {message}")


# ---- planning and scoring (both block, so they run on threads) ----


def make_plan(device_id, request):
    api_key = os.environ.get("MODEL_API_KEY")
    if not api_key:
        raise RuntimeError("MODEL_API_KEY is not set (put it in the repo-root .env)")
    device = planner.load_device(device_id)
    profile = planner.load_profile(device_id)
    client = OpenAI(base_url=planner.BASE_URL, api_key=api_key, timeout=PLANNER_TIMEOUT_SECONDS)
    content = planner.call_model(client, planner.build_messages(device, profile, request))
    raw = planner.parse_planning_json(content)
    plan, errors = planner.validate_and_build_plan(raw, device, profile)
    if errors:
        raise ValueError("plan failed validation: " + "; ".join(errors))
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


async def run_job(run):
    job = run.job
    device_id = job["device"]["device_id"]
    try:
        plan = await asyncio.to_thread(make_plan, device_id, job["request"])
    except Exception as err:
        log(f"planning failed: {err}")
        finish(run, "failed", f"Planning failed: {err}", None)
        return

    job["plan"] = plan
    emit(run, "plan.ready", plan=copy.deepcopy(plan))
    log(f"plan ready, {len(plan['steps'])} steps: {plan['summary']}")

    # score every gated step now, all at once, without holding up the run
    for step in plan["steps"]:
        if step.get("requires_approval"):
            run.scoring_tasks.append(asyncio.create_task(score_in_background(run, step)))

    emit_status(run, "running")

    run.profile = planner.load_profile(device_id)
    screen_text = run.profile.get("current_screen")
    # every press and read goes through the MCP tools, in memory in this same process
    async with Client(mcp_server.mcp) as client:
        for step in plan["steps"]:
            keep_going = await run_step(run, client, step, screen_text)
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
        if behavior == "abort":
            step["status"] = "skipped"
            step["error"] = decision.get("note") or "Rejected at approval"
            step["finished_at"] = now()
            emit_step(run, step)
            finish(run, "stopped", f"Stopped: step {step['index']} was rejected, the finger never moved.", screen_before)
            return False

        # "pause", and "replan" for now (no replanner yet, so it holds like a pause).
        # the step stays awaiting_approval: approving it later resumes, Stop ends the job
        if behavior == "replan":
            log("replan requested (no replanner yet, holding like a pause)")
        emit_status(run, "paused")


def fail_step(run, step, error, screen_text):
    step["status"] = "failed"
    step["error"] = error
    step["finished_at"] = now()
    step["evidence"]["screen_text"] = screen_text
    emit_step(run, step)
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


async def read_through_mcp(client, control_id):
    result = await mcp_server.call(client, "read_screen", {"control_id": control_id})
    return result["text"]


async def run_step(run, client, step, screen_before):
    """Returns False if the job should end here. Presses and reads go through the MCP
    tools on `client`; the approval card and the press budget live inside press."""
    run.active_step = step
    step["evidence"]["screen_text"] = screen_before
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
                fail_step(run, step, str(err), screen_before)
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
        text = await read_through_mcp(client, expect["control_id"])
        plain_reads = 1
        while not screen_matches(expect, text):
            correction, problem, error = plan_correction(run, step, text)
            if error:
                fail_step(run, step, error, text)
                return False

            if correction is None:
                # nothing safe to press (no numbers to go on, or not an up/down step),
                # so just look again in case the screen is slow. never a blind press
                if plain_reads >= max_attempts:
                    break
                await asyncio.sleep(SECONDS_BETWEEN_READS)
                text = await read_through_mcp(client, expect["control_id"])
                plain_reads += 1
                continue

            if step["attempts"] >= max_attempts:
                break

            # a self-correction: note what went wrong and what we're pressing about it
            retries = step.get("retries") or []
            retries.append({"attempt": step["attempts"], "screen_text": text, "problem": problem, "correction": correction})
            step["retries"] = retries
            step["attempts"] += 1
            step["status"] = "running"
            step["evidence"]["screen_text"] = text
            emit_step(run, step)
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
                fail_step(run, step, f"{problem}, but the correction press was refused: {err}", text)
                return False

            step["status"] = "verifying"
            emit_step(run, step)
            text = await read_through_mcp(client, expect["control_id"])
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

    if not screen_matches(expect, text):
        error = f"Expected {describe_expect(expect)} on {expect['control_id']}, read {text!r}."
        if step.get("retries"):
            error = (
                f"Still reads {text!r} after {step['attempts']} attempts, wanted {describe_expect(expect)}."
                " Gave up so it doesn't overshoot."
            )
        fail_step(run, step, error, text)
        return False

    step["status"] = "succeeded"
    step["finished_at"] = now()
    step["evidence"]["screen_text"] = text
    emit_step(run, step)
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
    }
    run = JobRun(job)
    runs[job["job_id"]] = run
    current_run = run
    emit_status(run, "planning")
    log(f"job {job['job_id']} created: \"{request_text}\"")

    run.task = asyncio.create_task(run_job_safely(run))
    return job


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    return get_run(job_id).job


@app.post("/jobs/{job_id}/steps/{step_id}/approval")
async def decide(job_id: str, step_id: str, http_request: Request):
    run = get_run(job_id)
    body = await read_body(http_request)
    decision = body.get("decision")
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail='decision must be "approve" or "reject"')
    if run.decision is None or run.decision.done() or run.waiting_step_id != step_id:
        raise HTTPException(status_code=409, detail=f"step {step_id} isn't waiting for approval")

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
