#!/usr/bin/env python3
"""Read what a control shows in one camera frame: the text on a display, or whether an LED is on.

SAM 3.1 (detect_controls.py) only finds where things are, it can't read them. Reading the
display is a Muse Spark call on a crop of the frame. Indicator lights don't need a model,
we just look at how bright / how red the pixels are.

bbox is [x, y, w, h] in frame pixels, origin top-left, same as Control.bbox in shared/types.ts.

Usage (MODEL_API_KEY comes from the repo-root .env, or the environment):
    python vision/read_screen.py frame.jpg --bbox 820,600,420,140                  # read a display
    python vision/read_screen.py frame.jpg --bbox 820,600,420,140 --expect "SV 80.0"  # PASS/FAIL
    python vision/read_screen.py frame.jpg --bbox 1330,460,48,48 --indicator       # LED on/off, no API call
    python vision/read_screen.py frame.jpg --controls frame.controls.json          # every screen + indicator
    python vision/read_screen.py frame.jpg --bbox ... --draw                       # also save the crop to eyeball

The raw API replies always get saved to <image>.read_raw.json for debugging.

Spike: untested on real Inkbird frames.
"""

import argparse
import base64
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ---- things to tune on real frames ----

PAD_FRACTION = 0.08  # grow the bbox by this much on each side before cropping, so edge digits don't get cut
MIN_SHORT_SIDE = 256  # upscale crops whose short side is smaller than this

# LED thresholds, both 0..1. tune on real rig frames
ON_THRESHOLD = 0.6  # mean brightness above this = on
RED_THRESHOLD = 0.25  # mean (red - avg(green, blue)) above this = on. HEAT is a red LED
GREEN_THRESHOLD = 0.25  # mean (green - avg(red, blue)) above this = on. COOL is a green LED

PROMPT = (
    "This is a crop of a camera photo showing one display on a temperature controller. "
    "Reply with only the exact characters shown on the display, nothing else, no explanation. "
    "If the display is blank or you can't read it, reply with an empty string."
)

# Inkbird ITC-308 labels we pull numbers out of. Anything else stays only in "text"
INKBIRD_FIELDS = ["PV", "SV", "TS", "HD", "CD", "AH", "AL", "PT", "CA"]

# ---- API details, from dev.meta.ai/docs/image-understanding (Chat Completions example) ----

API_URL = "https://api.meta.ai/v1/chat/completions"  # same base URL as backend/planner.py
MODEL = "muse-spark-1.3"  # same model as the planner, the docs' image understanding examples use it too
API_KEY_ENV = "MODEL_API_KEY"  # same key as the planner
TIMEOUT_SECONDS = 60

# no temperature / max_tokens: the docs don't list them for Chat Completions.
# no confidence either: token logprobs are only documented for the Responses API, so it's always None here


class ApiError(Exception):
    pass


def load_dotenv():
    """Read KEY=value lines from the repo-root .env (same as risk/scorer.py). Real env vars win."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------

def open_rgb(image_path):
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("read_screen needs Pillow to crop: pip install pillow")
    with Image.open(image_path) as source:
        return source.convert("RGB")


def padded_box(bbox, frame_width, frame_height, pad_fraction):
    """bbox [x, y, w, h] grown by pad_fraction on each side, clamped to the frame.
    Returns (left, top, right, bottom) for Pillow's crop (right/bottom exclusive)."""
    x, y, w, h = bbox
    pad_x = w * pad_fraction
    pad_y = h * pad_fraction
    left = max(0, int(round(x - pad_x)))
    top = max(0, int(round(y - pad_y)))
    right = min(frame_width, int(round(x + w + pad_x)))
    bottom = min(frame_height, int(round(y + h + pad_y)))
    if right <= left or bottom <= top:
        raise ValueError(f"bbox {bbox} is outside the {frame_width}x{frame_height} frame")
    return left, top, right, bottom


def crop_for_reading(image, bbox):
    """Padded crop, upscaled so the short side is at least MIN_SHORT_SIDE."""
    from PIL import Image

    box = padded_box(bbox, image.width, image.height, PAD_FRACTION)
    crop = image.crop(box)

    short_side = min(crop.width, crop.height)
    if short_side < MIN_SHORT_SIDE:
        scale = MIN_SHORT_SIDE / short_side
        new_size = (int(round(crop.width * scale)), int(round(crop.height * scale)))
        crop = crop.resize(new_size, Image.LANCZOS)
    return crop


def image_to_data_url(image):
    # PNG so the digits don't pick up JPEG artifacts
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return "data:image/png;base64," + encoded


# ---------------------------------------------------------------------------
# calling Muse Spark
# ---------------------------------------------------------------------------

