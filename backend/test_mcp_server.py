"""Tests for the MCP layer (mcp_server.py) and the runner that goes through it, on FakeHardware.
No rig, camera or API key needed: the plan comes from the Inkbird fixture and the scorer is
stubbed. Run from the repo root:
    python -m unittest backend/test_mcp_server.py -v
"""

import asyncio
import json
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.pop("HARDWARE", None)  # always the fake here, even if the shell says serial

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from mcp import Client  # noqa: E402

import api  # noqa: E402
import mcp_server  # noqa: E402
import planner  # noqa: E402
from hardware import FakeHardware, HardwareStopped  # noqa: E402

REQUEST = "Set the heater to turn on at 80 F"


class RecordingHardware(FakeHardware):
    """FakeHardware that remembers every press, and runs holds faster (hold_scale) unless a
    test wants the real length."""

    def __init__(self, hold_scale=0.01, undershoot=False):
        super().__init__(seconds_per_press=0.01, seconds_per_read=0.01, undershoot=undershoot)
        self.hold_scale = hold_scale
        self.started = []  # (control_id, count, hold_ms) for every press that began
        self.finished = []  # the ones that ran to the end
        self.cut_short = []  # the ones stop() interrupted
        self.stops = 0
        self.frames_saved_at = []

    def press(self, control_id, count, hold_ms=None):
        self.started.append((control_id, count, hold_ms))
        scaled_hold = hold_ms * self.hold_scale if hold_ms else None
        try:
            super().press(control_id, count, scaled_hold)
        except HardwareStopped:
            self.cut_short.append(control_id)
            raise
        self.finished.append((control_id, count, hold_ms))

    def stop(self):
        self.stops += 1
        super().stop()

    def save_frame(self, path=None):
        # the live camera loop calls this. just note when, there's no camera
        self.frames_saved_at.append(time.monotonic())


def fixture_plan():
    """The Inkbird fixture's steps, built the same way make_plan builds Muse Spark's reply:
    s1 hold SET (needs approval), s2 up x5 to 80, s3 hold SET to save, s4 check the HEAT light."""
    fixture = json.loads((REPO_ROOT / "shared" / "fixtures" / "inkbirdJobSample.json").read_text(encoding="utf-8"))
    raw = {"summary": fixture["plan"]["summary"], "steps": []}
    for step in fixture["plan"]["steps"]:
        raw_step = {
            "intent": step["intent"],
            "action": step["action"],
            "expect": step["expect"],
            "requires_approval": step["requires_approval"],
        }
        if step.get("approval_reason"):
            raw_step["approval_reason"] = step["approval_reason"]
        raw["steps"].append(raw_step)
    device = planner.load_device("inkbird-01")
    profile = planner.load_profile("inkbird-01")
    plan, errors = planner.validate_and_build_plan(raw, device, profile)
    assert not errors, errors
    return plan


def stub_score(job, step):
    time.sleep(0.05)  # a real score takes 9 to 14 s and never beats the card
    return {"risk_tier": "high", "risk_reason": "test score", "risk_scored_by": "test"}, {}


class FakeRequest:
    """Enough of a Starlette Request for the route functions."""

    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


def tool_error_text(result):
    text = ""
    for block in result.content:
        text += getattr(block, "text", "")
    return text


class McpLayerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hardware = RecordingHardware()
        api.hardware = self.hardware
        api.make_plan = lambda device_id, request: fixture_plan()
        api.scorer.score_step_with_raw = stub_score
        api.runs.clear()
        api.current_run = None

    async def asyncTearDown(self):
        # don't leave a job running into the next test
        for run in list(api.runs.values()):
            if not api.is_finished(run):
                await api.stop_job_now(run, "test over")

    # ---- helpers ----

    async def start_job(self):
        job = await api.create_job(FakeRequest({"device_id": "inkbird-01", "request": REQUEST}))
        return api.runs[job["job_id"]]

    async def wait_until(self, check, seconds=10):
        deadline = time.monotonic() + seconds
        while not check():
            if time.monotonic() > deadline:
                self.fail("timed out waiting")
            await asyncio.sleep(0.01)

    async def wait_for_card(self, run, step_id="s1"):
        await self.wait_until(lambda: run.waiting_step_id == step_id)

    async def decide(self, run, body):
        await api.decide(run.job["job_id"], run.waiting_step_id, FakeRequest(body))

    async def wait_for_finish(self, run):
        await self.wait_until(lambda: api.is_finished(run), seconds=20)

    def step(self, run, step_id):
        for step in run.job["plan"]["steps"]:
            if step["step_id"] == step_id:
                return step
        self.fail(f"no step {step_id}")

    def manual_run(self, active_step_id):
        """A job that's 'running' with no runner task, so a test can call press itself."""
        job = {
            "job_id": "job_test",
            "created_at": api.now(),
            "updated_at": api.now(),
            "request": REQUEST,
            "approval_mode": "risky_steps",
            "press_budget": api.DEFAULT_PRESS_BUDGET,
            "status": "running",
            "device": api.load_device_with_frame("inkbird-01"),
            "plan": fixture_plan(),
            "result": None,
        }
        run = api.JobRun(job)
        run.profile = planner.load_profile("inkbird-01")
        api.runs[job["job_id"]] = run
        run.active_step = self.step(run, active_step_id)
        return run

    # ---- the tests ----

    async def test_tools_are_listed(self):
        async with Client(mcp_server.mcp) as client:
            listing = await client.list_tools()
        names = []
        for tool in listing.tools:
            names.append(tool.name)
        self.assertEqual(sorted(names), ["get_controls", "get_frame", "press", "read_screen", "stop"])

    async def test_full_inkbird_job(self):
        run = await self.start_job()
        await self.wait_for_card(run)

        # the risky first step is waiting on the card and the finger hasn't moved
        self.assertEqual(run.job["status"], "awaiting_approval")
        self.assertEqual(self.step(run, "s1")["status"], "awaiting_approval")
        self.assertEqual(self.hardware.started, [])

        await self.decide(run, {"decision": "approve"})
        await self.wait_for_finish(run)

        self.assertEqual(run.job["status"], "succeeded", run.job["result"])
        self.assertEqual(self.hardware.finished, [("btn_set", 1, 3000), ("btn_up", 5, None), ("btn_set", 1, 3000)])
        for step in run.job["plan"]["steps"]:
            self.assertEqual(step["status"], "succeeded", step)
        self.assertEqual(self.step(run, "s4")["evidence"]["screen_text"], "ON")
        self.assertEqual(run.presses_used, 7)

        # every event the web app gets, in the same order as before MCP
        kinds = []
        for event in run.events:
            kinds.append(event["type"])
        self.assertEqual(kinds[:3], ["job.status", "plan.ready", "job.status"])
        self.assertEqual(kinds[-1], "job.finished")
        card = None
        first_running = None
        for index, event in enumerate(run.events):
            if event["type"] == "job.status" and event["status"] == "awaiting_approval" and card is None:
                card = index
            if event["type"] == "step.updated" and event["step"]["status"] == "running" and first_running is None:
                first_running = index
        self.assertLess(card, first_running, "s1 has to wait on the card before it runs")

    async def test_press_before_approval_is_refused(self):
        run = await self.start_job()
        await self.wait_for_card(run)
        arguments = {"job_id": run.job["job_id"], "step_id": "s1", "control_id": "btn_set", "count": 1, "hold_ms": 3000}

        # a second client tries to press the risky step while its card is still up
        async with Client(mcp_server.mcp) as client:
            result = await client.call_tool("press", arguments)
            self.assertTrue(result.is_error)
            self.assertIn("still waiting for approval", tool_error_text(result))

            # and a step that isn't running yet is refused too
            later = dict(arguments, step_id="s2", control_id="btn_up", count=1, hold_ms=None)
            result = await client.call_tool("press", later)
            self.assertTrue(result.is_error)
            self.assertIn("isn't the step running now", tool_error_text(result))

        self.assertEqual(self.hardware.started, [])
        self.assertEqual(run.presses_used, 0)
        self.assertEqual(run.waiting_step_id, "s1", "the card is still up for the human")

    async def test_reject_with_abort(self):
        run = await self.start_job()
        await self.wait_for_card(run)
        await self.decide(run, {"decision": "reject", "on_reject": "abort"})
        await self.wait_for_finish(run)

        self.assertEqual(run.job["status"], "stopped")
        self.assertEqual(self.step(run, "s1")["status"], "skipped")
        self.assertEqual(self.hardware.started, [])
        self.assertEqual(run.presses_used, 0)

    async def test_control_not_in_step_is_refused(self):
        run = self.manual_run("s2")  # s2 presses btn_up
        async with Client(mcp_server.mcp) as client:
            result = await client.call_tool("press", {
                "job_id": "job_test", "step_id": "s2", "control_id": "btn_set", "count": 1,
            })
            self.assertTrue(result.is_error)
            self.assertIn("btn_set isn't part of step s2", tool_error_text(result))

            # s3 holds SET, so the arrows aren't part of it
            run.active_step = self.step(run, "s3")
            result = await client.call_tool("press", {
                "job_id": "job_test", "step_id": "s3", "control_id": "btn_up", "count": 1,
            })
            self.assertTrue(result.is_error)

            # unknown job and unknown step
            result = await client.call_tool("press", {"job_id": "job_nope", "step_id": "s2", "control_id": "btn_up", "count": 1})
            self.assertIn("no job job_nope", tool_error_text(result))
            result = await client.call_tool("press", {"job_id": "job_test", "step_id": "s9", "control_id": "btn_up", "count": 1})
            self.assertIn("has no step s9", tool_error_text(result))
        self.assertEqual(self.hardware.started, [])

    async def test_31st_press_is_refused(self):
        run = self.manual_run("s2")
        arguments = {"job_id": "job_test", "step_id": "s2", "control_id": "btn_up", "count": 1}
        async with Client(mcp_server.mcp, raise_exceptions=True) as client:
            for press_number in range(1, 31):
                result = await client.call_tool("press", arguments)
                self.assertFalse(result.is_error, f"press {press_number}: {tool_error_text(result)}")
                self.assertEqual(result.structured_content["presses_used"], press_number)
            result = await client.call_tool("press", arguments)
        self.assertTrue(result.is_error)
        self.assertIn("Would go over the press budget (30 per job), nothing pressed.", tool_error_text(result))
        self.assertEqual(len(self.hardware.started), 30)
        self.assertEqual(run.presses_used, 30)

    async def test_stop_mid_hold(self):
        self.hardware.hold_scale = 1  # the real 3 s hold on SET
        run = await self.start_job()
        await self.wait_for_card(run)
        await self.decide(run, {"decision": "approve"})
        await self.wait_until(lambda: len(self.hardware.started) == 1)

        started = time.monotonic()
        async with Client(mcp_server.mcp) as client:
            result = await client.call_tool("stop", {"job_id": run.job["job_id"]})
        self.assertFalse(result.is_error, tool_error_text(result))
        self.assertEqual(result.structured_content["status"], "stopped")
        await self.wait_until(lambda: self.hardware.cut_short == ["btn_set"], seconds=2)

        self.assertLess(time.monotonic() - started, 1.5, "the hold should end right away, not after 3 s")
        self.assertEqual(run.job["status"], "stopped")
        self.assertEqual(self.step(run, "s1")["status"], "skipped")
        self.assertEqual(self.hardware.finished, [])
        self.assertEqual(self.hardware.stops, 1)

    async def test_live_frames_keep_coming_during_a_press(self):
        """api.py's frame loop is on its own camera thread, not behind the hardware worker."""
        self.hardware.hold_scale = 1  # the real 3 s hold on SET
        run = self.manual_run("s3")  # s3 holds SET for 3 s, no approval needed
        frames_task = asyncio.create_task(api.keep_frames_fresh())
        try:
            await asyncio.sleep(0.6)
            started = time.monotonic()
            async with Client(mcp_server.mcp) as client:
                result = await client.call_tool("press", {
                    "job_id": "job_test", "step_id": "s3", "control_id": "btn_set", "count": 1, "hold_ms": 3000,
                })
            ended = time.monotonic()
        finally:
            frames_task.cancel()
        self.assertFalse(result.is_error, tool_error_text(result))
        self.assertGreater(ended - started, 2.9)
        during = 0
        for moment in self.hardware.frames_saved_at:
            if started <= moment <= ended:
                during += 1
        # one every 0.5 s for 3+ s. stuck behind the press it would be 0
        self.assertGreaterEqual(during, 4, f"only {during} frames during a 3 s hold")
        self.assertEqual(run.presses_used, 1)

    async def test_retry_self_corrects(self):
        self.hardware = RecordingHardware(undershoot=True)  # one up press goes missing, screen reads 79
        api.hardware = self.hardware
        run = await self.start_job()
        await self.wait_for_card(run)
        await self.decide(run, {"decision": "approve"})
        await self.wait_for_finish(run)

        self.assertEqual(run.job["status"], "succeeded", run.job["result"])
        step = self.step(run, "s2")
        self.assertEqual(step["attempts"], 2)
        self.assertEqual(len(step["retries"]), 1)
        self.assertEqual(step["retries"][0]["screen_text"], "79")
        self.assertEqual(step["retries"][0]["correction"], {"type": "press", "control_id": "btn_up", "count": 1})
        self.assertEqual(step["evidence"]["screen_text"], "80")
        # the correction went through the press tool and was charged: 1 + 5 + 1 + 1
        self.assertIn(("btn_up", 1, None), self.hardware.finished)
        self.assertEqual(run.presses_used, 8)

    async def test_web_app_gets_the_calibrated_boxes(self):
        """GET /devices (and get_controls) use vision/calibration/<device>.boxes.json over the fixture."""
        import tempfile

        saved_dir = api.CALIBRATION_DIR
        with tempfile.TemporaryDirectory() as folder:
            api.CALIBRATION_DIR = Path(folder)
            try:
                # no file: the fixture's boxes, like before
                fixture_box = api.load_device_with_frame("inkbird-01")["controls"][0]["bbox"]
                (Path(folder) / "inkbird-01.boxes.json").write_text(json.dumps({
                    "frame_size": [1280, 720],
                    "boxes": {"btn_set": [410, 490, 70, 36]},
                }), encoding="utf-8")
                device = api.load_device_with_frame("inkbird-01")
            finally:
                api.CALIBRATION_DIR = saved_dir
        boxes = {}
        for control in device["controls"]:
            boxes[control["control_id"]] = control["bbox"]
        self.assertEqual(fixture_box, [820, 980, 140, 72])
        self.assertEqual(boxes["btn_set"], [410, 490, 70, 36])
        self.assertEqual(boxes["btn_up"], [1000, 980, 100, 72], "controls not in the file keep the fixture's box")
        self.assertEqual((device["camera"]["frame_width"], device["camera"]["frame_height"]), (1280, 720))

    async def test_read_controls_and_frame(self):
        async with Client(mcp_server.mcp) as client:
            result = await client.call_tool("get_controls", {"device_id": "inkbird-01"})
            self.assertFalse(result.is_error, tool_error_text(result))
            controls = result.structured_content["controls"]
            ids = []
            for control in controls:
                ids.append(control["control_id"])
                self.assertIn("reachable", control)
            self.assertIn("btn_set", ids)

            result = await client.call_tool("get_frame", {"device_id": "inkbird-01"})
            self.assertEqual(result.structured_content["frame_url"], "/mock/inkbird-01.svg")

            self.hardware.next_reads["screen_pv"] = "PV 72.0"
            result = await client.call_tool("read_screen", {"control_id": "screen_pv"})
            self.assertEqual(result.structured_content["text"], "PV 72.0")
            self.assertIsNone(result.structured_content["confidence"])  # FakeHardware has no vision vote

            result = await client.call_tool("get_controls", {"device_id": "nope-01"})
            self.assertTrue(result.is_error)


if __name__ == "__main__":
    unittest.main()
