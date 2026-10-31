"""The rig the job runner talks to: a finger that presses and a camera that reads.

backend/api.py only ever calls these methods, so swapping the fake for the real servo +
camera means writing one class with press / read_screen / stop and changing one line in
api.py (the `hardware = FakeHardware()` line).

Threading, since the real thing will block:
- press() and read_screen() run on a worker thread, one call at a time (one finger).
- stop() gets called from a different thread, possibly while press() is mid-move. It has
  to be quick and safe to call at any time, and should make a running press() give up
  (raise HardwareStopped) as soon as it can.
"""

import json
import os
import threading
import time

from screen_numbers import find_number, replace_number


class HardwareStopped(Exception):
    """press() raises this when stop() cut it short."""


class Hardware:
    def press(self, control_id, count, hold_ms=None):
        """Press control_id `count` times, holding each press for hold_ms if given."""
        raise NotImplementedError

    def read_screen(self, control_id):
        """What the camera reads on control_id right now, as text.
        For an indicator light, return "ON" or "OFF"."""
        raise NotImplementedError

    def read_screen_detail(self, control_id):
        """read_screen plus how sure the read is: {"text", "confidence"}. confidence is the
        vision vote ("high" / "medium" / "low"), or None when this hardware doesn't report one.
        The MCP read_screen tool calls this. Override it to pass the vision confidence through."""
        return {"text": self.read_screen(control_id), "confidence": None}

    def stop(self):
        """Retract the finger now. Called from another thread, see the note at the top."""
        raise NotImplementedError

    def prepare_job(self, profile):
        """Heads up that a job is starting on this device (backend/devices/<id>.json), before the
        screens are read for planning. Real hardware ignores it, FakeHardware uses the profile's
        fake_screens so its first read shows real screen text."""
        pass

    def prepare_step(self, step, profile):
        """Heads up about the step that's about to run, plus the device profile
        (backend/devices/<id>.json, with each control's pan/tilt numbers and step_size).
        Real hardware can ignore it or use the profile. FakeHardware uses it to know what
        the screen is supposed to say afterwards."""
        pass

    def latest_frame_url(self, device_id):
        """URL (or path on this backend) of the newest camera frame, or None with no camera.
        While it's None, api.py falls back to the web app's placeholder still for the device.
        The real camera class should override this."""
        return None


