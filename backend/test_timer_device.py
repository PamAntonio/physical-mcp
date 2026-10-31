"""Tests for timer-01, the 4-channel kitchen timer, as a backend device. FakeHardware, stubbed
scorer and model, no rig or API key. Model replies are written the way Muse Spark replies and go
through the real make_plan (prompt, refusal check, validator). Run from the repo root:
    python -m unittest backend/test_timer_device.py -v

The rig has two fixed fingers (H on servo 0, ST/SP on servo 1), every press holds 1 s, and the
planner starts from what the camera reads, not from the profile's current_screen.
"""

import asyncio
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.pop("HARDWARE", None)  # always the fake here

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from fastapi import HTTPException  # noqa: E402
from mcp import Client  # noqa: E402

import api  # noqa: E402
from hardware import FakeHardware  # noqa: E402
import mcp_server  # noqa: E402
import planner  # noqa: E402
from serial_hardware import NotCalibrated, SerialHardware  # noqa: E402
from test_mcp_server import FakeRequest, RecordingHardware, scored, tool_error_text  # noqa: E402
from test_serial_hardware import FakeESP32  # noqa: E402

DEVICE = "timer-01"
TIMER_CONTROLS = [
    "btn_t1", "btn_t2", "btn_t3", "btn_t4", "btn_h", "btn_m", "btn_s", "btn_10m", "btn_5m", "btn_10s",
    "btn_reset", "btn_stsp", "screen_t1", "screen_t2", "screen_t3", "screen_t4",
]
SCREENS = ["screen_t1", "screen_t2", "screen_t3", "screen_t4"]
FINGERS = ["btn_h", "btn_stsp"]  # the only two buttons with a finger on them
TWO_HOURS = "Set T1 to 2 hours and start it"
REAL_MAKE_PLAN = api.make_plan  # grabbed before any test's setUp replaces it


class ReadingHardware(RecordingHardware):
    """RecordingHardware that remembers which screen every read looked at, and starts with
    every channel showing `screens` (what the camera would see before planning). Reads say
    "high" like a vision vote would, except the ones numbered in low_reads (1 = the first read
    of the job, which is the one before planning). miss_press: FakeHardware's missed press."""

    def __init__(self, screens=None, low_reads=(), miss_press=None):
        super().__init__()
        self.reads = []
        self.low_reads = set(low_reads)
        self.miss_press = miss_press
        for control_id in SCREENS:
            self.last_reads[control_id] = "00:00 00"
        for control_id, text in (screens or {}).items():
            self.last_reads[control_id] = text

    def read_screen(self, control_id):
        self.reads.append(control_id)
        return super().read_screen(control_id)

    def read_screen_detail(self, control_id):
        text = self.read_screen(control_id)
        if len(self.reads) in self.low_reads:
            return {"text": text, "confidence": "low"}
        return {"text": text, "confidence": "high"}


def press_step(intent, control_id, text, requires_approval=False, reason=None, hold_ms=None):
    """One step the way Muse Spark writes it (planning fields only), verified on T1's row."""
    action = {"type": "press", "control_id": control_id, "count": 1}
    if hold_ms is not None:
        action["hold_ms"] = hold_ms
    step = {
        "intent": intent,
        "action": action,
        "expect": {"control_id": "screen_t1", "text_contains": text},
        "requires_approval": requires_approval,
    }
    if reason:
        step["approval_reason"] = reason
    return step


def t1_reading_in(prompt):
    """What the prompt says T1 shows right now, e.g. "01:00 00", or None if it doesn't say."""
    match = re.search(r"- screen_t1 \(T1 time\): (\d\d:\d\d \d\d)", prompt)
    return match.group(1) if match else None


def timer_model(prompts, target_hours=2):
    """A stand-in for Muse Spark on "set T1 to N hours and start it": it plans from the T1
    reading in the prompt, the way the real model is told to (H adds 1 hour per press), one H
    per step, then ST/SP. Leaves hold_ms off like a model that forgot it. Every prompt it gets
    goes in `prompts`."""

    def call_model(client, messages):
        prompt = messages[1]["content"]
        prompts.append(prompt)
        reading = t1_reading_in(prompt) or "00:00 00"
        hours = int(reading[:2])
        steps = []
        while hours < target_hours:
            hours += 1
            steps.append(press_step(f"Add an hour to T1 ({hours} h)", "btn_h", f"{hours:02d}:00 00"))
        steps.append(press_step("Start T1", "btn_stsp", f"{target_hours - 1:02d}:59", True, "Starts T1 counting down"))
        return json.dumps({"summary": f"T1 to {target_hours} h, then start it", "steps": steps})

    return call_model


def canned_model(prompts, reply):
    def call_model(client, messages):
        prompts.append(messages[1]["content"])
        return json.dumps(reply)

    return call_model


class TimerDeviceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hardware = ReadingHardware()
        api.hardware = self.hardware
        api.scorer.score_step_with_raw = scored("medium")
        api.runs.clear()
        api.current_run = None
        # the real make_plan runs (the other suites swap in a lambda), only the model call is
        # stubbed, so nothing ever goes out to the API
        self.addCleanup(setattr, api, "make_plan", api.make_plan)
        api.make_plan = REAL_MAKE_PLAN
        self.addCleanup(setattr, planner, "call_model", planner.call_model)
        old_key = os.environ.get("MODEL_API_KEY")
        os.environ["MODEL_API_KEY"] = "test-key-never-sent"
        if old_key is None:
            self.addCleanup(os.environ.pop, "MODEL_API_KEY", None)
        else:
            self.addCleanup(os.environ.__setitem__, "MODEL_API_KEY", old_key)
        self.prompts = []

    async def asyncTearDown(self):
        for run in list(api.runs.values()):
            if not api.is_finished(run):
                await api.stop_job_now(run, "test over")

    # ---- helpers ----

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

    def events(self, run, event_type):
        found = []
        for event in run.events:
            if event["type"] == event_type:
                found.append(event)
        return found

    def note_kinds(self, run):
        kinds = []
        for event in run.events:
            if event.get("note"):
                kinds.append(event["note"]["kind"])
        return kinds

    async def wait_for_card_and_score(self, run, step_id):
        await self.wait_until(lambda: run.waiting_step_id == step_id)
        await self.wait_until(lambda: self.step(run, step_id).get("risk_tier") is not None)

    def validate(self, raw_steps):
        return planner.validate_and_build_plan(
            {"summary": "test plan", "steps": raw_steps}, planner.load_device(DEVICE), planner.load_profile(DEVICE))

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

    def test_two_fixed_fingers_and_a_one_second_hold(self):
        profile = planner.load_profile(DEVICE)
        self.assertNotIn("rig", profile, "no pan/tilt on the timer")
        self.assertEqual(profile["hold_ms"], 1000)
        self.assertEqual(profile["max_hold_ms"], 1800)

        controls = profile["controls"]
        self.assertEqual(controls["btn_h"], {"reachable": True, "servo": 0, "rest_deg": 150, "press_deg": 50,
                                             "retry_if_unchanged": True})
        self.assertEqual(profile["plan_reads"], ["screen_t1"])
        self.assertEqual(controls["btn_stsp"],
                         {"reachable": True, "servo": 1, "rest_deg": None, "press_deg": None, "risky": True,
                          "turns_on_output": True})
        for control_id, entry in controls.items():
            for key in ("pan_deg", "tilt_up_deg", "tilt_press_deg"):
                self.assertNotIn(key, entry, control_id)
            if control_id in SCREENS:
                self.assertIs(entry["reachable"], True, "the camera reads every screen")
            elif control_id not in FINGERS:
                self.assertIs(entry["reachable"], False, control_id)
                for key in ("servo", "rest_deg", "press_deg"):
                    self.assertIsNone(entry[key], control_id)

    def test_notes_say_what_was_checked_on_the_rig(self):
        notes = planner.load_profile(DEVICE)["notes"]
        checked = {"H adds 1 hour": None, "T1 is the picked channel after power-on": None, "20:00 00": None}
        unverified = 0
        for note in notes:
            for phrase in checked:
                if phrase in note:
                    checked[phrase] = note
            if "UNVERIFIED" in note:
                unverified += 1
        for phrase, note in checked.items():
            self.assertIsNotNone(note, f"no note says {phrase!r}")
            self.assertNotIn("UNVERIFIED", note)
        self.assertGreater(unverified, 0, "everything else is still unverified")
        # what M/S/10M/5M/10S/RESET do is still written down, unverified, for when they get fingers
        all_notes = " ".join(notes)
        for phrase in ("10M, 5M and 10S", "RESET clears", "what H does past 23 hours"):
            self.assertIn(phrase, all_notes)

    def test_serial_rig_presses_h_on_its_own_servo(self):
        """serial_hardware's one-servo-per-button form, with the real timer profile and a fake ESP32."""
        fake = FakeESP32()
        rig = SerialHardware(serial_port=fake, device_id="test-device")
        try:
            rig.prepare_step({}, planner.load_profile(DEVICE))
            rig.press("btn_h", 1, 1000)
            self.assertEqual(fake.commands[-2:], ["CAL 0 150 50", "PRESS 0 1 1000"])
            for command in fake.commands:
                self.assertFalse(command.startswith("ANGLE"), "no pan move, the finger is fixed")

            # ST/SP has its servo but no angles until Sunday: refused before anything moves
            sent = len(fake.commands)
            with self.assertRaises(NotCalibrated) as ctx:
                rig.press("btn_stsp", 1, 1000)
            self.assertIn("rest_deg", str(ctx.exception))
            for control_id in ("btn_m", "btn_t2", "btn_reset"):
                with self.assertRaises(NotCalibrated):
                    rig.press(control_id, 1, 1000)
            self.assertEqual(len(fake.commands), sent, "nothing sent for an uncalibrated button")
        finally:
            rig._closed = True

    # ---- the planner ----

    def test_prompt_lists_only_the_two_fingers_and_their_hold(self):
        device = planner.load_device(DEVICE)
        profile = planner.load_profile(DEVICE)
        visible = planner.reachable_device(device, profile)
        visible_ids = []
        for control in visible["controls"]:
            visible_ids.append(control["control_id"])
            if control["control_id"] in FINGERS:
                self.assertEqual(control["hold_ms"], 1000)
            else:
                self.assertNotIn("hold_ms", control, "screens don't get pressed")
        self.assertEqual(sorted(visible_ids), sorted(FINGERS + SCREENS))

        prompt = planner.build_messages(device, profile, TWO_HOURS)[1]["content"]
        self.assertIn("H adds 1 hour", prompt)
        self.assertIn('"refusal"', prompt)
        for control_id in ("btn_t1", "btn_t2", "btn_m", "btn_10m", "btn_reset"):
            self.assertNotIn(f'"{control_id}"', prompt)
        # no camera (the CLI): the profile's screen goes in, marked as a guess
        self.assertIn("a guess from the device profile, NOT read from the camera", prompt)
        self.assertIn("T1 00:00 00", prompt)

    def test_prompt_uses_the_camera_readings(self):
        device = planner.load_device(DEVICE)
        profile = planner.load_profile(DEVICE)
        readings = []
        for control_id in SCREENS:
            readings.append({"control_id": control_id, "label": control_id[-2:].upper() + " time",
                             "text": "00:00 00", "confidence": "high", "error": None})
        readings[0]["text"] = "01:00 00"

        prompt = planner.build_messages(device, profile, TWO_HOURS, readings)[1]["content"]
        self.assertIn("read from the camera just now", prompt)
        self.assertEqual(t1_reading_in(prompt), "01:00 00")
        self.assertNotIn("guess", prompt)

        # a low-confidence read or a failed one: not trusted, and the profile's guess comes back, marked
        readings[0]["confidence"] = "low"
        readings[1]["error"] = "hardware error: Camera returned no frame"
        prompt = planner.build_messages(device, profile, TWO_HOURS, readings)[1]["content"]
        self.assertIsNone(t1_reading_in(prompt))
        self.assertIn("low-confidence read '01:00 00', don't trust it", prompt)
        self.assertIn("screen_t2 (T2 time): couldn't read it (hardware error: Camera returned no frame)", prompt)
        self.assertIn("guess from the device profile (NOT read from the camera", prompt)

    def test_every_press_gets_the_one_second_hold(self):
        # the model left hold_ms off: the planner fills in the device's, so the check passes
        plan, errors = self.validate([
            press_step("Add an hour", "btn_h", "01:00 00"),
            press_step("Start T1", "btn_stsp", "00:59", True, "Starts T1"),
        ])
        self.assertEqual(errors, [])
        for step in plan["steps"]:
            self.assertEqual(step["action"]["hold_ms"], 1000)

        # too short doesn't click, over 1800 auto-repeats: both refused
        _, errors = self.validate([press_step("Add an hour", "btn_h", "01:00 00", hold_ms=250)])
        self.assertTrue(any("needs a hold of 1000 ms but hold_ms is 250" in error for error in errors), errors)
        _, errors = self.validate([press_step("Add an hour", "btn_h", "01:00 00", hold_ms=2000)])
        self.assertTrue(any("over this device's limit of 1800 ms" in error for error in errors), errors)
        _, errors = self.validate([press_step("Add an hour", "btn_h", "01:00 00", hold_ms=1800)])
        self.assertEqual(errors, [])

        # a control's own hold_ms overrides the device's
        profile = planner.load_profile(DEVICE)
        profile["controls"]["btn_h"]["hold_ms"] = 1200
        self.assertEqual(planner.required_hold_ms("btn_h", profile), 1200)
        self.assertEqual(planner.required_hold_ms("btn_stsp", profile), 1000)
        plan, errors = planner.validate_and_build_plan(
            {"summary": "x", "steps": [press_step("Add an hour", "btn_h", "01:00 00")]},
            planner.load_device(DEVICE), profile)
        self.assertEqual(errors, [])
        self.assertEqual(plan["steps"][0]["action"]["hold_ms"], 1200)

    def test_planner_rules(self):
        # ST/SP turns the countdown on: a plan that forgets its approval gets it added, as a hold
        plan, errors = self.validate([press_step("Start T1", "btn_stsp", "00:59")])
        self.assertEqual(errors, [])
        self.assertIs(plan["steps"][0]["requires_approval"], True)
        self.assertIs(plan["steps"][0]["turns_on_output"], True)
        self.assertTrue(planner.needs_hold(plan["steps"][0]))
        # a button with no finger can never be in a plan
        for control_id in ("btn_t2", "btn_m", "btn_5m", "btn_reset"):
            _, errors = self.validate([press_step("x", control_id, "00:05 00", True, "x")])
            self.assertTrue(any(f"{control_id!r} is unreachable on this rig" in error for error in errors), errors)
        # the model saying no is a refusal, not a plan
        plan, errors = planner.validate_and_build_plan(
            {"refusal": "T2 can't be picked"}, planner.load_device(DEVICE), planner.load_profile(DEVICE))
        self.assertIsNone(plan)
        self.assertEqual(errors, ["the planner refused: T2 can't be picked"])

    # ---- jobs: the demo ----

    async def run_two_hours(self):
        """Runs TWO_HOURS through the real make_plan with timer_model, approves ST/SP, returns the run."""
        planner.call_model = timer_model(self.prompts)
        run = await self.start_job(TWO_HOURS)
        await self.wait_until(lambda: run.job.get("plan") is not None or api.is_finished(run))
        self.assertIsNotNone(run.job.get("plan"), run.job.get("result"))
        last = run.job["plan"]["steps"][-1]["step_id"]
        await self.wait_for_card_and_score(run, last)

        start = self.step(run, last)
        self.assertEqual(start["action"]["control_id"], "btn_stsp")
        self.assertEqual(start["status"], "awaiting_approval")
        self.assertIs(start["requires_approval"], True)
        # the model's step doesn't say turns_on_output, the profile's mark on btn_stsp adds it
        self.assertIs(start["turns_on_output"], True)
        self.assertTrue(planner.needs_hold(start), "starting the countdown is the output: always a hold")
        for step in run.job["plan"]["steps"][:-1]:
            self.assertIs(step["requires_approval"], False, "pressing H needs no approval")
            self.assertNotIn("turns_on_output", step)
        self.assertNotIn(("btn_stsp", 1, 1000), self.hardware.started, "ST/SP waits for the card")

        await self.decide(run, {"decision": "approve", "approval_type": "hold"})
        await self.wait_until(lambda: api.is_finished(run), seconds=20)
        self.assertEqual(run.job["status"], "succeeded", run.job["result"])
        return run

    async def test_two_hours_from_zero_presses_h_twice(self):
        run = await self.run_two_hours()

        # the planner was told what the camera read, not the profile's fixed screen
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(t1_reading_in(self.prompts[0]), "00:00 00")
        self.assertIn("read from the camera just now", self.prompts[0])
        self.assertNotIn("guess", self.prompts[0])

        # H, H, then ST/SP, every one a 1 s hold
        self.assertEqual(self.hardware.finished, [("btn_h", 1, 1000), ("btn_h", 1, 1000), ("btn_stsp", 1, 1000)])
        expects = []
        for step in run.job["plan"]["steps"]:
            expects.append((step["expect"]["control_id"], step["expect"]["text_contains"]))
        self.assertEqual(expects, [("screen_t1", "01:00 00"), ("screen_t1", "02:00 00"), ("screen_t1", "01:59")])
        # only screen_t1 read before planning, then screen_t1 verified after each press
        self.assertEqual(self.hardware.reads, ["screen_t1", "screen_t1", "screen_t1", "screen_t1"])
        self.assertNotIn("screen_t2", self.prompts[0].split("Device notes")[0], "T2-T4 aren't read")

    async def test_two_hours_from_one_hour_presses_h_once(self):
        self.hardware = ReadingHardware(screens={"screen_t1": "01:00 00"})
        api.hardware = self.hardware
        run = await self.run_two_hours()

        self.assertEqual(t1_reading_in(self.prompts[0]), "01:00 00")
        self.assertEqual(self.hardware.finished, [("btn_h", 1, 1000), ("btn_stsp", 1, 1000)])
        self.assertEqual(len(run.job["plan"]["steps"]), 2)
        self.assertEqual(self.hardware.reads, ["screen_t1", "screen_t1", "screen_t1"])

    async def test_stock_fake_hardware_reads_real_screen_text_first(self):
        # FakeHardware as `python backend/api.py` runs it (RecordingHardware only adds press
        # records): its first read before planning is the profile's fake_screens, T1 at 00:00 00
        self.hardware = RecordingHardware()
        api.hardware = self.hardware
        await self.run_two_hours()
        self.assertEqual(t1_reading_in(self.prompts[0]), "00:00 00")
        self.assertNotIn("couldn't read", self.prompts[0])
        self.assertNotIn("guess", self.prompts[0])
        plan_note = self.events(run=api.current_run, event_type="plan.ready")[0]["note"]
        self.assertEqual(plan_note["message"], "Planned from screen_t1 reads '00:00 00' (no vision vote)")
        self.assertEqual(plan_note["read"]["text"], "00:00 00")

    def test_fake_screens_never_overwrite_what_is_on_screen(self):
        fake = FakeHardware(seconds_per_press=0.0, seconds_per_read=0.0)
        fake.last_reads["screen_t1"] = "01:59 30"  # a job before this one left T1 counting
        fake.prepare_job(planner.load_profile(DEVICE))
        self.assertEqual(fake.read_screen("screen_t1"), "01:59 30")
        self.assertEqual(fake.read_screen("screen_t2"), "00:00 00")
        # no fake_screens in a profile (the Inkbird): nothing changes, reads stay empty
        fresh = FakeHardware(seconds_per_press=0.0, seconds_per_read=0.0)
        fresh.prepare_job(planner.load_profile("inkbird-01"))
        self.assertEqual(fresh.read_screen("screen_pv"), "")

    async def test_empty_read_before_planning_is_refused_not_run_on_the_guess(self):
        # hardware that really reads nothing (no fake_screens, a dead camera feed): the planner
        # still gets the profile's guess, marked as one, but the plan check has no real T1 to
        # simulate from, so the plan is refused instead of pressing on a guess
        self.hardware = ReadingHardware()
        self.hardware.prepare_job = lambda profile: None
        self.hardware.last_reads.clear()
        api.hardware = self.hardware
        planner.call_model = timer_model(self.prompts)
        run = await self.start_job(TWO_HOURS)
        await self.wait_until(lambda: api.is_finished(run))
        self.assertIn("screen_t1 (T1 time): couldn't read it (empty read)", self.prompts[0])
        self.assertIn("NOT read from the camera", self.prompts[0])
        self.assertEqual(run.job["status"], "failed")
        self.assertIn("I couldn't read T1 before planning", run.job["result"]["message"])
        self.assertEqual(self.events(run, "job.finished")[0]["note"]["kind"], "planner_refused")
        self.assertEqual(self.hardware.started, [])

    # ---- the plan is checked against the screen, in code ----

    def plan_from(self, raw_steps):
        plan, errors = self.validate(raw_steps)
        self.assertEqual(errors, [])
        return plan

    def t1_read(self, text, confidence="high"):
        return [{"control_id": "screen_t1", "label": "T1 time", "text": text, "confidence": confidence, "error": None}]

    def two_hours_steps(self, *h_expects, start_expect="01:59"):
        steps = []
        for text in h_expects:
            steps.append(press_step("Add an hour", "btn_h", text))
        steps.append(press_step("Start T1", "btn_stsp", start_expect, True, "Starts T1"))
        return steps

    def test_plan_check_passes_plans_that_add_up(self):
        profile = planner.load_profile(DEVICE)
        # from 00:00 00: H, H, ST/SP
        plan = self.plan_from(self.two_hours_steps("01:00 00", "02:00 00"))
        self.assertIsNone(planner.check_plan(plan, self.t1_read("00:00 00"), TWO_HOURS, profile))
        # from 01:00 00: one H, ST/SP. short expects ("02:00") pass too, the runner checks "contains"
        plan = self.plan_from(self.two_hours_steps("02:00"))
        self.assertIsNone(planner.check_plan(plan, self.t1_read("01:00 00"), TWO_HOURS, profile))
        # one step with count 2 adds two hours
        plan = self.plan_from([dict(press_step("Add two hours", "btn_h", "02:00 00"),
                                    action={"type": "press", "control_id": "btn_h", "count": 2}),
                               press_step("Start T1", "btn_stsp", "01:59", True, "Starts T1")])
        self.assertIsNone(planner.check_plan(plan, self.t1_read("00:00 00"), TWO_HOURS, profile))
        # no time in the request: only the expects are checked
        plan = self.plan_from([press_step("Start T1", "btn_stsp", "00:59", True, "Starts T1")])
        self.assertIsNone(planner.check_plan(plan, self.t1_read("01:00 00"), "Start T1", profile))
        # a device without plan_check isn't checked at all
        self.assertIsNone(planner.check_plan(plan, [], TWO_HOURS, planner.load_profile("inkbird-01")))

    def test_plan_check_refuses_what_cant_be_reached(self):
        profile = planner.load_profile(DEVICE)
        plan = self.plan_from(self.two_hours_steps("02:00 00"))
        reason = planner.check_plan(plan, self.t1_read("01:59 00"), TWO_HOURS, profile)
        self.assertEqual(reason, "T1 shows 01:59 00. I can add whole hours with H and there's no finger on "
                                 "RESET, so I can't get to 02:00 00. Reset the timer by hand and try again.")
        # already past the target: H can't go down
        reason = planner.check_plan(plan, self.t1_read("03:00 00"), TWO_HOURS, profile)
        self.assertIn("T1 shows 03:00 00", reason)
        self.assertIn("can't get to 02:00 00", reason)
        # minutes can't be added at all
        reason = planner.check_plan(plan, self.t1_read("00:00 00"), "Set T1 to 1 hour 30 minutes", profile)
        self.assertIn("can't get to 01:30 00", reason)

    def test_plan_check_rejects_wrong_expects_and_wrong_totals(self):
        profile = planner.load_profile(DEVICE)
        # the planner didn't refuse, but step 2 expects 03:00 00 where H gets to 02:00 00
        plan = self.plan_from(self.two_hours_steps("01:00 00", "03:00 00"))
        reason = planner.check_plan(plan, self.t1_read("00:00 00"), TWO_HOURS, profile)
        self.assertEqual(reason, "Step 2 expects '03:00 00' on screen_t1, but from 00:00 00 the screen would "
                                 "show 02:00 00 by then. The plan doesn't add up, so I won't run it.")
        # every expect lines up, but one H only gets to 1 hour
        plan = self.plan_from(self.two_hours_steps("01:00 00", start_expect="00:59"))
        reason = planner.check_plan(plan, self.t1_read("00:00 00"), TWO_HOURS, profile)
        self.assertEqual(reason, "The plan would set T1 to 01:00 00, but you asked for 02:00 00 (it shows 00:00 00 now).")
        # starting at 00:00 00, or a countdown expect that isn't what starting shows
        plan = self.plan_from([press_step("Start T1", "btn_stsp", "00:00", True, "Starts T1")])
        self.assertIn("there's no time on it", planner.check_plan(plan, self.t1_read("00:00 00"), "Start T1", profile))
        plan = self.plan_from(self.two_hours_steps("01:00 00", "02:00 00", start_expect="02:00 00"))
        self.assertIn("would show 01:59 59", planner.check_plan(plan, self.t1_read("00:00 00"), TWO_HOURS, profile))
        # no real read to start from (failed, or low): refused, never simulated from a guess
        for reading in (self.t1_read("", None), self.t1_read("01:00 00", "low"),
                        [{"control_id": "screen_t1", "text": "", "confidence": None, "error": "camera"}]):
            self.assertIn("couldn't read T1 before planning", planner.check_plan(plan, reading, TWO_HOURS, profile))

    async def test_two_hours_from_0159_is_refused_with_the_reason(self):
        # timer_model is the model's mistake from the live run: from 01:59 it presses H once
        self.hardware = ReadingHardware(screens={"screen_t1": "01:59 00"})
        api.hardware = self.hardware
        planner.call_model = timer_model(self.prompts)
        run = await self.start_job(TWO_HOURS)
        await self.wait_until(lambda: api.is_finished(run))

        self.assertEqual(t1_reading_in(self.prompts[0]), "01:59 00")
        self.assertEqual(run.job["status"], "failed")
        self.assertIsNone(run.job["plan"], "a refused plan never goes out")
        finished = self.events(run, "job.finished")[0]
        self.assertEqual(finished["note"]["kind"], "planner_refused")
        self.assertEqual(finished["result"]["message"],
                         "Can't do this on this rig: T1 shows 01:59 00. I can add whole hours with H and there's no "
                         "finger on RESET, so I can't get to 02:00 00. Reset the timer by hand and try again. "
                         "Nothing was pressed.")
        self.assertEqual(self.hardware.started, [])

    async def test_wrong_expect_is_refused_even_if_the_planner_did_not(self):
        planner.call_model = canned_model(self.prompts, {"summary": "T1 to 2 hours", "steps": self.two_hours_steps(
            "01:00 00", "03:00 00")})
        run = await self.start_job(TWO_HOURS)
        await self.wait_until(lambda: api.is_finished(run))
        self.assertEqual(run.job["status"], "failed")
        self.assertEqual(self.events(run, "job.finished")[0]["note"]["kind"], "planner_refused")
        self.assertIn("Step 2 expects '03:00 00'", run.job["result"]["message"])
        self.assertEqual(self.hardware.started, [])

    def test_prompt_says_h_only_adds_and_reset_is_unreachable(self):
        prompt = planner.build_messages(planner.load_device(DEVICE), planner.load_profile(DEVICE), TWO_HOURS,
                                        self.t1_read("01:59 00"))[1]["content"]
        self.assertIn("H only ever adds time, and RESET has no finger", prompt)
        self.assertIn("T1 shows 01:59 00 and 2 hours is asked", prompt)
        self.assertIn("refuse", prompt)

    def test_fake_screens_env_starts_t1_elsewhere(self):
        with mock.patch.dict(os.environ, {"FAKE_SCREENS": '{"screen_t1": "01:59 00"}'}):
            fake = FakeHardware(seconds_per_press=0.0, seconds_per_read=0.0)
            fake.prepare_job(planner.load_profile(DEVICE))
        self.assertEqual(fake.read_screen("screen_t1"), "01:59 00")
        self.assertEqual(fake.read_screen("screen_t2"), "00:00 00")
        with mock.patch.dict(os.environ, {"FAKE_SCREENS": "01:59"}):
            with self.assertRaises(ValueError):
                FakeHardware(seconds_per_press=0.0, seconds_per_read=0.0).prepare_job(planner.load_profile(DEVICE))

    # ---- jobs: things the rig can't do ----

    async def test_t2_is_refused_not_planned(self):
        planner.call_model = canned_model(self.prompts, {
            "refusal": "T2 can't be picked (no finger on T1 to T4) and minutes can't be set (only H can be pressed)."})
        run = await self.start_job("Set T2 to 5 minutes")
        await self.wait_until(lambda: api.is_finished(run))

        self.assertEqual(run.job["status"], "failed")
        self.assertIsNone(run.job["plan"])
        self.assertTrue(run.job["result"]["message"].startswith("Can't do this on this rig: T2 can't be picked"),
                        run.job["result"]["message"])
        self.assertIn("Nothing was pressed", run.job["result"]["message"])
        self.assertEqual(self.hardware.started, [])
        # the prompt never offered T2 or 5M
        self.assertNotIn('"btn_t2"', self.prompts[0])
        self.assertNotIn('"btn_5m"', self.prompts[0])

    async def test_t2_plan_from_a_confused_model_is_refused_too(self):
        # if the model plans with buttons that have no finger anyway, the validator stops it
        planner.call_model = canned_model(self.prompts, {"summary": "T2 to 5 minutes", "steps": [
            press_step("Pick T2", "btn_t2", "00:00 00"),
            press_step("Add 5 minutes", "btn_5m", "00:05 00"),
        ]})
        run = await self.start_job("Set T2 to 5 minutes")
        await self.wait_until(lambda: api.is_finished(run))

        self.assertEqual(run.job["status"], "failed")
        self.assertIsNone(run.job["plan"])
        message = run.job["result"]["message"]
        self.assertIn("'btn_t2' is unreachable on this rig", message)
        self.assertIn("'btn_5m' is unreachable on this rig", message)
        self.assertEqual(self.hardware.started, [])

    # ---- only screen_t1 before planning ----

    def test_planning_reads_only_screen_t1(self):
        ids = []
        for control in planner.planning_screens(planner.load_device(DEVICE), planner.load_profile(DEVICE)):
            ids.append(control["control_id"])
        self.assertEqual(ids, ["screen_t1"])
        # no plan_reads in a profile = every reachable screen, like before (the Inkbird)
        inkbird = []
        for control in planner.planning_screens(planner.load_device("inkbird-01"), planner.load_profile("inkbird-01")):
            inkbird.append(control["control_id"])
        self.assertIn("screen_pv", inkbird)
        self.assertGreater(len(inkbird), 1)

    # ---- low-confidence reads pause, never press blind ----

    async def resume(self, run, expect_status=None):
        await self.wait_until(lambda: run.resume is not None)
        await api.resume_job(run.job["job_id"], FakeRequest({"decided_by": "test"}))

    async def test_low_read_before_planning_pauses_until_resumed(self):
        self.hardware = ReadingHardware(low_reads={1})
        api.hardware = self.hardware
        planner.call_model = timer_model(self.prompts)
        run = await self.start_job(TWO_HOURS)
        await self.wait_until(lambda: run.resume is not None)

        # paused before planning: no plan, the model never asked, nothing pressed
        self.assertEqual(run.job["status"], "paused")
        self.assertIn("Couldn't read screen_t1 clearly (before planning)", run.job["pause_reason"])
        self.assertIn("Nothing was pressed", run.job["pause_reason"])
        self.assertIsNone(run.job["plan"])
        self.assertEqual(self.prompts, [])
        self.assertEqual(self.hardware.started, [])
        paused = self.events(run, "job.status")[-1]
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["reason"], run.job["pause_reason"])
        self.assertEqual(paused["note"]["kind"], "read_unclear")
        self.assertEqual(paused["note"]["read"], {"control_id": "screen_t1", "text": "00:00 00", "confidence": "low"})

        # resuming reads the screen again first, then plans from that read
        await self.resume(run)
        await self.wait_until(lambda: run.job.get("plan") is not None)
        self.assertEqual(self.hardware.reads[:2], ["screen_t1", "screen_t1"])
        self.assertIsNone(run.job["pause_reason"])
        self.assertEqual(t1_reading_in(self.prompts[0]), "00:00 00")
        kinds = self.note_kinds(run)
        self.assertLess(kinds.index("read_unclear"), kinds.index("resumed"))
        await self.wait_for_card_and_score(run, "s3")
        await self.decide(run, {"decision": "approve", "approval_type": "hold"})
        await self.wait_until(lambda: api.is_finished(run), seconds=20)
        self.assertEqual(run.job["status"], "succeeded", run.job["result"])

    async def test_low_read_after_a_press_pauses_and_never_presses_blind(self):
        # read 1 is before planning, read 2 checks the first H, read 3 is the re-read after resuming
        self.hardware = ReadingHardware(low_reads={2, 3})
        api.hardware = self.hardware
        planner.call_model = timer_model(self.prompts)
        run = await self.start_job(TWO_HOURS)
        await self.wait_until(lambda: run.resume is not None)

        self.assertEqual(run.job["status"], "paused")
        self.assertIn("Couldn't read screen_t1 clearly (step 1)", run.job["pause_reason"])
        s1 = self.step(run, "s1")
        self.assertEqual(s1["status"], "verifying", "not passed on a read we don't trust")
        self.assertEqual(s1["evidence"]["screen_confidence"], "low")
        self.assertEqual(self.hardware.started, [("btn_h", 1, 1000)], "nothing pressed after the low read")

        # resume, but the re-read is low too: paused again, still nothing pressed
        await self.resume(run)
        await self.wait_until(lambda: len(self.hardware.reads) >= 3 and run.resume is not None)
        self.assertEqual(run.job["status"], "paused")
        self.assertEqual(self.note_kinds(run).count("read_unclear"), 2)
        self.assertEqual(self.hardware.started, [("btn_h", 1, 1000)])

        # a clear read this time: the step verifies and the job carries on
        await self.resume(run)
        await self.wait_for_card_and_score(run, "s3")
        self.assertEqual(s1["status"], "succeeded")
        self.assertEqual(s1["evidence"]["screen_confidence"], "high")
        await self.decide(run, {"decision": "approve", "approval_type": "hold"})
        await self.wait_until(lambda: api.is_finished(run), seconds=20)
        self.assertEqual(run.job["status"], "succeeded", run.job["result"])
        self.assertEqual(self.hardware.finished, [("btn_h", 1, 1000), ("btn_h", 1, 1000), ("btn_stsp", 1, 1000)])

    async def test_resume_needs_a_read_pause_and_stop_ends_one(self):
        self.hardware = ReadingHardware(low_reads={1})
        api.hardware = self.hardware
        planner.call_model = timer_model(self.prompts)
        run = await self.start_job(TWO_HOURS)
        await self.wait_until(lambda: run.resume is not None)
        await api.stop_job(run.job["job_id"])
        self.assertEqual(run.job["status"], "stopped")
        self.assertIsNone(run.job["pause_reason"])
        self.assertEqual(self.hardware.started, [])
        # nothing to resume now (or ever, on a job that never paused on a read)
        with self.assertRaises(HTTPException) as ctx:
            await api.resume_job(run.job["job_id"], FakeRequest({}))
        self.assertEqual(ctx.exception.status_code, 409)

    # ---- confidence on every read's log entry and on the approval card ----

    def read_notes(self, run):
        notes = []
        for event in run.events:
            if event.get("note") and "read" in event["note"]:
                notes.append(event["note"])
        return notes

    async def test_confidence_on_every_read_and_on_the_approval_card(self):
        planner.call_model = timer_model(self.prompts)
        run = await self.start_job(TWO_HOURS)
        await self.wait_for_card_and_score(run, "s3")

        # the card's step carries the read it shows (the last read of screen_t1) and how sure it is
        card = self.step(run, "s3")
        self.assertEqual(card["evidence"]["screen_text"], "02:00 00")
        self.assertEqual(card["evidence"]["screen_confidence"], "high")
        last_card_event = None
        for event in run.events:
            if event["type"] == "step.updated" and event["step"]["status"] == "awaiting_approval":
                last_card_event = event
        self.assertEqual(last_card_event["step"]["evidence"]["screen_confidence"], "high")

        await self.decide(run, {"decision": "approve", "approval_type": "hold"})
        await self.wait_until(lambda: api.is_finished(run), seconds=20)
        # one log entry per read, each with its level: the read before planning + 3 verifies
        notes = self.read_notes(run)
        self.assertEqual(len(notes), len(self.hardware.reads))
        for note in notes:
            self.assertIn(note["read"]["confidence"], ("high", "medium", "low"))
        self.assertEqual(self.events(run, "plan.ready")[0]["note"]["read"]["confidence"], "high")
        for step in run.job["plan"]["steps"]:
            self.assertEqual(step["evidence"]["screen_confidence"], "high")

    # ---- retries, skips, rejections and refusals look different ----

    async def test_missed_h_is_retried(self):
        # the second H doesn't register, so screen_t1 stays on 01:00 00: looked at twice, then H again
        self.hardware = ReadingHardware(miss_press="btn_h")
        api.hardware = self.hardware
        planner.call_model = timer_model(self.prompts)
        run = await self.start_job(TWO_HOURS)
        await self.wait_for_card_and_score(run, "s3")

        self.assertEqual(self.hardware.finished, [("btn_h", 1, 1000)] * 3)
        s2 = self.step(run, "s2")
        self.assertEqual(s2["status"], "succeeded")
        self.assertEqual(len(s2["retries"]), 1)
        retry = s2["retries"][0]
        self.assertEqual(retry["screen_text"], "01:00 00")
        self.assertEqual(retry["screen_confidence"], "high")
        self.assertEqual(retry["correction"], {"type": "press", "control_id": "btn_h", "count": 1})
        self.assertIn("didn't register", retry["problem"])
        retry_notes = []
        for event in run.events:
            if event.get("note", {}).get("kind") == "retry":
                retry_notes.append(event["note"])
        self.assertEqual(len(retry_notes), 1)
        self.assertEqual(retry_notes[0]["read"]["confidence"], "high")
        self.assertEqual(len(self.read_notes(run)), len(self.hardware.reads), "the re-reads get entries too")

        await self.decide(run, {"decision": "approve", "approval_type": "hold"})
        await self.wait_until(lambda: api.is_finished(run), seconds=20)
        self.assertEqual(run.job["status"], "succeeded", run.job["result"])

    def test_unchanged_screen_retry_rules(self):
        plan, errors = self.validate([
            press_step("Add an hour", "btn_h", "02:00 00"),
            press_step("Start T1", "btn_stsp", "01:59", True, "Starts T1"),
        ])
        self.assertEqual(errors, [])
        run = api.JobRun({"device": planner.load_device(DEVICE)})
        run.profile = planner.load_profile(DEVICE)
        h, stsp = plan["steps"]
        before = {"control_id": "screen_t1", "text": "01:00 00", "confidence": "high"}
        same = {"control_id": "screen_t1", "text": "01:00  00", "confidence": "high"}
        changed = {"control_id": "screen_t1", "text": "03:00 00", "confidence": "high"}

        correction, problem = api.plan_repeat(run, h, same, before, plain_reads=2)
        self.assertEqual(correction, {"type": "press", "control_id": "btn_h", "count": 1})
        self.assertIn("didn't register", problem)
        # not yet: only one look at it
        self.assertEqual(api.plan_repeat(run, h, same, before, plain_reads=1), (None, None))
        # it changed, just not to what we wanted: never guess
        self.assertEqual(api.plan_repeat(run, h, changed, before, plain_reads=2), (None, None))
        # nothing real to compare with (the profile's guess)
        self.assertEqual(api.plan_repeat(run, h, same, None, plain_reads=2), (None, None))
        # ST/SP: needs approval, and pressing it twice would stop what it started
        self.assertEqual(api.plan_repeat(run, stsp, same, before, plain_reads=2), (None, None))

    async def reject_card(self, on_reject):
        planner.call_model = timer_model(self.prompts)
        run = await self.start_job(TWO_HOURS)
        await self.wait_for_card_and_score(run, "s3")
        await self.decide(run, {"decision": "reject", "on_reject": on_reject, "note": "not now", "decided_by": "Pam"})
        return run

    async def test_rejection_and_skip_are_separate_entries(self):
        run = await self.reject_card("abort")
        await self.wait_until(lambda: api.is_finished(run))
        self.assertEqual(run.job["status"], "stopped")
        kinds = self.note_kinds(run)
        self.assertEqual(kinds[-2:], ["rejected", "step_skipped"])
        rejected = None
        skipped = None
        for event in run.events:
            kind = event.get("note", {}).get("kind")
            if kind == "rejected":
                rejected = event
            if kind == "step_skipped":
                skipped = event
        self.assertEqual(rejected["note"]["message"], "Step 3 rejected by Pam (abort): not now")
        self.assertEqual(rejected["step"]["status"], "awaiting_approval")
        self.assertEqual(skipped["step"]["status"], "skipped")
        self.assertNotIn(("btn_stsp", 1, 1000), self.hardware.started)

    async def test_rejection_with_pause_says_why(self):
        run = await self.reject_card("pause")
        await self.wait_until(lambda: run.job["status"] == "paused")
        self.assertIn("Step 3 was rejected, holding here", run.job["pause_reason"])
        self.assertEqual(self.note_kinds(run)[-1], "rejected")
        self.assertNotIn("step_skipped", self.note_kinds(run))
        self.assertIsNone(run.resume, "a rejected step resumes by approving it, not POST .../resume")

    async def test_refusal_is_its_own_entry(self):
        planner.call_model = canned_model(self.prompts, {"refusal": "T2 can't be picked on this rig"})
        run = await self.start_job("Set T2 to 5 minutes")
        await self.wait_until(lambda: api.is_finished(run))
        finished = self.events(run, "job.finished")[0]
        self.assertEqual(finished["note"]["kind"], "planner_refused")
        self.assertEqual(finished["note"]["message"], finished["result"]["message"])
        # the canned reason has no full stop, the runner adds one before "Nothing was pressed."
        self.assertEqual(finished["result"]["message"], "Can't do this on this rig: T2 can't be picked on this rig. Nothing was pressed.")
        # a plain planning failure isn't marked a refusal
        planner.call_model = canned_model(self.prompts, {"summary": "x", "steps": [press_step("x", "btn_t2", "x")]})
        run = await self.start_job("Set T2 to 5 minutes")
        await self.wait_until(lambda: api.is_finished(run))
        self.assertNotIn("note", self.events(run, "job.finished")[0])

    # ---- ST/SP turns the countdown on: always a hold ----

    async def test_tap_on_stsp_is_refused_even_at_low_risk(self):
        """The real backend path: real make_plan, the approval route, the MCP press gate."""
        api.scorer.score_step_with_raw = scored("low")
        planner.call_model = timer_model(self.prompts)
        run = await self.start_job(TWO_HOURS)
        await self.wait_for_card_and_score(run, "s3")
        self.assertEqual(self.step(run, "s3")["risk_tier"], "low", "the scorer says low...")

        # ...but a tap, or no approval type at all, is refused and ST/SP doesn't move
        for body in ({"decision": "approve", "approval_type": "tap"}, {"decision": "approve"}):
            with self.assertRaises(HTTPException) as refused:
                await self.decide(run, body)
            self.assertEqual(refused.exception.status_code, 400)
            self.assertIn("turns something on", refused.exception.detail)
            self.assertIn("press-and-hold", refused.exception.detail)
        self.assertEqual(run.waiting_step_id, "s3", "the card stays up")
        self.assertEqual(run.job["status"], "awaiting_approval")
        self.assertNotIn(("btn_stsp", 1, 1000), self.hardware.started)

        await self.decide(run, {"decision": "approve", "approval_type": "hold"})
        await self.wait_until(lambda: api.is_finished(run), seconds=20)
        self.assertEqual(run.job["status"], "succeeded", run.job["result"])
        self.assertEqual(self.hardware.finished[-1], ("btn_stsp", 1, 1000))

    # ---- FakeHardware confidence, opt-in ----

    def test_fake_confidence_is_off_unless_asked(self):
        quick = {"seconds_per_press": 0.0, "seconds_per_read": 0.0}
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FAKE_CONFIDENCE", None)
            os.environ.pop("FAKE_LOW_READ", None)
            self.assertIsNone(FakeHardware(**quick).read_screen_detail("screen_t1")["confidence"])

        for level in ("high", "medium", "low"):
            with mock.patch.dict(os.environ, {"FAKE_CONFIDENCE": level}):
                fake = FakeHardware(**quick)
                for _ in range(3):
                    self.assertEqual(fake.read_screen_detail("screen_t1")["confidence"], level)
        with mock.patch.dict(os.environ, {"FAKE_CONFIDENCE": "sure"}):
            with self.assertRaises(ValueError):
                FakeHardware(**quick)

    def test_fake_low_read_is_only_the_nth(self):
        quick = {"seconds_per_press": 0.0, "seconds_per_read": 0.0}
        with mock.patch.dict(os.environ, {"FAKE_CONFIDENCE": "high", "FAKE_LOW_READ": "2"}):
            fake = FakeHardware(**quick)
        levels = []
        for control_id in ("screen_t1", "screen_t1", "screen_t1", "screen_t2"):
            levels.append(fake.read_screen_detail(control_id)["confidence"])
        self.assertEqual(levels, ["high", "low", "high", "high"])
        # on its own (no FAKE_CONFIDENCE) the other reads stay null
        with mock.patch.dict(os.environ, {"FAKE_LOW_READ": "1"}):
            os.environ.pop("FAKE_CONFIDENCE", None)
            fake = FakeHardware(**quick)
        self.assertEqual(fake.read_screen_detail("screen_t1")["confidence"], "low")
        self.assertIsNone(fake.read_screen_detail("screen_t1")["confidence"])
        for bad in ("0", "two"):
            with mock.patch.dict(os.environ, {"FAKE_LOW_READ": bad}):
                with self.assertRaises(ValueError):
                    FakeHardware(**quick)

    async def test_filming_the_pause_with_fake_low_read(self):
        # the setup for the video: FAKE_CONFIDENCE=high FAKE_LOW_READ=2 on the stock FakeHardware.
        # read 1 is before planning, read 2 checks the first H: it pauses there, resume reads high
        with mock.patch.dict(os.environ, {"FAKE_CONFIDENCE": "high", "FAKE_LOW_READ": "2"}):
            self.hardware = FakeHardware(seconds_per_press=0.01, seconds_per_read=0.01)
        api.hardware = self.hardware
        planner.call_model = timer_model(self.prompts)
        run = await self.start_job(TWO_HOURS)
        await self.wait_until(lambda: run.resume is not None)

        self.assertEqual(run.job["status"], "paused")
        self.assertIn("Couldn't read screen_t1 clearly (step 1): low confidence, read '01:00 00'",
                      run.job["pause_reason"])
        await api.resume_job(run.job["job_id"], FakeRequest({"decided_by": "Pam"}))
        await self.wait_until(lambda: self.step(run, "s1")["status"] == "succeeded")
        self.assertEqual(self.step(run, "s1")["evidence"]["screen_confidence"], "high")
        kinds = self.note_kinds(run)
        self.assertEqual(kinds.count("read_unclear"), 1)
        self.assertLess(kinds.index("read_unclear"), kinds.index("resumed"))

        await self.wait_for_card_and_score(run, "s3")
        await self.decide(run, {"decision": "approve", "approval_type": "hold"})
        await self.wait_until(lambda: api.is_finished(run), seconds=20)
        self.assertEqual(run.job["status"], "succeeded", run.job["result"])

    # ---- the mock demo fixture ----

    def test_fixture_is_the_t1_two_hour_demo(self):
        fixture = json.loads((REPO_ROOT / "shared" / "fixtures" / "timerJobSample.json").read_text(encoding="utf-8"))
        self.assertEqual(fixture["request"], TWO_HOURS)
        steps = fixture["plan"]["steps"]
        presses = []
        for step in steps:
            presses.append(step["action"]["control_id"])
            self.assertEqual(step["expect"]["control_id"], "screen_t1")
            self.assertEqual(step["action"]["hold_ms"], 1000)
        self.assertEqual(presses, ["btn_h", "btn_h", "btn_stsp"])
        # one H misses and gets retried, with the read's confidence
        retries = []
        for step in steps:
            retries.extend(step.get("retries") or [])
        self.assertEqual(len(retries), 1)
        self.assertEqual(steps[1]["retries"][0]["correction"]["control_id"], "btn_h")
        self.assertEqual(steps[1]["retries"][0]["screen_confidence"], "high")
        # ST/SP waits for hold-to-approve because it turns the countdown on (ApprovalCard's
        # needsHold), not because of its tier: the tier is what the scorer really gave, medium
        start = steps[2]
        self.assertEqual(start["status"], "awaiting_approval")
        self.assertIs(start["requires_approval"], True)
        self.assertIs(start["turns_on_output"], True)
        self.assertTrue(planner.needs_hold(start))
        self.assertNotEqual(start["risk_tier"], "high", "the hold mustn't depend on a high tier")
        self.assertEqual(start["evidence"]["screen_confidence"], "high")
        # and the plan is one the backend would accept
        raw = []
        for step in steps:
            raw.append({key: step[key] for key in ("intent", "action", "expect", "requires_approval")})
        self.assertEqual(self.validate(raw)[1], [])

    # ---- MCP and the routes ----

    def manual_run(self, raw_steps):
        """A job that's 'running' with no runner task, so a test can call press itself."""
        plan, errors = self.validate(raw_steps)
        self.assertEqual(errors, [])
        job = {
            "job_id": "job_timer_test", "created_at": api.now(), "updated_at": api.now(),
            "request": TWO_HOURS, "approval_mode": "risky_steps", "press_budget": api.DEFAULT_PRESS_BUDGET,
            "status": "running", "device": api.load_device_with_frame(DEVICE), "plan": plan, "result": None,
        }
        run = api.JobRun(job)
        run.profile = planner.load_profile(DEVICE)
        api.runs[job["job_id"]] = run
        run.active_step = plan["steps"][0]
        return run

    async def test_press_tool_holds_one_second_and_refuses_long_holds(self):
        run = self.manual_run([press_step("Add an hour", "btn_h", "01:00 00")])
        arguments = {"job_id": run.job["job_id"], "step_id": "s1", "control_id": "btn_h", "count": 1}
        async with Client(mcp_server.mcp) as client:
            # no hold_ms from the caller: the tool uses the planned one (the device's 1 s)
            result = await client.call_tool("press", arguments)
            self.assertFalse(result.is_error, tool_error_text(result))
            self.assertEqual(result.structured_content["hold_ms"], 1000)
            self.assertEqual(self.hardware.finished, [("btn_h", 1, 1000)])

            # a plan with a 2 s hold can't be made, but if one got here the tool still says no
            run.job["plan"]["steps"][0]["action"]["hold_ms"] = 2000
            result = await client.call_tool("press", dict(arguments, hold_ms=2000))
            self.assertTrue(result.is_error)
            self.assertIn("over this device's 1800 ms limit", tool_error_text(result))
            self.assertIn("nothing pressed", tool_error_text(result))
        self.assertEqual(self.hardware.started, [("btn_h", 1, 1000)], "the 2 s press never started")
        self.assertEqual(run.presses_used, 1, "and wasn't charged to the budget")

    async def test_get_controls_and_device_route(self):
        async with Client(mcp_server.mcp) as client:
            result = await client.call_tool("get_controls", {"device_id": DEVICE})
        self.assertFalse(result.is_error, tool_error_text(result))
        controls = {}
        for control in result.structured_content["controls"]:
            controls[control["control_id"]] = control
        self.assertEqual(sorted(controls), sorted(TIMER_CONTROLS))
        for control_id, control in controls.items():
            self.assertIs(control["reachable"], control_id in FINGERS + SCREENS, control_id)
        self.assertIs(controls["btn_stsp"]["risky"], True)
        self.assertIs(controls["btn_stsp"]["destructive"], False)
        self.assertIs(controls["btn_stsp"]["turns_on_output"], True)
        self.assertIs(controls["btn_h"]["turns_on_output"], False)
        self.assertIs(controls["btn_h"]["risky"], False)
        self.assertEqual(controls["btn_t2"]["bbox"], [700, 386, 82, 81])

        device = await api.get_device(DEVICE)
        self.assertEqual(device["camera"]["latest_frame_url"], "/mock/timer-01.svg")
        self.assertEqual((device["camera"]["frame_width"], device["camera"]["frame_height"]), (959, 1280))


if __name__ == "__main__":
    unittest.main()
