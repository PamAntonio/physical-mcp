"""MCP server for the rig: the only way an action reaches the machine.

Tools: get_controls, press, read_screen, get_frame, stop.

Muse Spark makes one JSON plan (planner.py), and the step runner in api.py is the MCP client:
it runs every step through these tools over an in-memory connection, Client(mcp), in the same
process as api.py. So one process owns the hardware and the single-worker hardware pool.

press is the approval gate. Before it touches the hardware it checks, in this order:
1. the job and step exist, the job is still going, the step is the one running right now, and
   the control belongs to that step (an up/down step may also press the other arrow, that's
   how a retry fixes an overshoot)
2. a requires_approval step has been approved, waiting on the same approval card as always
3. the press fits in the job's press budget, which it then charges
Anything that fails raises, so the caller gets is_error=True and nothing is pressed.

api.py hands itself over with use_backend(), so the tools share its runs, hardware, approval
futures and events. Importing api here instead would load a second copy of it when api.py runs
as a script, with its own runs and its own hardware.
"""

import asyncio
import copy
from typing import TypedDict

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent

import planner
from hardware import HardwareStopped

mcp = MCPServer("physical-mcp")

# TODO: to let agents outside this process connect, mount Streamable HTTP in api.py:
# app.mount("/", mcp.streamable_http_app()) with mcp.session_manager.run() in the app's lifespan

backend = None  # the api module, set by use_backend()


def use_backend(module):
    global backend
    backend = module


# tool errors start with one of these, so the runner knows what kind of no it got
REFUSED = "refused: "
STOPPED = "stopped: "
HARDWARE_ERROR = "hardware error: "


def refused(message):
    return ToolError(REFUSED + message)


# ---- the gate ----


def find_step(job_id, step_id):
    """(run, step), or a refusal if there's no such job or step."""
    run = backend.runs.get(job_id)
    if run is None:
        raise refused(f"no job {job_id}, nothing pressed")
    plan = run.job.get("plan") or {}
    for step in plan.get("steps") or []:
        if step["step_id"] == step_id:
            return run, step
    raise refused(f"job {job_id} has no step {step_id}, nothing pressed")


def allowed_controls(run, step):
    """The controls a press for this step may touch. A step that isn't a press has none."""
    action = step["action"]
    if action["type"] != "press":
        return set()
    allowed = {action["control_id"]}
    up_id, down_id = backend.up_down_controls(run.job["device"])
    if action["control_id"] in (up_id, down_id):
        # plan_correction may press the other arrow to fix an overshoot
        for control_id in (up_id, down_id):
            if control_id is not None:
                allowed.add(control_id)
    return allowed


async def ensure_approved(run, step):
    """True if the step may go ahead, False if it was rejected (abort ends the job).
    A requires_approval step waits on the approval card here, the same future
    POST .../approval resolves. The runner uses this for steps that don't press, too."""
    if not step.get("requires_approval") or step["step_id"] in run.approved_step_ids:
        return True
    if run.waiting_step_id is not None:
        # the card is already up for this step. a second caller doesn't get to wait (or press)
        raise refused(f"step {step['step_id']} is still waiting for approval, nothing pressed")
    try:
        approved = await backend.wait_for_approval(run, step, step["evidence"]["screen_text"])
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            raise  # this call itself got cancelled, let it go
        # only the approval future got cancelled: the job was stopped while the card was up
        raise refused(f"job {run.job['job_id']} stopped while step {step['step_id']} waited for approval, nothing pressed")
    if approved:
        run.approved_step_ids.add(step["step_id"])
    return approved


async def gated_press(job_id, step_id, control_id, count, hold_ms):
    run, step = find_step(job_id, step_id)
    if backend.is_finished(run):
        raise refused(f"job {job_id} is already {run.job['status']}, nothing pressed")
    if run.active_step is not step:
        raise refused(f"step {step_id} isn't the step running now, nothing pressed")
    if control_id not in allowed_controls(run, step):
        raise refused(f"{control_id} isn't part of step {step_id}, nothing pressed")

    action = step["action"]
    most = max(action.get("count") or 1, backend.MAX_CORRECTION_PRESSES)
    if isinstance(count, bool) or count < 1 or count > most:
        raise refused(f"count {count} isn't allowed for step {step_id} (1 to {most}), nothing pressed")
    if hold_ms is not None and hold_ms != action.get("hold_ms"):
        raise refused(f"hold_ms {hold_ms} isn't what step {step_id} planned, nothing pressed")
    # no hold given: the step's planned one for its own control, else whatever the profile
    # says this control needs (the timer holds every press 1 s, a 250 ms tap doesn't click)
    if hold_ms is None and control_id == action["control_id"]:
        hold_ms = action.get("hold_ms")
    if hold_ms is None:
        hold_ms = planner.required_hold_ms(control_id, run.profile)
    most_hold = planner.max_hold_ms(run.profile)
    if hold_ms is not None and most_hold is not None and hold_ms > most_hold:
        raise refused(f"hold_ms {hold_ms} is over this device's {most_hold} ms limit (it would auto-repeat), nothing pressed")

    if not await ensure_approved(run, step):
        raise refused(f"step {step_id} was rejected at approval, nothing pressed")
    # the job may have been stopped while the card was up
    if backend.is_finished(run):
        raise refused(f"job {job_id} is already {run.job['status']}, nothing pressed")

    # check and charge in one go (no await in between), so two calls can't both squeeze in
    budget = run.job.get("press_budget") or backend.DEFAULT_PRESS_BUDGET
    if run.presses_used + count > budget:
        raise refused(f"Would go over the press budget ({budget} per job), nothing pressed.")
    run.presses_used += count

    try:
        if step["status"] in ("pending", "awaiting_approval"):
            backend.start_step(run, step)  # first press of the step: running, attempt 1, prepare_step
        await backend.on_hardware(backend.hardware.press, control_id, count, hold_ms)
    except HardwareStopped as err:
        raise ToolError(STOPPED + str(err))
    except Exception as err:
        raise ToolError(HARDWARE_ERROR + str(err))

    return {
        "job_id": job_id,
        "step_id": step_id,
        "control_id": control_id,
        "count": count,
        "hold_ms": hold_ms,
        "presses_used": run.presses_used,
        "press_budget": budget,
    }


