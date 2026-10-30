#!/usr/bin/env python3
"""Move a device's control boxes to a new camera view by clicking the panel's 4 corners.

    python backend/calibrate.py --reference <frame the current boxes were made on> <device_id>
    python backend/calibrate.py <new camera frame> <device_id>

Both open http://localhost:3000/calibrate (the Next.js app has to be running: cd web, npm run
dev). Click the control panel's corners in order: top-left, top-right, bottom-right, bottom-left.
The boxes show up on the frame right away; Redo if they're off, Save when they're right.

How it works: the reference layout is the current boxes in vision/calibration/<device>.boxes.json
plus where the panel's corners are on the frame those boxes were made on. --reference saves that
once, to <device>.reference.json. After that, every new frame maps the REFERENCE boxes through a
perspective transform (homography) from the reference corners to the corners you clicked, so
calibrating again never builds on the last calibration's error. Saving backs the old boxes file up
to <device>.boxes.<time>.bak.json first, and keeps everything else in it (LED baselines and such).
The web app draws the new boxes too: api.py reads the same boxes file.

    --corners "x,y x,y x,y x,y"   skip the page, use these corners (frame pixels, same order)
    --port 8765                   where this script's little API listens for the page
    --no-browser                  don't open the page, just print its address

Only Pillow + numpy, no OpenCV.
"""

import argparse
import json
import math
import mimetypes
import re
import shutil
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
CALIBRATION_DIR = REPO_ROOT / "vision" / "calibration"
CORNER_NAMES = ["top-left", "top-right", "bottom-right", "bottom-left"]

sys.path.insert(0, str(REPO_ROOT / "vision"))
from read_screen import pretty_json  # noqa: E402  same file format as read_screen.py --calibrate


# ---------------------------------------------------------------------------
# the math
# ---------------------------------------------------------------------------

def homography(source_points, target_points):
    """3x3 matrix H mapping each of the 4 source points onto its target point (perspective
    transform). For each pair (x, y) -> (u, v), with H's last entry fixed to 1:
        u = (h0 x + h1 y + h2) / (h6 x + h7 y + 1)
        v = (h3 x + h4 y + h5) / (h6 x + h7 y + 1)
    Multiplying out gives two linear equations per pair, so 4 pairs = 8 equations, 8 unknowns."""
    if len(source_points) != 4 or len(target_points) != 4:
        raise ValueError("need exactly 4 source and 4 target points")
    rows = []
    values = []
    for index in range(4):
        x, y = source_points[index]
        u, v = target_points[index]
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        values.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        values.append(v)
    try:
        h = np.linalg.solve(np.array(rows, dtype=float), np.array(values, dtype=float))
    except np.linalg.LinAlgError:
        raise ValueError("those 4 points don't make a usable shape (3 of them are in a line?)")
    return np.array([[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1.0]])


def map_point(matrix, x, y):
    u, v, w = matrix @ np.array([x, y, 1.0])
    return u / w, v / w


def map_box(matrix, box, frame_size=None):
    """[x, y, w, h] -> the box around where its 4 corners land, as whole pixels, kept in the frame."""
    x, y, w, h = box
    xs = []
    ys = []
    for corner_x, corner_y in [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]:
        u, v = map_point(matrix, corner_x, corner_y)
        xs.append(u)
        ys.append(v)
    left = math.floor(min(xs))
    top = math.floor(min(ys))
    right = math.ceil(max(xs))
    bottom = math.ceil(max(ys))
    if frame_size is not None:
        left = max(0, left)
        top = max(0, top)
        right = min(frame_size[0], right)
        bottom = min(frame_size[1], bottom)
    return [left, top, max(1, right - left), max(1, bottom - top)]


def check_corners(corners):
    """Corners as [[x, y] x 4] in top-left, top-right, bottom-right, bottom-left order, or a
    ValueError that says what's wrong (wrong count, clicked out of order, squashed flat)."""
    if len(corners) != 4:
        raise ValueError(f"need 4 corners, got {len(corners)}")
    points = []
    for corner in corners:
        if len(corner) != 2:
            raise ValueError(f"a corner is [x, y], got {corner}")
        points.append([float(corner[0]), float(corner[1])])

    # walking TL -> TR -> BR -> BL on screen (y down) always turns the same way, clockwise.
    # a negative turn means a corner is out of order (or the shape folds over itself)
    for index in range(4):
        ax, ay = points[index]
        bx, by = points[(index + 1) % 4]
        cx, cy = points[(index + 2) % 4]
        turn = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if turn <= 0:
            raise ValueError("corners have to go top-left, top-right, bottom-right, bottom-left")

    area = 0.0
    for index in range(4):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % 4]
        area += x1 * y2 - x2 * y1
    if abs(area) / 2 < 100:
        raise ValueError("those corners are almost on top of each other")
    return points


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def frame_size_of(path):
    """(width, height) of a frame. SVG (the placeholder frames) from its width/height."""
    path = Path(path)
    if path.suffix.lower() == ".svg":
        text = path.read_text(encoding="utf-8")
        width = re.search(r'<svg[^>]*\swidth="(\d+(?:\.\d+)?)"', text)
        height = re.search(r'<svg[^>]*\sheight="(\d+(?:\.\d+)?)"', text)
        if not width or not height:
            raise ValueError(f"{path.name} has no width/height on its <svg>")
        return [int(float(width.group(1))), int(float(height.group(1)))]
    from PIL import Image

    with Image.open(path) as image:
        return [image.width, image.height]


