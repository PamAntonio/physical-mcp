"""The real rig: an ESP32 servo finger on USB serial plus a USB webcam.

Implements the Hardware interface in backend/hardware.py, so api.py can swap it in for
FakeHardware. Start the backend with HARDWARE=serial to use it (see api.py).

Firmware: firmware/esp32_finger/esp32_finger.ino. It speaks one line per command:
    PING -> PONG
    ANGLE <servo> <deg>
    CAL <servo> <rest_deg> <press_deg>
    PRESS <servo> <count> <hold_ms>  -> OK PRESS <servo> done=<n>/<count> hold=<ms>
    REST                             (any new line also aborts a running PRESS)

Where each button is comes from the device profile (backend/devices/<device_id>.json).
Two rig layouts are supported, per control:
    one servo per button: {"servo": 0, "rest_deg": 90, "press_deg": 65}
    one finger on pan/tilt: {"pan_deg": 40, "tilt_up_deg": 90, "tilt_press_deg": 65}
        with the profile's "rig": {"pan_servo": 0, "tilt_servo": 1, "settle_ms": 500}
        (those are the defaults if "rig" is missing)

Camera frames are written to web/public/frames/<device_id>-latest.jpg so the Next.js
dev server can show them, and read_screen() hands the same frame to vision/read_screen.py.

Env:
    SERIAL_PORT     e.g. /dev/cu.usbserial-0001 (default: auto-detect the ESP32)
    CAMERA_INDEX    OpenCV camera index (default 0)
    CAMERA_WIDTH    default 2560, CAMERA_HEIGHT default 1440 (the vision calibration size)
    DEVICE_ID       profile to load before the first step (default inkbird-01)

Bench test without the backend (from the repo root):
    python backend/serial_hardware.py ping
    python backend/serial_hardware.py angle 0 70
    python backend/serial_hardware.py press btn_set --hold 3500
    python backend/serial_hardware.py frame
    python backend/serial_hardware.py read screen_pv
"""

import argparse
import json
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

from hardware import Hardware, HardwareStopped

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
DEVICES_DIR = BACKEND_DIR / "devices"
FRAMES_DIR = REPO_ROOT / "web" / "public" / "frames"
VISION_DIR = REPO_ROOT / "vision"

BAUD = 115200
RELEASE_MS = 350          # matches RELEASE_MS in the firmware
REPLY_SLACK_S = 3.0       # extra time allowed on top of the expected press time
BOOT_WAIT_S = 4.0         # opening the port resets most ESP32 boards; wait for READY
PORT_PATTERNS = ("usbserial", "SLAB_USBtoUART", "wchusbserial", "usbmodem", "ttyUSB", "ttyACM")
PRESS_REPLY = re.compile(r"^OK PRESS (\d+) done=(\d+)/(\d+)")


class NotCalibrated(Exception):
    """A control has no servo position in the device profile yet."""


class RigError(Exception):
    """The ESP32 answered with an error, rebooted, or stopped answering."""


def find_port():
    from serial.tools import list_ports  # pyserial

    for port in list_ports.comports():
        if any(p in port.device for p in PORT_PATTERNS):
            return port.device
    found = ", ".join(p.device for p in list_ports.comports()) or "none"
    raise RigError(f"No ESP32 serial port found (ports seen: {found}). Set SERIAL_PORT.")


