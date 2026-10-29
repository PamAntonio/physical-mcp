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

import threading
import time


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

    def stop(self):
        """Retract the finger now. Called from another thread, see the note at the top."""
        raise NotImplementedError

    def prepare_step(self, step):
        """Heads up about the step that's about to run. Real hardware can ignore this,
        FakeHardware uses it to know what the screen is supposed to say afterwards."""
        pass

    def latest_frame_url(self, device_id):
        """URL (or path on this backend) of the newest camera frame, or None with no camera.
        While it's None, api.py falls back to the web app's placeholder still for the device.
        The real camera class should override this."""
        return None


class FakeHardware(Hardware):
    """Pretends to press and always reads back exactly what the step expected.
    Good for running the whole flow with no rig attached."""

    def __init__(self, seconds_per_press=0.2, seconds_per_read=0.3):
        self.seconds_per_press = seconds_per_press
        self.seconds_per_read = seconds_per_read
        self.stop_requested = threading.Event()
        self.next_reads = {}  # control_id -> what the next read should say
        self.last_reads = {}  # control_id -> what it said last time

    def prepare_step(self, step):
        expect = step.get("expect") or {}
        control_id = expect.get("control_id")
        if not control_id:
            return
        if expect.get("indicator_on") is not None:
            self.next_reads[control_id] = "ON" if expect["indicator_on"] else "OFF"
        else:
            self.next_reads[control_id] = expect.get("text_contains") or ""

    def press(self, control_id, count, hold_ms=None):
        # grab this press's flag now, stop() swaps in a fresh one for the next job
        stop_flag = self.stop_requested
        seconds_per_push = self.seconds_per_press + (hold_ms or 0) / 1000
        seconds = 0.3 + count * seconds_per_push
        print(f"[fake hardware] press {control_id} x{count}" + (f" hold {hold_ms} ms" if hold_ms else ""), flush=True)
        # wait() returns True as soon as stop() sets the flag
        if stop_flag.wait(seconds):
            raise HardwareStopped(f"stopped while pressing {control_id}")

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
