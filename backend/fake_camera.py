#!/usr/bin/env python3
"""Try the live camera view with no rig: the backend on FakeHardware, plus a fake webcam.

    python backend/fake_camera.py              # http://127.0.0.1:8000, same as api.py
    python backend/fake_camera.py --port 9000

Every 0.5 s it draws a test frame into web/public/frames/<device>-latest.jpg, the same file
serial_hardware.py writes on the real rig: a frame counter and clock (so you can see it's
live), a dot that moves, and an outline on every control's bbox (so you can check the web
app's boxes land on them). Presses and reads are FakeHardware, like api.py with no rig.
Needs Pillow (pip install -r backend/requirements-hardware.txt).
"""

import argparse
import math
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent))

import api  # noqa: E402
import planner  # noqa: E402
from hardware import FakeHardware  # noqa: E402

FRAMES_DIR = api.REPO_ROOT / "web" / "public" / "frames"


class FakeCameraHardware(FakeHardware):
    def __init__(self, device_id):
        super().__init__()
        self.device_id = device_id
        self.device = planner.load_device(device_id)
        self.frames_drawn = 0
        # like SerialHardware: the camera thread and GET /devices can both save a frame
        self.camera_lock = threading.RLock()

    def save_frame(self, path=None):
        with self.camera_lock:
            return self.draw_frame(path)

    def draw_frame(self, path=None):
        from PIL import Image, ImageDraw, ImageFont

        width = self.device["camera"]["frame_width"]
        height = self.device["camera"]["frame_height"]
        image = Image.new("RGB", (width, height), (24, 27, 30))
        draw = ImageDraw.Draw(image)
        big = ImageFont.load_default(size=height // 12)
        small = ImageFont.load_default(size=height // 40)

        # every control's box, where the web app draws its overlay
        for control in self.device["controls"]:
            x, y, w, h = control["bbox"]
            draw.rectangle([x, y, x + w - 1, y + h - 1], outline=(109, 255, 158), width=max(2, width // 600))
            draw.text((x, y + h + 6), control["control_id"], fill=(109, 255, 158), font=small)

        # proof it's live: a counter, the time, and a dot going round
        self.frames_drawn += 1
        stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 10) % 10}"
        draw.text((width // 30, height // 30), f"FAKE CAMERA  frame {self.frames_drawn}  {stamp}", fill=(230, 230, 230), font=big)
        angle = self.frames_drawn * 0.4
        center_x = width * 0.85 + math.cos(angle) * height * 0.08
        center_y = height * 0.8 + math.sin(angle) * height * 0.08
        radius = height // 40
        draw.ellipse([center_x - radius, center_y - radius, center_x + radius, center_y + radius], fill=(92, 200, 255))

        # same as serial_hardware.py: write next to it, then swap, so the app never sees half a file
        FRAMES_DIR.mkdir(parents=True, exist_ok=True)
        path = Path(path) if path else FRAMES_DIR / f"{self.device_id}-latest.jpg"
        temporary = path.with_name(path.stem + ".tmp.jpg")
        image.save(temporary, quality=85)
        os.replace(temporary, path)
        return path

    def latest_frame_url(self, device_id):
        try:
            path = self.save_frame()
        except Exception as err:
            print(f"[fake camera] no frame: {err}", file=sys.stderr, flush=True)
            return None
        return f"/frames/{path.name}?t={int(time.time() * 1000)}"


def main():
    parser = argparse.ArgumentParser(description="Backend on FakeHardware with a fake webcam")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--device", default="inkbird-01")
    args = parser.parse_args()

    api.hardware = FakeCameraHardware(args.device)
    api.LIVE_FRAMES = True  # turn on the frame loop, like HARDWARE=serial does
    uvicorn.run(api.app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
