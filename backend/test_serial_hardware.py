"""Tests for serial_hardware.py against a fake ESP32 that follows the firmware's protocol.
No board, camera or API key needed.  Run from the repo root:
    python -m unittest backend/test_serial_hardware.py -v
"""

import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import serial_hardware  # noqa: E402
from hardware import HardwareStopped  # noqa: E402
from serial_hardware import NotCalibrated, RigError, SerialHardware  # noqa: E402

SPEED = 0.01  # the fake runs holds 100x faster than real time


class FakeESP32:
    """Speaks esp32_finger.ino's protocol. Any new line aborts a running PRESS."""

    def __init__(self, reboot_on_press=False):
        self.commands = []
        self.inbox = queue.Queue()
        self.outbox = queue.Queue()
        self.reboot_on_press = reboot_on_press
        self.outbox.put(b"READY\n")
        threading.Thread(target=self._run, daemon=True).start()

    # pyserial-like surface used by SerialHardware
    def write(self, data):
        for line in data.decode().splitlines():
            self.inbox.put(line)

    def flush(self):
        pass

    def readline(self):
        try:
            return self.outbox.get(timeout=0.05)
        except queue.Empty:
            return b""

    def close(self):
        pass

    def _say(self, text):
        self.outbox.put((text + "\n").encode())

    def _wait_or_abort(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if not self.inbox.empty():
                return True
            time.sleep(0.001)
        return False

    def _run(self):
        while True:
            line = self.inbox.get()
            self.commands.append(line)
            parts = line.split()
            name, args = parts[0], [int(x) for x in parts[1:]]
            if name == "PING":
                self._say("PONG")
            elif name == "REST":
                self._say("OK REST")
            elif name == "ANGLE":
                self._say(f"OK ANGLE {args[0]} {args[1]}")
            elif name == "CAL":
                self._say(f"OK CAL {args[0]} rest={args[1]} press={args[2]}")
            elif name == "PRESS":
                if self.reboot_on_press:
                    self._say("READY")
                    continue
                servo, count = args[0], args[1]
                hold = args[2] if len(args) > 2 else 250
                done = 0
                for _ in range(count):
                    if self._wait_or_abort(hold / 1000 * SPEED):
                        break
                    done += 1
                    if self._wait_or_abort(0.35 * SPEED):
                        break
                self._say(f"OK PRESS {servo} done={done}/{count} hold={hold}")


PER_BUTTON = {"controls": {
    "btn_set": {"servo": 0, "rest_deg": 90, "press_deg": 65, "hold_ms": 3000},
    "btn_up": {"servo": 1, "rest_deg": 90, "press_deg": 70},
    "btn_down": {"pan_deg": None, "tilt_up_deg": None, "tilt_press_deg": None},
}}
PAN_TILT = {"rig": {"pan_servo": 2, "tilt_servo": 3, "settle_ms": 10}, "controls": {
    "btn_up": {"pan_deg": 40, "tilt_up_deg": 95, "tilt_press_deg": 60},
}}


def make_rig(profile, **fake_kwargs):
    fake = FakeESP32(**fake_kwargs)
    rig = SerialHardware(serial_port=fake, device_id="test-device")
    rig.prepare_step({}, profile)
    return rig, fake


class SerialHardwareTests(unittest.TestCase):
    def test_handshake_pings(self):
        rig, fake = make_rig(PER_BUTTON)
        self.assertEqual(fake.commands[0], "PING")

    def test_press_per_button_with_hold(self):
        rig, fake = make_rig(PER_BUTTON)
        rig.press("btn_set", 1, 3500)
        self.assertEqual(fake.commands[-2:], ["CAL 0 90 65", "PRESS 0 1 3500"])

    def test_press_default_hold(self):
        rig, fake = make_rig(PER_BUTTON)
        rig.press("btn_up", 3)
        self.assertEqual(fake.commands[-1], "PRESS 1 3 250")

    def test_press_pan_tilt(self):
        rig, fake = make_rig(PAN_TILT)
        rig.press("btn_up", 2)
        self.assertEqual(fake.commands[-3:], ["ANGLE 2 40", "CAL 3 95 60", "PRESS 3 2 250"])

    def test_uncalibrated_control_says_what_to_fill_in(self):
        rig, _ = make_rig(PER_BUTTON)
        with self.assertRaises(NotCalibrated) as ctx:
            rig.press("btn_down", 1)
        self.assertIn("pan_deg", str(ctx.exception))

    def test_stop_mid_hold_is_fast_and_raises(self):
        global SPEED
        SPEED = 1.0  # real-time hold so there is something to interrupt
        try:
            rig, fake = make_rig(PER_BUTTON)
            errors = []

            def run():
                try:
                    rig.press("btn_set", 1, 3000)
                except HardwareStopped as exc:
                    errors.append(exc)

            worker = threading.Thread(target=run)
            started = time.monotonic()
            worker.start()
            time.sleep(0.3)
            rig.stop()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertLess(time.monotonic() - started, 1.5)  # not the full 3 s hold
            self.assertIn("REST", fake.commands)
        finally:
            SPEED = 0.01

    def test_next_job_works_after_stop(self):
        rig, fake = make_rig(PER_BUTTON)
        rig.stop()
        rig.press("btn_up", 1)
        self.assertEqual(fake.commands[-1], "PRESS 1 1 250")

    def test_read_screen_detail_keeps_vision_confidence(self):
        rig, fake = make_rig(PER_BUTTON)
        commands_before = list(fake.commands)
        calls = []

        # stand-in for vision/read_screen.py: no camera, no Muse Spark call
        class FakeVision:
            @staticmethod
            def read_screen(path, control_id=None, device_id=None):
                calls.append(("read_screen", path, control_id, device_id))
                return {"text": "TS 80.0", "parsed": {"TS": 80.0}, "confidence": "medium", "reads": ["TS 80.0"]}

            @staticmethod
            def read_indicator(path, control_id=None, device_id=None):
                calls.append(("read_indicator", path, control_id, device_id))
                return {"on": True, "brightness": 0.8}

        rig.save_frame = lambda path=None: Path("frame.jpg")
        saved_module = sys.modules.get("read_screen")
        sys.modules["read_screen"] = FakeVision
        try:
            self.assertEqual(rig.read_screen_detail("screen_pv"), {"text": "TS 80.0", "confidence": "medium"})
            self.assertEqual(rig.read_screen("screen_pv"), "TS 80.0")  # plain read is unchanged
            # an LED is a pixel check, no vote, so no confidence
            self.assertEqual(rig.read_screen_detail("led_heat"), {"text": "ON", "confidence": None})
        finally:
            if saved_module is None:
                sys.modules.pop("read_screen", None)
            else:
                sys.modules["read_screen"] = saved_module

        self.assertEqual(calls[0], ("read_screen", "frame.jpg", "screen_pv", "test-device"))
        self.assertEqual(calls[-1], ("read_indicator", "frame.jpg", "led_heat", "test-device"))
        self.assertEqual(fake.commands, commands_before, "reading sends nothing to the ESP32")

    def test_reboot_mid_press_explains_brownout(self):
        rig, _ = make_rig(PER_BUTTON, reboot_on_press=True)
        with self.assertRaises(RigError) as ctx:
            rig.press("btn_up", 1)
        self.assertIn("external 5V", str(ctx.exception))


class FakeWebcam:
    """cv2.VideoCapture stand-in. Remembers if two grabs ever overlapped."""

    def __init__(self):
        self.frames_read = 0
        self.inside = 0
        self.overlapped = False
        self._count_lock = threading.Lock()

    def read(self):
        with self._count_lock:
            self.inside += 1
            if self.inside > 1:
                self.overlapped = True
        time.sleep(0.002)  # a real read takes a moment, give an overlap the chance to happen
        with self._count_lock:
            self.inside -= 1
            self.frames_read += 1
        return True, "frame"

    def release(self):
        pass


class FakeCv2:
    """Just enough of cv2 for save_frame: imwrite writes a few bytes."""

    @staticmethod
    def imwrite(path, frame):
        Path(path).write_bytes(b"not really a jpeg")
        return True


class CameraLoop:
    """What api.py's live frame loop does, on its own thread: save_frame over and over."""

    def __init__(self, rig, every=0.05):
        self.rig = rig
        self.every = every
        self.saved_at = []
        self.errors = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.rig.save_frame()
                self.saved_at.append(time.monotonic())
            except Exception as exc:
                self.errors.append(exc)
            self._stop.wait(self.every)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self._stop.set()
        self._thread.join(timeout=5)

    def saves_between(self, start, end):
        count = 0
        for moment in self.saved_at:
            if start <= moment <= end:
                count += 1
        return count


class CameraWithPressTests(unittest.TestCase):
    """The webcam and the ESP32 are separate: frames keep coming during a press."""

    def setUp(self):
        global SPEED
        SPEED = 1.0  # real-time holds, so a 3 s hold really takes 3 s
        self.folder = tempfile.TemporaryDirectory()
        self.saved_frames_dir = serial_hardware.FRAMES_DIR
        serial_hardware.FRAMES_DIR = Path(self.folder.name)
        self.saved_cv2 = sys.modules.get("cv2")
        sys.modules["cv2"] = FakeCv2
        self.rig, self.fake = make_rig(PER_BUTTON)
        self.webcam = FakeWebcam()
        self.rig._camera = self.webcam  # already "open", so _grab never calls VideoCapture

    def tearDown(self):
        global SPEED
        SPEED = 0.01
        serial_hardware.FRAMES_DIR = self.saved_frames_dir
        if self.saved_cv2 is None:
            sys.modules.pop("cv2", None)
        else:
            sys.modules["cv2"] = self.saved_cv2
        self.folder.cleanup()

    def test_frames_keep_coming_during_a_3s_press(self):
        with CameraLoop(self.rig) as loop:
            time.sleep(0.2)
            started = time.monotonic()
            self.rig.press("btn_set", 1, 3000)
            ended = time.monotonic()
        self.assertGreater(ended - started, 2.9, "the hold should really take 3 s here")
        during = loop.saves_between(started, ended)
        # every 0.05 s for 3 s is ~50 saves. a loop stuck behind the press would get 0 or 1
        self.assertGreaterEqual(during, 20, f"only {during} frames saved during the hold")
        self.assertEqual(loop.errors, [])
        self.assertEqual(self.fake.commands[-2:], ["CAL 0 90 65", "PRESS 0 1 3000"])
        for command in self.fake.commands:
            self.assertFalse(command.startswith(("FRAME", "CAM")), "the camera never talks to the ESP32")

    def test_stop_mid_hold_is_fast_with_the_camera_running(self):
        errors = []

        def hold():
            try:
                self.rig.press("btn_set", 1, 3000)
            except HardwareStopped as exc:
                errors.append(exc)

        with CameraLoop(self.rig) as loop:
            worker = threading.Thread(target=hold)
            worker.start()
            time.sleep(0.3)
            stop_started = time.monotonic()
            self.rig.stop()
            stop_took = time.monotonic() - stop_started
            worker.join(timeout=5)
            saves_after_stop = len(loop.saved_at)
            time.sleep(0.2)
            self.assertGreater(len(loop.saved_at), saves_after_stop, "frames keep coming after a stop")

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertLess(stop_took, 0.2, "stop() mustn't wait on the camera")
        self.assertLess(time.monotonic() - stop_started, 1.5)
        self.assertIn("REST", self.fake.commands)

    def test_live_loop_and_read_screen_never_grab_at_once(self):
        class FakeVision:
            @staticmethod
            def read_screen(path, control_id=None, device_id=None):
                return {"text": "TS 80.0", "confidence": "high"}

            @staticmethod
            def read_indicator(path, control_id=None, device_id=None):
                return {"on": False}

        saved_vision = sys.modules.get("read_screen")
        sys.modules["read_screen"] = FakeVision
        reads = []
        try:
            with CameraLoop(self.rig, every=0.0) as loop:  # as fast as it can go
                for _ in range(30):
                    reads.append(self.rig.read_screen_detail("screen_pv"))
                    reads.append(self.rig.read_screen_detail("led_heat"))
        finally:
            if saved_vision is None:
                sys.modules.pop("read_screen", None)
            else:
                sys.modules["read_screen"] = saved_vision

        self.assertEqual(len(reads), 60)
        self.assertGreater(len(loop.saved_at), 5, "the loop ran alongside the reads")
        self.assertFalse(self.webcam.overlapped, "two grabs hit the webcam at the same time")
        self.assertEqual(loop.errors, [])
        # read_screen grabs into its own file, the loop keeps -latest.jpg
        self.assertTrue((serial_hardware.FRAMES_DIR / "test-device-read.jpg").is_file())
        self.assertTrue((serial_hardware.FRAMES_DIR / "test-device-latest.jpg").is_file())


if __name__ == "__main__":
    unittest.main()
