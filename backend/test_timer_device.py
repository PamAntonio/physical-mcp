"""Tests for timer-01, the 4-channel kitchen timer, as a backend device. FakeHardware, stubbed
scorer, no rig or API key. The plans are written the way Muse Spark replies and go through the
same validator make_plan uses. Run from the repo root:
    python -m unittest backend/test_timer_device.py -v
"""

import asyncio
import json
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.pop("HARDWARE", None)  # always the fake here

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from fastapi import HTTPException  # noqa: E402
from mcp import Client  # noqa: E402

import api  # noqa: E402
import mcp_server  # noqa: E402
import planner  # noqa: E402
from test_mcp_server import FakeRequest, RecordingHardware, scored, tool_error_text  # noqa: E402

DEVICE = "timer-01"
TIMER_CONTROLS = [
    "btn_t1", "btn_t2", "btn_t3", "btn_t4", "btn_h", "btn_m", "btn_s", "btn_10m", "btn_5m", "btn_10s",
    "btn_reset", "btn_stsp", "screen_t1", "screen_t2", "screen_t3", "screen_t4",
]


class ReadingHardware(RecordingHardware):
    """RecordingHardware that also remembers which screen every read looked at."""

    def __init__(self):
        super().__init__()
        self.reads = []

    def read_screen(self, control_id):
        self.reads.append(control_id)
        return super().read_screen(control_id)


def press_step(intent, control_id, screen, text, requires_approval=False, reason=None):
    """One step the way Muse Spark writes it (planning fields only)."""
    step = {
        "intent": intent,
        "action": {"type": "press", "control_id": control_id, "count": 1},
        "expect": {"control_id": screen, "text_contains": text},
        "requires_approval": requires_approval,
    }
    if reason:
        step["approval_reason"] = reason
    return step


def build_plan(raw_steps):
    plan, errors = planner.validate_and_build_plan(
        {"summary": "test plan", "steps": raw_steps}, planner.load_device(DEVICE), planner.load_profile(DEVICE))
    assert not errors, errors
    return plan


def t2_five_minutes_plan():
    """Set T2 to 5 minutes and start it: pick T2, 5M once, ST/SP (approval), then T2 counts down."""
    return build_plan([
        press_step("Pick channel T2", "btn_t2", "screen_t2", "00:00 00"),
        press_step("Set T2 to 5 minutes", "btn_5m", "screen_t2", "00:05 00"),
        press_step("Start T2", "btn_stsp", "screen_t2", "00:04", True, "Starts T2 counting down"),
    ])


def reset_t1_plan():
    """Reset T1 while it's running. The model left the approval off RESET: the profile adds it."""
    return build_plan([
        press_step("Pick channel T1", "btn_t1", "screen_t1", ":"),  # running, so any time will do
        press_step("Reset T1", "btn_reset", "screen_t1", "00:00 00"),
    ])


class TimerDeviceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hardware = ReadingHardware()
        api.hardware = self.hardware
        api.scorer.score_step_with_raw = scored("medium")
        api.runs.clear()
        api.current_run = None

    async def asyncTearDown(self):
        for run in list(api.runs.values()):
            if not api.is_finished(run):
                await api.stop_job_now(run, "test over")

    # ---- helpers (same shape as test_mcp_server's) ----

    async def start_job(self, request):
        job = await api.create_job(FakeRequest({"device_id": DEVICE, "request": request}))
        return api.runs[job["job_id"]]

    async def wait_until(self, check, seconds=10):
        deadline = time.monotonic() + seconds
        while not check():
            if time.monotonic() > deadline:
                self.fail("timed out waiting")
            await asyncio.sleep(0.01)

    def step(self, run, step_id):
        for step in run.job["plan"]["steps"]:
            if step["step_id"] == step_id:
                return step
        self.fail(f"no step {step_id}")

    async def decide(self, run, body):
        await api.decide(run.job["job_id"], run.waiting_step_id, FakeRequest(body))

    async def wait_for_card_and_score(self, run, step_id):
        await self.wait_until(lambda: run.waiting_step_id == step_id)
        await self.wait_until(lambda: self.step(run, step_id).get("risk_tier") is not None)

    # ---- the device files ----

    def test_profile_fixture_and_boxes_agree(self):
        profile = json.loads((BACKEND_DIR / "devices" / "timer-01.json").read_text(encoding="utf-8"))
        boxes = json.loads((REPO_ROOT / "vision" / "calibration" / "timer-01.boxes.json").read_text(encoding="utf-8"))
        device = planner.load_device(DEVICE)

        self.assertEqual(sorted(profile["controls"]), sorted(TIMER_CONTROLS))
        self.assertEqual(sorted(boxes["boxes"]), sorted(TIMER_CONTROLS), "same ids as vision's boxes")
        fixture_ids = []
        for control in device["controls"]:
            fixture_ids.append(control["control_id"])
            self.assertEqual(control["bbox"], boxes["boxes"][control["control_id"]])
        self.assertEqual(sorted(fixture_ids), sorted(TIMER_CONTROLS))

        # pan/tilt on every control, all still to be found on the rig
        self.assertTrue(profile["_comment"].startswith("PLACEHOLDER"))
        self.assertEqual(profile["rig"], {"pan_servo": 1, "tilt_servo": 0})
        for control_id, entry in profile["controls"].items():
            for key in ("pan_deg", "tilt_up_deg", "tilt_press_deg"):
                self.assertIn(key, entry, control_id)
                self.assertIsNone(entry[key], control_id)

        # the notes say which parts nobody has checked yet
        unverified = 0
        for note in profile["notes"]:
            if "UNVERIFIED" in note:
                unverified += 1
        self.assertGreater(unverified, 0)

    def test_planner_prompt_and_marks(self):
        device = planner.load_device(DEVICE)
        profile = planner.load_profile(DEVICE)
        prompt = planner.build_messages(device, profile, "Set T2 to 5 minutes and start it")[1]["content"]
        self.assertIn("ST/SP starts the picked channel", prompt)
        self.assertIn('"destructive": true', prompt)  # RESET is marked for the model too
        self.assertIn('"destructive": true | false', prompt)

        # ST/SP is risky: a plan that forgets its approval is refused, not run
        raw = [press_step("Start T2", "btn_stsp", "screen_t2", "00:04")]
        _, errors = planner.validate_and_build_plan({"summary": "x", "steps": raw}, device, profile)
        self.assertTrue(any("risky control 'btn_stsp'" in error for error in errors), errors)

        # RESET: approval and the destructive mark get added even when the model leaves them off
        plan = reset_t1_plan()
        reset = plan["steps"][1]
        self.assertIs(reset["requires_approval"], True)
        self.assertIs(reset["destructive"], True)
        self.assertNotIn("turns_on_output", reset)
        self.assertTrue(planner.needs_hold(reset))
        # picking a channel needs nothing
        self.assertIs(plan["steps"][0]["requires_approval"], False)
        self.assertFalse(planner.needs_hold(plan["steps"][0]))

        # a non-boolean destructive is refused
        bad = dict(press_step("Reset T1", "btn_reset", "screen_t1", "00:00 00", True), destructive="yes")
        _, errors = planner.validate_and_build_plan({"summary": "x", "steps": [bad]}, device, profile)
        self.assertTrue(any("'destructive' must be a boolean" in error for error in errors), errors)

    # ---- jobs ----

    async def test_set_t2_to_five_minutes_and_start_it(self):
        api.make_plan = lambda device_id, request: t2_five_minutes_plan()
        run = await self.start_job("Set T2 to 5 minutes and start it")
        await self.wait_for_card_and_score(run, "s3")

        # picking T2 and setting the time ran on their own, ST/SP waits on the card
        self.assertEqual(self.hardware.finished, [("btn_t2", 1, None), ("btn_5m", 1, None)])
        self.assertEqual(self.hardware.started, self.hardware.finished, "ST/SP hasn't moved")
        start = self.step(run, "s3")
        self.assertEqual(start["status"], "awaiting_approval")
        self.assertIs(start["requires_approval"], True)
        self.assertFalse(planner.needs_hold(start), "starting a timer is a normal approval")

        # medium risk, nothing wiped or switched on: a tap is enough
        await self.decide(run, {"decision": "approve", "approval_type": "tap"})
        await self.wait_until(lambda: api.is_finished(run), seconds=20)
        self.assertEqual(run.job["status"], "succeeded", run.job["result"])
        self.assertEqual(self.hardware.finished[-1], ("btn_stsp", 1, None))

        # every verify read T2's row, and the start step saw it counting
        self.assertEqual(self.hardware.reads, ["screen_t2", "screen_t2", "screen_t2"])
        self.assertEqual(start["evidence"]["screen_text"], "00:04")

    async def test_reset_t1_while_running_needs_a_hold(self):
        api.make_plan = lambda device_id, request: reset_t1_plan()
        for tier in ("low", "medium"):
            with self.subTest(tier=tier):
                self.hardware = ReadingHardware()
                api.hardware = self.hardware
                api.scorer.score_step_with_raw = scored(tier)
                run = await self.start_job("Reset T1")
                await self.wait_for_card_and_score(run, "s2")
                self.assertEqual(self.step(run, "s2")["risk_tier"], tier)
                self.assertEqual(self.hardware.finished, [("btn_t1", 1, None)])

                # a tap (or no type at all) is refused, RESET doesn't move, the card stays up
                with self.assertRaises(HTTPException) as refused:
                    await self.decide(run, {"decision": "approve", "approval_type": "tap"})
                self.assertEqual(refused.exception.status_code, 400)
                self.assertIn("wipes something", refused.exception.detail)
                self.assertIn("press-and-hold", refused.exception.detail)
                with self.assertRaises(HTTPException):
                    await self.decide(run, {"decision": "approve"})
                self.assertEqual(run.waiting_step_id, "s2")
                self.assertEqual(self.hardware.started, [("btn_t1", 1, None)])

                await self.decide(run, {"decision": "approve", "approval_type": "hold"})
                await self.wait_until(lambda: api.is_finished(run), seconds=20)
                self.assertEqual(run.job["status"], "succeeded", run.job["result"])
                self.assertEqual(self.hardware.finished[-1], ("btn_reset", 1, None))
                self.assertEqual(self.hardware.reads[-1], "screen_t1")

    # ---- MCP and the routes ----

    async def test_get_controls_and_device_route(self):
        async with Client(mcp_server.mcp) as client:
            result = await client.call_tool("get_controls", {"device_id": DEVICE})
        self.assertFalse(result.is_error, tool_error_text(result))
        controls = {}
        for control in result.structured_content["controls"]:
            controls[control["control_id"]] = control
        self.assertEqual(sorted(controls), sorted(TIMER_CONTROLS))
        self.assertIs(controls["btn_reset"]["destructive"], True)
        self.assertIs(controls["btn_reset"]["risky"], True)
        self.assertIs(controls["btn_stsp"]["risky"], True)
        self.assertIs(controls["btn_stsp"]["destructive"], False)
        self.assertIs(controls["btn_5m"]["risky"], False)
        self.assertEqual(controls["btn_t2"]["bbox"], [700, 386, 82, 81])

        device = await api.get_device(DEVICE)
        self.assertEqual(device["camera"]["latest_frame_url"], "/mock/timer-01.svg")
        self.assertEqual((device["camera"]["frame_width"], device["camera"]["frame_height"]), (959, 1280))


if __name__ == "__main__":
    unittest.main()
