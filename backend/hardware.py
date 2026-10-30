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
    kicks in. Only once per server run, so the demo shows exactly one self-correction."""

    UP_DOWN = {"btn_up": 1, "btn_down": -1}  # which way each one moves the number

    def __init__(self, seconds_per_press=0.2, seconds_per_read=0.3, undershoot=None):
        self.seconds_per_press = seconds_per_press
        self.seconds_per_read = seconds_per_read
        if undershoot is None:
            undershoot = os.environ.get("FAKE_UNDERSHOOT") == "1"
        self.undershoot = undershoot
        self.undershoot_done = False
        self.stop_requested = threading.Event()
        self.next_reads = {}  # control_id -> what the next read should say
        self.last_reads = {}  # control_id -> what it said last time
        self.expected_read = None  # (control_id, text) the current step is after
        self.step_size = None

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

    def stop(self):
        print("[fake hardware] stop, finger retracted", flush=True)
        self.stop_requested.set()
        self.stop_requested = threading.Event()