# ---- tools ----


class ControlInfo(TypedDict):
    control_id: str
    label: str
    kind: str
    bbox: list[float]
    confidence: float
    reachable: bool
    risky: bool
    turns_on_output: bool
    destructive: bool


class Controls(TypedDict):
    device_id: str
    controls: list[ControlInfo]


class Pressed(TypedDict):
    job_id: str
    step_id: str
    control_id: str
    count: int
    hold_ms: int | None
    presses_used: int
    press_budget: int


class ScreenRead(TypedDict):
    control_id: str
    text: str
    confidence: str | None


class Frame(TypedDict):
    device_id: str
    frame_url: str


class Stopped(TypedDict):
    job_id: str
    status: str


@mcp.tool()
async def get_controls(device_id: str) -> Controls:
    """The device's buttons, screens and lights, with where they are in the camera frame.
    reachable is false for a control the finger can't press on this rig. risky = pressing it
    needs approval. turns_on_output / destructive = that approval is always a press-and-hold."""
    try:
        device = backend.load_device_with_frame(device_id)
    except ValueError as err:
        raise ToolError(str(err))
    try:
        profile = planner.load_profile(device_id)
    except ValueError:
        profile = {}  # no profile, so nothing is marked unreachable or risky

    controls = []
    for control in device["controls"]:
        controls.append({
            "control_id": control["control_id"],
            "label": control.get("label", ""),
            "kind": control.get("kind", ""),
            "bbox": control.get("bbox", []),
            "confidence": control.get("confidence", 0.0),
            "reachable": planner.is_reachable(control, profile),
            "risky": planner.is_risky(control, profile),
            "turns_on_output": planner.control_has_mark(control["control_id"], profile, "turns_on_output"),
            "destructive": planner.control_has_mark(control["control_id"], profile, "destructive"),
        })
    return {"device_id": device_id, "controls": controls}


@mcp.tool()
async def press(job_id: str, step_id: str, control_id: str, count: int, hold_ms: int | None = None) -> Pressed:
    """Press control_id `count` times for step step_id of a running job, holding each press
    for hold_ms if given (else the step's planned hold, else the hold the device profile says
    that control needs). This is the approval gate: a step that needs approval waits here
    until a human approves it, and every press is charged to the job's press budget.
    Refused (nothing pressed) for an unknown job or step, a step that isn't running now,
    a control that isn't part of the step, a hold over the device's max_hold_ms, a rejected
    step, or going over the budget."""
    return await gated_press(job_id, step_id, control_id, count, hold_ms)


@mcp.tool()
async def read_screen(control_id: str) -> ScreenRead:
    """What the camera reads on control_id right now. For an indicator light, "ON" or "OFF".
    confidence is the vision vote ("high", "medium" or "low"), or null when the hardware
    doesn't report one."""
    try:
        detail = await backend.on_hardware(backend.hardware.read_screen_detail, control_id)
    except HardwareStopped as err:
        raise ToolError(STOPPED + str(err))
    except Exception as err:
        raise ToolError(HARDWARE_ERROR + str(err))
    return {"control_id": control_id, "text": detail["text"] or "", "confidence": detail.get("confidence")}


@mcp.tool()
async def get_frame(device_id: str) -> Frame:
    """URL of the newest camera frame for the device, or the placeholder still while
    there's no camera. "" if there's neither."""
    try:
        planner.load_device(device_id)
    except ValueError as err:
        raise ToolError(str(err))
    # a real camera grabs a frame here, so keep it off the event loop
    frame_url = await asyncio.to_thread(backend.frame_url_for, device_id)
    return {"device_id": device_id, "frame_url": frame_url}


@mcp.tool()
async def stop(job_id: str) -> Stopped:
    """Stop the job now: the finger retracts and the job ends as "stopped"."""
    run = backend.runs.get(job_id)
    if run is None:
        raise ToolError(f"no job {job_id}")
    await backend.stop_job_now(run, "Stopped through MCP. Finger retracted.")
    return {"job_id": job_id, "status": run.job["status"]}


# ---- client side, for the runner ----


class ToolRefused(Exception):
    """The gate said no (job, step, control, approval or budget). Nothing was pressed."""


async def call(client, tool_name, arguments):
    """client.call_tool, returning the tool's structured result. An is_error result comes back
    as the exception the runner already handles: a refusal -> ToolRefused, a stop ->
    HardwareStopped, a hardware failure (or anything else) -> RuntimeError."""
    result = await client.call_tool(tool_name, arguments)
    if not result.is_error:
        return copy.deepcopy(result.structured_content)

    text = ""
    for block in result.content:
        if isinstance(block, TextContent):
            text += block.text
    # the SDK puts this in front of a ToolError's message
    prefix = f"Error executing tool {tool_name}: "
    if text.startswith(prefix):
        text = text[len(prefix):]
    if text.startswith(REFUSED):
        raise ToolRefused(text[len(REFUSED):])
    if text.startswith(STOPPED):
        raise HardwareStopped(text[len(STOPPED):])
    if text.startswith(HARDWARE_ERROR):
        raise RuntimeError(text[len(HARDWARE_ERROR):])
    raise RuntimeError(text)