def boxes_path(device_id, folder):
    return Path(folder) / f"{device_id}.boxes.json"


def reference_path(device_id, folder):
    return Path(folder) / f"{device_id}.reference.json"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def now_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Calibration:
    """One run: a frame, a device, and either making the reference or using it."""

    def __init__(self, frame_path, device_id, make_reference=False, folder=CALIBRATION_DIR):
        self.frame_path = Path(frame_path)
        self.device_id = device_id
        self.make_reference = make_reference
        self.folder = Path(folder)
        self.frame_size = frame_size_of(self.frame_path)

        if not boxes_path(device_id, folder).is_file():
            raise SystemExit(f"No {boxes_path(device_id, folder)} to start from. Make the boxes first "
                             f"(read_screen.py --calibrate {device_id})")
        self.current = read_json(boxes_path(device_id, folder))

        self.reference = None
        if not make_reference:
            if not reference_path(device_id, folder).is_file():
                raise SystemExit(
                    f"No reference layout for {device_id} yet. Make one first, on the frame the current "
                    f"boxes were made on:\n    python backend/calibrate.py --reference <that frame> {device_id}")
            self.reference = read_json(reference_path(device_id, folder))

    def preview(self, corners):
        """{"boxes", "panel"}: where every box goes for these clicked corners."""
        corners = check_corners(corners)
        if self.make_reference:
            # the reference is the current boxes as they are, the corners just pin the panel down
            return {"boxes": dict(self.current["boxes"]), "panel": corners}
        matrix = homography(self.reference["panel"], corners)
        boxes = {}
        for control_id, box in self.reference["boxes"].items():
            boxes[control_id] = map_box(matrix, box, self.frame_size)
        return {"boxes": boxes, "panel": corners}

    def save(self, corners):
        result = self.preview(corners)
        if self.make_reference:
            reference = {
                "_comment": f"Reference layout for calibrate.py: the {self.device_id} boxes and the control "
                            f"panel's corners (top-left, top-right, bottom-right, bottom-left) on "
                            f"{self.frame_path.name}, the frame those boxes were made on. Made {now_stamp()}.",
                "device_id": self.device_id,
                "frame": self.frame_path.name,
                "frame_size": self.frame_size,
                "panel": result["panel"],
                "boxes": result["boxes"],
            }
            path = reference_path(self.device_id, self.folder)
            path.write_text(pretty_json(reference), encoding="utf-8")
            return {"saved": str(path), "backup": None, "picture": None}

        # back the old boxes up, then write the new ones over just the boxes (baselines etc. stay)
        path = boxes_path(self.device_id, self.folder)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{self.device_id}.boxes.{stamp}.bak.json")
        shutil.copyfile(path, backup)

        updated = dict(self.current)
        updated["_comment"] = (f"Calibrated on {self.frame_path.name} ({self.frame_size[0]}x{self.frame_size[1]}) "
                               f"by clicking the panel corners (backend/calibrate.py) at {now_stamp()}. "
                               "Redo if the camera moves.")
        updated["frame_size"] = self.frame_size
        boxes = dict(self.current.get("boxes") or {})
        for control_id, box in result["boxes"].items():
            boxes[control_id] = box
        updated["boxes"] = boxes
        self.current = updated
        path.write_text(pretty_json(updated), encoding="utf-8")

        picture = self.draw(result)
        return {"saved": str(path), "backup": str(backup), "picture": str(picture) if picture else None}

    def draw(self, result):
        """The frame with the panel and the new boxes on it, next to the boxes file, to look at
        later. Skipped for frames Pillow can't open (SVG)."""
        try:
            from PIL import Image, ImageDraw, ImageFont

            with Image.open(self.frame_path) as source:
                image = source.convert("RGB")
        except Exception:
            return None
        draw = ImageDraw.Draw(image)
        line = max(2, image.width // 500)
        font = ImageFont.load_default(size=max(12, image.width // 70))
        panel = result["panel"]
        for index in range(4):
            draw.line([tuple(panel[index]), tuple(panel[(index + 1) % 4])], fill=(92, 200, 255), width=line)
        for control_id, (x, y, w, h) in result["boxes"].items():
            draw.rectangle([x, y, x + w - 1, y + h - 1], outline=(109, 255, 158), width=line)
            draw.text((x, y + h + 2), control_id, fill=(109, 255, 158), font=font)
        picture = self.folder / f"{self.device_id}.calibration.png"
        image.save(picture)
        return picture

    def session(self, frame_url):
        """What the page needs to start."""
        return {
            "device_id": self.device_id,
            "mode": "reference" if self.make_reference else "calibrate",
            "frame_name": self.frame_path.name,
            "frame_url": frame_url,
            "frame_size": self.frame_size,
            "corner_names": CORNER_NAMES,
            # drawn faint for comparison. only meaningful on a frame the same size
            "current_boxes": self.current.get("boxes") or {},
            "current_frame_size": self.current.get("frame_size"),
        }


# ---------------------------------------------------------------------------
# the little API the /calibrate page talks to
# ---------------------------------------------------------------------------

def make_handler(calibration, port, saved):
    frame_url = f"http://127.0.0.1:{port}/frame"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # quiet, the page says what happened

        def send_cors(self):
            # the page is on the Next.js app (another port), so let it in
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def send_json(self, status, data):
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_cors()
            self.end_headers()

        def do_GET(self):
            if self.path.startswith("/session"):
                self.send_json(200, calibration.session(frame_url))
            elif self.path.startswith("/frame"):
                body = calibration.frame_path.read_bytes()
                kind = mimetypes.guess_type(calibration.frame_path.name)[0] or "application/octet-stream"
                self.send_response(200)
                self.send_cors()
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_json(404, {"error": "not here"})

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                corners = body.get("corners")
                if not isinstance(corners, list):
                    raise ValueError("send {\"corners\": [[x, y], ...]}")
                if self.path.startswith("/preview"):
                    self.send_json(200, calibration.preview(corners))
                elif self.path.startswith("/save"):
                    result = calibration.save(corners)
                    self.send_json(200, result)
                    print(f"Saved {result['saved']}" + (f" (old one backed up to {result['backup']})" if result["backup"] else ""))
                    if result["picture"]:
                        print(f"Picture of the new boxes: {result['picture']}")
                    saved.set()
                else:
                    self.send_json(404, {"error": "not here"})
            except ValueError as err:
                self.send_json(400, {"error": str(err)})

    return Handler


def parse_corners(text):
    corners = []
    for pair in text.split():
        parts = pair.split(",")
        if len(parts) != 2:
            raise SystemExit(f'--corners wants "x,y x,y x,y x,y", got {pair!r}')
        corners.append([float(parts[0]), float(parts[1])])
    return corners


def main(argv=None):
    parser = argparse.ArgumentParser(description="Move a device's control boxes by clicking its panel corners.")
    parser.add_argument("frame", type=Path, help="camera frame (.jpg/.png/.webp, or a placeholder .svg)")
    parser.add_argument("device_id")
    parser.add_argument("--reference", action="store_true",
                        help="this frame is the one the current boxes were made on: save the reference layout")
    parser.add_argument("--corners", help='"x,y x,y x,y x,y" in frame pixels, instead of clicking')
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--web", default="http://localhost:3000", help="where the Next.js app runs")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--folder", type=Path, default=CALIBRATION_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not args.frame.is_file():
        raise SystemExit(f"No frame at {args.frame}")
    calibration = Calibration(args.frame, args.device_id, make_reference=args.reference, folder=args.folder)

    if args.corners:
        try:
            result = calibration.save(parse_corners(args.corners))
        except ValueError as err:
            raise SystemExit(str(err))
        print(f"Saved {result['saved']}" + (f" (old one backed up to {result['backup']})" if result["backup"] else ""))
        if result["picture"]:
            print(f"Picture of the new boxes: {result['picture']}")
        return 0

    saved = threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(calibration, args.port, saved))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page = f"{args.web}/calibrate?api=http://127.0.0.1:{args.port}"
    what = "reference corners" if args.reference else "panel corners"
    print(f"Click the {what} on {args.frame.name}: {page}")
    print("(the web app has to be running: cd web, npm run dev). Ctrl+C to give up.")
    if not args.no_browser:
        webbrowser.open(page)
    try:
        while not saved.wait(0.5):
            pass
        time.sleep(0.5)  # let the page get its answer before we go
    except KeyboardInterrupt:
        print("\nNothing saved.")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