def call_model(api_key, image_data_url):
    """One Chat Completions request with the prompt and the crop. Returns the parsed JSON response."""
    body = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",  # the docs say images only get looked at in user messages
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        error_text = err.read().decode("utf-8", errors="replace")
        if err.code == 400:
            raise ApiError(f"400: the request or the image was rejected. Not retrying.\n{error_text}")
        if err.code == 404:
            raise ApiError(f'404 from {API_URL}: model "{MODEL}" wasn\'t found. Check {API_KEY_ENV}.\n{error_text}')
        raise ApiError(f"HTTP {err.code}:\n{error_text}")
    except urllib.error.URLError as err:
        raise ApiError(f"Couldn't reach {API_URL}: {err.reason}")


def reply_text(response_json):
    """choices[0].message.content, or "" if it isn't there."""
    choices = response_json.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    # some APIs send content as a list of parts, glue the text ones together just in case
    pieces = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                pieces.append(part["text"])
    return "".join(pieces)


# ---------------------------------------------------------------------------
# cleaning up and parsing what it read
# ---------------------------------------------------------------------------

QUOTE_CHARS = "\"'`“”‘’"
# 80F, 80 F, 80°F, 80 ° F, 80ºF, 80 deg F -> 80°F. Needs a digit first so "OFF" stays "OFF"
DEGREES_F = re.compile(r"(\d)\s*(?:°|º|deg)?\s*F\b")


def clean_reply(text):
    text = text.strip()
    # strip wrapping quotes/backticks, maybe more than one layer ("`TS 80.0`")
    while len(text) >= 2 and text[0] in QUOTE_CHARS and text[-1] in QUOTE_CHARS:
        text = text[1:-1].strip()
    text = text.strip(QUOTE_CHARS).strip()
    text = " ".join(text.split())  # collapse spaces and newlines
    text = DEGREES_F.sub(r"\1°F", text)
    return text