def load_profile(device_id):
    path = DEVICES_DIR / f"{device_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class SerialHardware(Hardware):
    def __init__(self, port=None, device_id="inkbird-01", camera_index=0,
                 camera_size=(2560, 1440), serial_port=None):
        """serial_port: an already-open port object (tests pass a fake). Otherwise `port`
        (or auto-detect) is opened with pyserial."""
        self.device_id = device_id
        self.profile = load_profile(device_id)
        self.camera_index = camera_index
        self.camera_size = camera_size
        self._camera = None
        self._camera_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._lines = queue.Queue()
        self._stop_flag = threading.Event()
        self._closed = False

        if serial_port is not None:
            self._port = serial_port
        else:
            import serial  # pyserial

            self._port = serial.Serial(port or find_port(), BAUD, timeout=0.1)
        self._reader = threading.Thread(target=self._read_loop, name="esp32-reader", daemon=True)
        self._reader.start()
        self._handshake()

    @classmethod
    def from_env(cls):
        width = int(os.environ.get("CAMERA_WIDTH", "2560"))
        height = int(os.environ.get("CAMERA_HEIGHT", "1440"))
        return cls(
            port=os.environ.get("SERIAL_PORT") or None,
            device_id=os.environ.get("DEVICE_ID", "inkbird-01"),
            camera_index=int(os.environ.get("CAMERA_INDEX", "0")),
            camera_size=(width, height),
        )

    # ---- serial plumbing ----

    def _read_loop(self):
        while not self._closed:
            try:
                raw = self._port.readline()
            except Exception as exc:  # port unplugged
                self._lines.put(f"__PORT_ERROR__ {exc}")
                return
            if raw:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    print(f"[esp32] {line}", flush=True)
                    self._lines.put(line)

    def _send(self, command):
        with self._write_lock:
            self._port.write((command + "\n").encode("ascii"))
            if hasattr(self._port, "flush"):
                self._port.flush()

    def _wait_for(self, prefix, timeout_s, stop_flag=None):
        """Next line starting with prefix. Raises RigError on ERR, a reboot, or timeout.
        If stop_flag gets set while waiting, keeps listening briefly for the reply (the
        firmware answers right after it aborts) and then returns whatever it got."""
        deadline = time.monotonic() + timeout_s
        while True:
            if stop_flag is not None and stop_flag.is_set():
                deadline = min(deadline, time.monotonic() + 1.0)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if stop_flag is not None and stop_flag.is_set():
                    return None
                raise RigError(f"ESP32 did not answer {prefix!r} in time")
            try:
                line = self._lines.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if line.startswith(prefix):
                return line
            if line.startswith("ERR"):
                raise RigError(f"ESP32: {line}")
            if line == "READY":
                raise RigError("ESP32 rebooted mid-command (it printed READY again). Usually "
                               "the servo browned out the board: power it from an external 5V "
                               "supply with ground shared.")
            if line.startswith("__PORT_ERROR__"):
                raise RigError(f"Serial port failed: {line[15:]}")
            # anything else (OK REST after a stop, stray output) is just logged by the reader

    def _handshake(self):
        # opening the port usually resets the board: give it a moment to print READY
        try:
            self._wait_for("READY", BOOT_WAIT_S)
        except RigError:
            pass  # board didn't reset (or READY was missed); PING decides
        self._drain()
        self._send("PING")
        self._wait_for("PONG", 2.0)

    def _drain(self):
        while True:
            try:
                self._lines.get_nowait()
            except queue.Empty:
                return

    # ---- where a control is ----

    def prepare_step(self, step, profile):
        if profile:
            self.profile = profile

    def _placement(self, control_id):
        entry = (self.profile.get("controls") or {}).get(control_id)
        where = f"backend/devices/{self.device_id}.json"
        if entry is None:
            raise NotCalibrated(f"{control_id!r} is not in {where}")
        if _num(entry.get("servo")):
            rest, press = entry.get("rest_deg"), entry.get("press_deg")
            if _num(rest) and _num(press):
                return {"servo": int(entry["servo"]), "rest": int(rest), "press": int(press)}
            raise NotCalibrated(f"{control_id!r} has a servo but no rest_deg/press_deg in {where}")
        pan, up, down = entry.get("pan_deg"), entry.get("tilt_up_deg"), entry.get("tilt_press_deg")
        if _num(pan) and _num(up) and _num(down):
            rig = self.profile.get("rig") or {}
            return {
                "pan_servo": int(rig.get("pan_servo", 0)),
                "pan": int(pan),
                "settle_ms": int(rig.get("settle_ms", 500)),
                "servo": int(rig.get("tilt_servo", 1)),
                "rest": int(up),
                "press": int(down),
            }
        raise NotCalibrated(
            f"{control_id!r} has no servo position yet. In {where} set either "
            f'"servo" + "rest_deg" + "press_deg", or "pan_deg" + "tilt_up_deg" + "tilt_press_deg".')

    # ---- Hardware interface ----

    def press(self, control_id, count, hold_ms=None):
        stop_flag = self._stop_flag  # stop() swaps in a fresh flag for the next job
        if stop_flag.is_set():
            raise HardwareStopped(f"stopped before pressing {control_id}")
        place = self._placement(control_id)
        count = max(1, min(int(count), 30))  # firmware clamps to 1..30 too
        hold = int(hold_ms) if hold_ms else 250

        if "pan" in place:
            self._send(f"ANGLE {place['pan_servo']} {place['pan']}")
            self._wait_for("OK ANGLE", 2.0)
            if stop_flag.wait(place["settle_ms"] / 1000):
                raise HardwareStopped(f"stopped while moving to {control_id}")

        self._send(f"CAL {place['servo']} {place['rest']} {place['press']}")
        self._wait_for("OK CAL", 2.0)

        expected_s = count * (hold + RELEASE_MS) / 1000
        self._send(f"PRESS {place['servo']} {count} {hold}")
        reply = self._wait_for("OK PRESS", expected_s + REPLY_SLACK_S, stop_flag=stop_flag)
        if stop_flag.is_set():
            raise HardwareStopped(f"stopped while pressing {control_id}")
        match = PRESS_REPLY.match(reply or "")
        if not match or int(match.group(2)) != count:
            raise RigError(f"press {control_id} x{count} incomplete: {reply!r}")

    def stop(self):
        flag = self._stop_flag
        self._stop_flag = threading.Event()  # the next job starts clean
        flag.set()
        try:
            self._send("REST")  # the firmware aborts a running PRESS on any new line
        except Exception as exc:
            print(f"[serial hardware] REST failed: {exc}", file=sys.stderr, flush=True)

    # ---- camera ----

    def _grab(self):
        """A fresh frame as an OpenCV image."""
        import cv2  # opencv-python

        with self._camera_lock:
            if self._camera is None:
                cam = cv2.VideoCapture(self.camera_index)
                cam.set(cv2.CAP_PROP_FRAME_WIDTH, self.camera_size[0])
                cam.set(cv2.CAP_PROP_FRAME_HEIGHT, self.camera_size[1])
                if not cam.isOpened():
                    raise RigError(f"Can't open camera {self.camera_index}. On a Mac, allow "
                                   "camera access for your terminal app in System Settings.")
                self._camera = cam
            frame = None
            for _ in range(5):  # skip buffered frames so we see the panel as it is now
                ok, img = self._camera.read()
                if ok:
                    frame = img
            if frame is None:
                raise RigError("Camera returned no frame")
            return frame

    def save_frame(self, path=None):
        import cv2

        FRAMES_DIR.mkdir(parents=True, exist_ok=True)
        path = Path(path) if path else FRAMES_DIR / f"{self.device_id}-latest.jpg"
        frame = self._grab()
        tmp = path.with_name(path.stem + ".tmp.jpg")
        cv2.imwrite(str(tmp), frame)
        os.replace(tmp, path)  # the web app never sees a half-written file
        return path

    def latest_frame_url(self, device_id):
        try:
            path = self.save_frame()
        except Exception as exc:
            print(f"[serial hardware] no frame: {exc}", file=sys.stderr, flush=True)
            return None
        return f"/frames/{path.name}?t={int(time.time() * 1000)}"

    def read_screen(self, control_id):
        if str(VISION_DIR) not in sys.path:
            sys.path.insert(0, str(VISION_DIR))
        import read_screen as vision  # Bhavisha's vision/read_screen.py

        path = self.save_frame()
        if control_id.startswith("led_"):
            result = vision.read_indicator(str(path), control_id=control_id, device_id=self.device_id)
            return "ON" if result["on"] else "OFF"
        return vision.read_screen(str(path), control_id=control_id, device_id=self.device_id)["text"]

    def close(self):
        self._closed = True
        with self._camera_lock:
            if self._camera is not None:
                self._camera.release()
        self._port.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bench-test the ESP32 finger and webcam.")
    parser.add_argument("--port", help="serial port (default: SERIAL_PORT or auto-detect)")
    parser.add_argument("--device", default=os.environ.get("DEVICE_ID", "inkbird-01"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    angle = sub.add_parser("angle", help="move one servo, for finding press angles")
    angle.add_argument("servo", type=int)
    angle.add_argument("deg", type=int)
    press = sub.add_parser("press", help="press a control using the profile's angles")
    press.add_argument("control_id")
    press.add_argument("--count", type=int, default=1)
    press.add_argument("--hold", type=int, default=None, help="hold ms, e.g. 3500 for SET")
    sub.add_parser("rest")
    frame = sub.add_parser("frame", help="save one webcam frame")
    frame.add_argument("--out", default=None)
    read = sub.add_parser("read", help="frame + vision read of one control")
    read.add_argument("control_id")
    args = parser.parse_args(argv)

    if args.cmd in ("frame", "read"):
        rig = SerialHardware.__new__(SerialHardware)  # camera only, no serial needed
        rig.device_id, rig.profile = args.device, load_profile(args.device)
        rig.camera_index = int(os.environ.get("CAMERA_INDEX", "0"))
        rig.camera_size = (int(os.environ.get("CAMERA_WIDTH", "2560")),
                           int(os.environ.get("CAMERA_HEIGHT", "1440")))
        rig._camera, rig._camera_lock = None, threading.Lock()
        print(rig.save_frame(args.out) if args.cmd == "frame" else rig.read_screen(args.control_id))
        return 0

    rig = SerialHardware(port=args.port or os.environ.get("SERIAL_PORT"), device_id=args.device)
    try:
        if args.cmd == "ping":
            print("PONG: the ESP32 is answering")
        elif args.cmd == "angle":
            rig._send(f"ANGLE {args.servo} {args.deg}")
            print(rig._wait_for("OK ANGLE", 2.0))
        elif args.cmd == "press":
            rig.press(args.control_id, args.count, args.hold)
            print("pressed")
        elif args.cmd == "rest":
            rig.stop()
            print("resting")
    finally:
        rig._closed = True
        rig._port.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