class FakeHardware(Hardware):
    """Pretends to press and reads back what the step expected.
    Good for running the whole flow with no rig attached.

    undershoot (or FAKE_UNDERSHOOT=1 in the env): the first time an up/down step runs, one
    press "doesn't register", so the screen reads one step short and the backend's retry
    kicks in. Only once per server run, so the demo shows exactly one self-correction.

    miss_press (or FAKE_MISS_PRESS=btn_h in the env): the second press of that control "doesn't
    register" (the second, so there's a verified screen for it to stay on), and the screen keeps
    showing what it did before. That's the timer's missed H, and the backend's retry_if_unchanged presses it again.
    Only once per server run.

    Read confidence, opt-in, for filming the confidence UI with no rig. Default: None on every
    read, the fake has no vision vote.
    - confidence (or FAKE_CONFIDENCE=high|medium|low): every read reports that level.
    - low_read (or FAKE_LOW_READ=N): only the Nth read reports "low", so the backend pauses on
      it. Reads are counted from when the backend started, 1 = the first one (the read before
      the first job's plan, on the timer). Together with FAKE_CONFIDENCE=high the resume's
      re-read then comes back high."""

    UP_DOWN = {"btn_up": 1, "btn_down": -1}  # which way each one moves the number
    CONFIDENCE_LEVELS = ("high", "medium", "low")

    def __init__(self, seconds_per_press=0.2, seconds_per_read=0.3, undershoot=None, miss_press=None,
                 confidence=None, low_read=None):
        self.seconds_per_press = seconds_per_press
        self.seconds_per_read = seconds_per_read
        if undershoot is None:
            undershoot = os.environ.get("FAKE_UNDERSHOOT") == "1"
        self.undershoot = undershoot
        self.undershoot_done = False
        if miss_press is None:
            miss_press = os.environ.get("FAKE_MISS_PRESS") or None
        self.miss_press = miss_press
        self.miss_press_seen = 0  # presses of miss_press so far
        self.miss_press_done = False
        if confidence is None:
            confidence = os.environ.get("FAKE_CONFIDENCE") or None
        if confidence is not None and confidence not in self.CONFIDENCE_LEVELS:
            raise ValueError(f"FAKE_CONFIDENCE should be high, medium or low, not {confidence!r}")
        self.confidence = confidence
        if low_read is None and os.environ.get("FAKE_LOW_READ"):
            try:
                low_read = int(os.environ["FAKE_LOW_READ"])
            except ValueError:
                raise ValueError(f"FAKE_LOW_READ should be a read number like 3, not {os.environ['FAKE_LOW_READ']!r}")
        if low_read is not None and low_read < 1:
            raise ValueError(f"FAKE_LOW_READ counts from 1, got {low_read}")
        self.low_read = low_read
        self.reads_done = 0  # reads through read_screen_detail (what the MCP tool calls) so far
        self.stop_requested = threading.Event()
        self.next_reads = {}  # control_id -> what the next read should say
        self.last_reads = {}  # control_id -> what it said last time
        self.expected_read = None  # (control_id, text) the current step is after
        self.step_size = None

    def prepare_job(self, profile):
        # what the screens show before anything's pressed (timer-01: every row 00:00 00), so the
        # read before planning returns real text. only screens we haven't shown yet: after a
        # job the screen keeps what that job left on it, like the real timer
        fake_screens = dict((profile or {}).get("fake_screens") or {})
        # FAKE_SCREENS='{"screen_t1": "01:59 00"}' starts a screen somewhere else, e.g. to film
        # the planner refusing a target it can't reach. same rule: never over what's on screen
        if os.environ.get("FAKE_SCREENS"):
            try:
                override = json.loads(os.environ["FAKE_SCREENS"])
            except ValueError:
                raise ValueError(f"FAKE_SCREENS should be JSON like {{\"screen_t1\": \"01:59 00\"}}, not {os.environ['FAKE_SCREENS']!r}")
            if not isinstance(override, dict):
                raise ValueError("FAKE_SCREENS should be a JSON object of control_id -> screen text")
            fake_screens.update(override)
        for control_id, text in fake_screens.items():
            if control_id not in self.last_reads and control_id not in self.next_reads:
                self.last_reads[control_id] = text

    def prepare_step(self, step, profile):
        self.step_size = (profile or {}).get("step_size")
        self.expected_read = None
        expect = step.get("expect") or {}
        control_id = expect.get("control_id")
        if not control_id:
            return
        if expect.get("indicator_on") is not None:
            text = "ON" if expect["indicator_on"] else "OFF"
        else:
            text = expect.get("text_contains") or ""
        self.expected_read = (control_id, text)
        self.next_reads[control_id] = text

    def press(self, control_id, count, hold_ms=None):
        # grab this press's flag now, stop() swaps in a fresh one for the next job
        stop_flag = self.stop_requested
        seconds_per_push = self.seconds_per_press + (hold_ms or 0) / 1000
        seconds = 0.3 + count * seconds_per_push
        print(f"[fake hardware] press {control_id} x{count}" + (f" hold {hold_ms} ms" if hold_ms else ""), flush=True)
        # wait() returns True as soon as stop() sets the flag
        if stop_flag.wait(seconds):
            raise HardwareStopped(f"stopped while pressing {control_id}")

        if not self.expected_read:
            return
        screen_id, wanted = self.expected_read
        if control_id == self.miss_press and not self.miss_press_done:
            self.miss_press_seen += 1
            if self.miss_press_seen == 2:  # the second one, so there's a screen to stay on
                self.miss_press_done = True
                self.next_reads.pop(screen_id, None)  # the screen doesn't change
                print(f"[fake hardware] {control_id} press didn't register, screen stays", flush=True)
                return
        short_text = None
        if self.undershoot and not self.undershoot_done and control_id in self.UP_DOWN and self.step_size:
            # one press goes missing, so the number lands a step short of the target
            found = find_number(wanted)
            if found is not None:
                short_value = found[0] - self.UP_DOWN[control_id] * self.step_size
                short_text = replace_number(wanted, short_value)
        if short_text is not None:
            self.undershoot_done = True
            self.next_reads[screen_id] = short_text
            print(f"[fake hardware] dropped one {control_id} press, screen will say {short_text!r}", flush=True)
        else:
            # any other press (including a correction) lands where the step wanted
            self.next_reads[screen_id] = wanted

    def read_screen(self, control_id):
        time.sleep(self.seconds_per_read)  # pretend the camera + vision call takes a moment
        if control_id in self.next_reads:
            text = self.next_reads.pop(control_id)
        else:
            text = self.last_reads.get(control_id, "")
        self.last_reads[control_id] = text
        print(f"[fake hardware] read {control_id}: {text!r}", flush=True)
        return text

    def read_screen_detail(self, control_id):
        text = self.read_screen(control_id)
        self.reads_done += 1
        confidence = self.confidence
        if self.low_read is not None and self.reads_done == self.low_read:
            confidence = "low"
            print(f"[fake hardware] read {self.reads_done} reports low confidence (FAKE_LOW_READ)", flush=True)
        return {"text": text, "confidence": confidence}

    def stop(self):
        print("[fake hardware] stop, finger retracted", flush=True)
        self.stop_requested.set()
        self.stop_requested = threading.Event()