INKBIRD_VALUE = re.compile(r"\b(" + "|".join(INKBIRD_FIELDS) + r")\s*[:=]?\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)


def parse_inkbird(text):
    """"TS 80.0" -> {"TS": 80.0}, "PV 72.0 SV 80.0" -> {"PV": 72.0, "SV": 80.0}. Unknown text is ignored."""
    parsed = {}
    for match in INKBIRD_VALUE.finditer(text):
        parsed[match.group(1).upper()] = float(match.group(2))
    return parsed


def normalize_for_compare(text):
    """Lowercase, no spaces, 80.0 -> 80, 80.50 -> 80.5, degrees written one way."""
    text = DEGREES_F.sub(r"\1°F", text)
    text = "".join(text.lower().split())

    def short_number(match):
        number = match.group(0)
        number = number.rstrip("0").rstrip(".")
        return number or "0"

    return re.sub(r"\d+\.\d+", short_number, text)


def matches_expectation(read_text, text_contains):
    """True if the screen shows text_contains. Case-insensitive, ignores spaces, 80 == 80.0.
    Numbers have to match whole: "TS 80" doesn't match "TS 800" or "TS 80.5".
    Empty text_contains always matches (the contract uses "" for indicator checks)."""
    expected = normalize_for_compare(text_contains)
    if not expected:
        return True
    seen = normalize_for_compare(read_text)

    start = seen.find(expected)
    while start != -1:
        end = start + len(expected)
        ok = True
        # don't let "80" match inside "180"
        if expected[0].isdigit() and start > 0 and seen[start - 1].isdigit():
            ok = False
        # ...or inside "800" / "80.5"
        if expected[-1].isdigit() and end < len(seen):
            if seen[end].isdigit():
                ok = False
            if seen[end] == "." and end + 1 < len(seen) and seen[end + 1].isdigit():
                ok = False
        if ok:
            return True
        start = seen.find(expected, start + 1)
    return False


# ---------------------------------------------------------------------------
# the two readers
# ---------------------------------------------------------------------------

def read_screen(image_path, bbox, api_key=None, raw_log=None, draw_path=None):
    """Read a display. Returns {"text": str, "parsed": dict, "confidence": None}.
    raw_log: a list to append the raw API reply to. draw_path: save the crop we sent there."""
    # crop (and --draw) first, so you can still eyeball the crop without a key
    image = open_rgb(image_path)
    crop = crop_for_reading(image, bbox)
    if draw_path is not None:
        crop.save(draw_path)

    if api_key is None:
        api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise ApiError(f"{API_KEY_ENV} isn't set. export {API_KEY_ENV}=... (same key as the planner)")

    response_json = call_model(api_key, image_to_data_url(crop))
    if raw_log is not None:
        raw_log.append({"bbox": list(bbox), "crop_size": [crop.width, crop.height], "response": response_json})

    text = clean_reply(reply_text(response_json))
    return {"text": text, "parsed": parse_inkbird(text), "confidence": None}


def read_indicator(image_path, bbox, draw_path=None):
    """Is an LED on? No model call. Returns {"on": bool, "brightness": float, "redness": float,
    "greenness": float}, all 0..1-ish (redness/greenness can go negative).
    On if any of the three passes its threshold: HEAT is red, COOL is green.
    Exact bbox, no padding, so the panel around the LED doesn't water down the numbers."""
    image = open_rgb(image_path)
    box = padded_box(bbox, image.width, image.height, 0.0)
    crop = image.crop(box)
    if draw_path is not None:
        crop.save(draw_path)

    total_brightness = 0.0
    total_redness = 0.0
    total_greenness = 0.0
    count = 0
    for red, green, blue in crop.getdata():
        total_brightness += (red + green + blue) / 3
        total_redness += red - (green + blue) / 2
        total_greenness += green - (red + blue) / 2
        count += 1

    brightness = total_brightness / count / 255
    redness = total_redness / count / 255
    greenness = total_greenness / count / 255
    on = brightness > ON_THRESHOLD or redness > RED_THRESHOLD or greenness > GREEN_THRESHOLD
    return {
        "on": on,
        "brightness": round(brightness, 4),
        "redness": round(redness, 4),
        "greenness": round(greenness, 4),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def parse_bbox(text):
    parts = text.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--bbox wants x,y,w,h")
    try:
        numbers = [int(float(part)) for part in parts]
    except ValueError:
        raise argparse.ArgumentTypeError("--bbox wants four numbers: x,y,w,h")
    if numbers[2] <= 0 or numbers[3] <= 0:
        raise argparse.ArgumentTypeError("--bbox width and height have to be > 0")
    return numbers


def load_controls(path):
    """A detect_controls.py .controls.json (a list), or anything with "controls" / "device.controls"
    (a Device or a fixture Job), so the fixture boxes work too."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "device" in data:
        data = data["device"]
    if isinstance(data, dict):
        data = data.get("controls", [])
    return data


def main():
    parser = argparse.ArgumentParser(description="Read a display (Muse Spark) or an LED (pixels) in a camera frame.")
    parser.add_argument("image", type=Path, help="the camera frame")
    parser.add_argument("--bbox", type=parse_bbox, help="x,y,w,h in frame pixels")
    parser.add_argument("--indicator", action="store_true", help="treat --bbox as an LED, no API call")
    parser.add_argument("--controls", type=Path, help="controls JSON (detect_controls.py output, a Device or a fixture)")
    parser.add_argument("--expect", help='with a display: print PASS/FAIL against this text, e.g. "TS 80.0"')
    parser.add_argument("--draw", action="store_true", help="save each crop next to the image to eyeball")
    parser.add_argument("--raw-out", type=Path, help="where to save raw API replies (default: <image>.read_raw.json)")
    args = parser.parse_args()
    load_dotenv()

    image_path = args.image
    if not image_path.is_file():
        raise SystemExit(f"No image at {image_path}")
    if (args.bbox is None) == (args.controls is None):
        raise SystemExit("Give exactly one of --bbox or --controls")

    base = image_path.with_suffix("")
    raw_path = args.raw_out or Path(f"{base}.read_raw.json")

    # what to read: (name, kind, bbox)
    targets = []
    if args.bbox is not None:
        targets.append(("bbox", "indicator" if args.indicator else "screen", args.bbox))
    else:
        for control in load_controls(args.controls):
            kind = control.get("kind")
            if kind in ("screen", "indicator"):
                targets.append((control.get("control_id", "?"), kind, control["bbox"]))
            # buttons have nothing to read
        if not targets:
            raise SystemExit(f"No screens or indicators in {args.controls}")

    raw_log = []
    results = {}
    failed = False
    for name, kind, bbox in targets:
        draw_path = Path(f"{base}.{name}.crop.png") if args.draw else None
        if kind == "indicator":
            result = read_indicator(image_path, bbox, draw_path=draw_path)
            print(f"{name}: {'ON' if result['on'] else 'off'} "
                  f"(brightness {result['brightness']}, redness {result['redness']}, greenness {result['greenness']})", file=sys.stderr)
        else:
            print(f"{name}: reading the display...", file=sys.stderr)
            try:
                result = read_screen(image_path, bbox, raw_log=raw_log, draw_path=draw_path)
            except ApiError as err:
                # keep going with the other controls, the error goes in the result
                result = {"text": "", "parsed": {}, "confidence": None, "error": str(err)}
                print(f"  failed: {err}", file=sys.stderr)
            else:
                print(f'  read "{result["text"]}"', file=sys.stderr)
            if args.expect is not None:
                passed = "error" not in result and matches_expectation(result["text"], args.expect)
                result["expect"] = args.expect
                result["pass"] = passed
                print(f'  {"PASS" if passed else "FAIL"}: expected "{args.expect}"', file=sys.stderr)
                if not passed:
                    failed = True
        if draw_path is not None:
            print(f"  crop saved to {draw_path}", file=sys.stderr)
        results[name] = result

    if raw_log:
        raw_path.write_text(json.dumps(raw_log, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Raw replies saved to {raw_path}", file=sys.stderr)

    # one target -> just its result, --controls -> keyed by control_id
    output = results["bbox"] if args.bbox is not None else results
    print(json.dumps(output, indent=2, ensure_ascii=False))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
