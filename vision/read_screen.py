#!/usr/bin/env python3
"""Read what a control shows in one camera frame: the text on a display, or whether an LED is on.

SAM 3.1 (detect_controls.py) only finds where things are, it can't read them. Reading a
display is a Muse Spark call on a crop of the frame. Indicator lights don't need a model,
we compare their pixels to an off-state baseline saved at calibration.

Boxes come from vision/calibration/<device>.boxes.json first (fixed camera, so fixed boxes).
Only if a control has no saved box do we ask SAM ("display screen" / "indicator light").
bbox is [x, y, w, h] in frame pixels, origin top-left, same as Control.bbox in shared/types.ts.

Usage (MODEL_API_KEY comes from the repo-root .env, or the environment):
    python vision/read_screen.py frame.jpg                                   # every calibrated screen + LED
    python vision/read_screen.py frame.jpg --control screen_sv --expect "80"  # PASS / FAIL / UNSURE
    python vision/read_screen.py frame.jpg --control led_heat                 # LED on/off, no model call
    python vision/read_screen.py frame.jpg --bbox 820,600,420,140             # a display at a one-off box
    python vision/read_screen.py frame.jpg --bbox 1330,460,48,48 --indicator  # an LED at a one-off box
    python vision/read_screen.py frame.jpg --controls frame.controls.json     # boxes from detect_controls.py
    python vision/read_screen.py frame.jpg --calibrate inkbird-01             # set up the boxes + LED baselines
    add --draw to any of them to save the crops next to the image

Screens get read 2-3 times and voted on, see vote_reads(). The raw API replies always
get saved to <image>.read_raw.json for debugging.

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
from datetime import datetime, timezone
from pathlib import Path

# ---- things to tune on real frames ----

PAD_FRACTION = 0.08  # grow the bbox by this much on each side before cropping, so edge digits don't get cut
MIN_SHORT_SIDE = 256  # upscale crops whose short side is smaller than this

# LED on/off against the baseline saved at --calibrate (scores are 0..1). tune on real rig frames
BRIGHTNESS_DELTA = 0.15  # on if mean brightness is this much above the off baseline
COLOUR_DELTA = 0.15  # on if the LED's colour score (redness for HEAT, greenness for COOL) is this much above
NEAR_WHITE = 235  # a pixel with every channel above this is blown out
SATURATED_FRACTION = 0.5  # more than this share of blown-out pixels = the LED is so bright the camera clipped it

# no baseline saved for an LED? fall back to plain thresholds. tune on real rig frames
ON_THRESHOLD = 0.6  # mean brightness above this = on
RED_THRESHOLD = 0.25  # mean (red - avg(green, blue)) above this = on. HEAT is a red LED
GREEN_THRESHOLD = 0.25  # mean (green - avg(red, blue)) above this = on. COOL is a green LED

# which colour score counts for which LED
LED_COLOUR = {"led_heat": "redness", "led_cool": "greenness"}

# sanity check on what the screen read. outside this = confidence "low"
TEMP_MIN_F = 32
TEMP_MAX_F = 212

PROMPT = (
    "This is a crop of a camera photo showing one 7-segment LED readout on a temperature controller. "
    "Reply with only the characters shown, using only 0-9, \".\", \"-\" and the letters P, V, S, T. "
    "Nothing else, no units, no explanation. "
    "Treat 6 and 8 carefully: a 6 has no top-right segment, an 8 has all seven. "
    "If the readout is blank or you can't read it, reply with an empty string."
)

# Inkbird ITC-308 labels we pull numbers out of. Anything else stays only in "text"
INKBIRD_FIELDS = ["PV", "SV", "TS", "HD", "CD", "AH", "AL", "PT", "CA"]
# the ones that are temperatures, for the 32-212 °F check ("value" = a bare number on its own)
TEMPERATURE_FIELDS = ["PV", "SV", "TS", "AH", "AL", "value"]

DEFAULT_DEVICE = "inkbird-01"
REPO_ROOT = Path(__file__).resolve().parent.parent
CALIBRATION_DIR = Path(__file__).resolve().parent / "calibration"
DEVICES_DIR = REPO_ROOT / "backend" / "devices"  # Pam's device profiles, read only

# what to ask SAM for when a control has no saved box
SAM_PROMPTS = {"screen": "display screen", "indicator": "indicator light", "button": "button"}

# ---- API details, from dev.meta.ai/docs/api-reference/responses/schemas ----

API_URL = "https://api.meta.ai/v1/responses"  # same as detect_controls.py
MODEL = "muse-spark-1.3"  # same model as the planner, the docs' image understanding examples use it too
API_KEY_ENV = "MODEL_API_KEY"  # same key as the planner
TIMEOUT_SECONDS = 60
# Input Image Content: "Render detail for the image: one of high, low, auto, or original. Defaults to auto."
IMAGE_DETAIL = "high"
# temperature is documented too, but left at the default on purpose: at 0 the reads would just
# repeat each other and the vote wouldn't catch anything


class ApiError(Exception):
    pass


def load_dotenv():
    """Read KEY=value lines from the repo-root .env (same as risk/scorer.py). Real env vars win."""
    env_file = REPO_ROOT / ".env"
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


def log(message):
    print(message, file=sys.stderr)


def get_api_key():
    api_key = os.environ.get(API_KEY_ENV)
    if not api_key:
        raise ApiError(f"{API_KEY_ENV} isn't set. Put it in the repo-root .env (same key as the planner)")
    return api_key


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
# where the boxes come from: calibration file first, SAM second
# ---------------------------------------------------------------------------

def calibration_path(device_id):
    return CALIBRATION_DIR / f"{device_id}.boxes.json"


def load_calibration(device_id):
    path = calibration_path(device_id)
    if not path.exists():
        return {"boxes": {}, "baselines": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("boxes", {})
    data.setdefault("baselines", {})
    return data


def control_kind(control_id):
    if control_id.startswith("screen"):
        return "screen"
    if control_id.startswith("led"):
        return "indicator"
    return "button"


def sam_boxes(image_path, sam_prompt, api_key):
    """Ask SAM for one concept. Returns [{"bbox": [x, y, w, h], "confidence": c}, ...]"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import detect_controls

    try:
        response_json = detect_controls.call_sam(api_key, sam_prompt, detect_controls.image_to_data_url(Path(image_path)))
    except detect_controls.ApiError as err:
        raise ApiError(str(err))
    found = []
    for box in detect_controls.parse_boxes(detect_controls.output_text(response_json)):
        # x2/y2 are inclusive, same as detect_controls.to_controls
        bbox = [box["x1"], box["y1"], box["x2"] - box["x1"] + 1, box["y2"] - box["y1"] + 1]
        found.append({"bbox": bbox, "confidence": box["confidence"]})
    return found


def pick_sam_box(control_id, found):
    """Guess which SAM match is which control, from the Inkbird layout:
    PV screen above SV, HEATING light left of COOLING, SET above ▲ (left) and ▼ (right)."""
    if not found:
        return None
    top_to_bottom = sorted(found, key=lambda match: match["bbox"][1])
    left_to_right = sorted(found, key=lambda match: match["bbox"][0])

    if control_id == "screen_pv":
        return top_to_bottom[0]["bbox"]
    if control_id == "screen_sv":
        return top_to_bottom[1]["bbox"] if len(top_to_bottom) > 1 else None
    if control_id == "led_heat":
        return left_to_right[0]["bbox"] if len(left_to_right) > 1 else None
    if control_id == "led_cool":
        return left_to_right[-1]["bbox"] if len(left_to_right) > 1 else None
    if control_id == "btn_set":
        return top_to_bottom[0]["bbox"]
    if control_id in ("btn_up", "btn_down") and len(top_to_bottom) >= 3:
        bottom_two = sorted(top_to_bottom[1:3], key=lambda match: match["bbox"][0])
        return bottom_two[0]["bbox"] if control_id == "btn_up" else bottom_two[1]["bbox"]
    return None


def resolve_box(image_path, control_id, device_id, bbox=None, api_key=None):
    """Returns (bbox, source). source is "given", "calibration" or "sam"."""
    if bbox is not None:
        return list(bbox), "given"

    calibration = load_calibration(device_id)
    saved = calibration["boxes"].get(control_id)
    if saved is not None:
        log(f"{control_id}: box from calibration ({calibration_path(device_id).name})")
        return list(saved), "calibration"

    kind = control_kind(control_id)
    sam_prompt = SAM_PROMPTS[kind]
    log(f'{control_id}: no saved box, asking SAM for "{sam_prompt}"')
    found = sam_boxes(image_path, sam_prompt, api_key or get_api_key())
    picked = pick_sam_box(control_id, found)
    if picked is None:
        raise ApiError(f'SAM found {len(found)} "{sam_prompt}" match(es), not enough to tell which one is {control_id}')
    log(f"{control_id}: box from SAM {picked}")
    return picked, "sam"


# ---------------------------------------------------------------------------
# calling Muse Spark
# ---------------------------------------------------------------------------

def call_model(api_key, image_data_url):
    """One Responses request with the prompt and the crop. Returns the parsed JSON response."""
    body = {
        "model": MODEL,
        "input": [
            {
                "type": "message",
                "role": "user",  # the docs say images only get looked at in user messages
                "content": [
                    {"type": "input_text", "text": PROMPT},
                    {"type": "input_image", "image_url": image_data_url, "detail": IMAGE_DETAIL},
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
    """output[] message items -> content[] output_text parts -> text, glued together.
    (the docs: Output Message -> Output Text Content -> "text"). "" if there's none."""
    pieces = []
    for item in response_json.get("output", []) or []:
        if item.get("type") != "message":
            continue  # reasoning items and such
        for part in item.get("content", []) or []:
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
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
BARE_NUMBER = re.compile(r"^(-?\d+(?:\.\d+)?)(?:°F)?$")


def parse_inkbird(text):
    """"TS 80.0" -> {"TS": 80.0}, "PV 72.0 SV 80.0" -> {"PV": 72.0, "SV": 80.0}.
    A number on its own ("86.8", what one 7-segment readout shows) -> {"value": 86.8}.
    Unknown text is ignored."""
    parsed = {}
    for match in INKBIRD_VALUE.finditer(text):
        parsed[match.group(1).upper()] = float(match.group(2))
    bare = BARE_NUMBER.match(text.strip())
    if bare:
        parsed["value"] = float(bare.group(1))
    return parsed


def out_of_range_temperatures(parsed):
    """The temperatures in parsed that can't be right for a room/water controller."""
    bad = []
    for field in TEMPERATURE_FIELDS:
        if field in parsed and not (TEMP_MIN_F <= parsed[field] <= TEMP_MAX_F):
            bad.append(field)
    return bad


def normalize_for_compare(text):
    """Lowercase, no spaces, 80.0 -> 80, 80.50 -> 80.5, degrees written one way."""
    text = DEGREES_F.sub(r"\1°F", text)
    text = "".join(text.lower().split())

    def short_number(match):
        number = match.group(0)
        number = number.rstrip("0").rstrip(".")
        return number or "0"

    return re.sub(r"\d+\.\d+", short_number, text)


def matches_expectation(read_text, text_contains, confidence=None):
    """"PASS" if the screen shows text_contains, "FAIL" if not, "UNSURE" if the read was "low"
    confidence (never PASS or FAIL on a read we don't trust, the backend should re-read).
    Case-insensitive, ignores spaces, 80 == 80.0. Numbers have to match whole: "TS 80"
    doesn't match "TS 800" or "TS 80.5". Empty text_contains always passes (indicator checks)."""
    if confidence == "low":
        return "UNSURE"

    expected = normalize_for_compare(text_contains)
    if not expected:
        return "PASS"
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
            return "PASS"
        start = seen.find(expected, start + 1)
    return "FAIL"


# ---------------------------------------------------------------------------
# reading a screen: 2-3 reads and a vote
# ---------------------------------------------------------------------------

def vote_reads(read_once):
    """read_once() -> cleaned text of one read. Reads twice: agree = "high".
    Disagree = a third read, majority wins = "medium". All three differ = "low", first read.
    Then the sanity check: a temperature outside 32-212 °F = "low" whatever the vote said.
    Returns {"text", "parsed", "confidence", "reads", "out_of_range"}."""
    reads = [read_once(), read_once()]
    if normalize_for_compare(reads[0]) == normalize_for_compare(reads[1]):
        text, confidence = reads[0], "high"
    else:
        reads.append(read_once())
        third = normalize_for_compare(reads[2])
        if third == normalize_for_compare(reads[0]):
            text, confidence = reads[0], "medium"
        elif third == normalize_for_compare(reads[1]):
            text, confidence = reads[1], "medium"
        else:
            text, confidence = reads[0], "low"

    parsed = parse_inkbird(text)
    bad = out_of_range_temperatures(parsed)
    if bad:
        confidence = "low"
    return {"text": text, "parsed": parsed, "confidence": confidence, "reads": reads, "out_of_range": bad}


def read_screen(image_path, control_id=None, bbox=None, device_id=DEFAULT_DEVICE, raw_log=None, draw_path=None):
    """Read one 7-segment readout (screen_pv or screen_sv, never both at once).
    Returns {"text", "parsed", "confidence": "high"|"medium"|"low", "reads", "out_of_range",
    "bbox", "box_source"}. raw_log: a list to append the raw API replies to."""
    box, source = resolve_box(image_path, control_id or "screen", device_id, bbox=bbox)

    # crop (and --draw) before the key check, so you can still eyeball the crop without a key
    image = open_rgb(image_path)
    crop = crop_for_reading(image, box)
    if draw_path is not None:
        crop.save(draw_path)
    api_key = get_api_key()
    data_url = image_to_data_url(crop)

    def read_once():
        response_json = call_model(api_key, data_url)
        text = clean_reply(reply_text(response_json))
        if raw_log is not None:
            raw_log.append({"control_id": control_id, "bbox": box, "crop_size": [crop.width, crop.height],
                            "text": text, "response": response_json})
        return text

    result = vote_reads(read_once)
    result["bbox"] = box
    result["box_source"] = source
    return result


# ---------------------------------------------------------------------------
# reading an LED
# ---------------------------------------------------------------------------

def led_scores(crop):
    """Mean brightness, redness (red - avg(green, blue)), greenness (green - avg(red, blue)),
    all /255, and the share of blown-out (near white) pixels."""
    total_brightness = 0.0
    total_redness = 0.0
    total_greenness = 0.0
    white = 0
    count = 0
    for red, green, blue in crop.getdata():
        total_brightness += (red + green + blue) / 3
        total_redness += red - (green + blue) / 2
        total_greenness += green - (red + blue) / 2
        if red > NEAR_WHITE and green > NEAR_WHITE and blue > NEAR_WHITE:
            white += 1
        count += 1
    return {
        "brightness": round(total_brightness / count / 255, 4),
        "redness": round(total_redness / count / 255, 4),
        "greenness": round(total_greenness / count / 255, 4),
        "white_fraction": round(white / count, 4),
    }


def read_indicator(image_path, control_id=None, bbox=None, device_id=DEFAULT_DEVICE, baseline=None, draw_path=None):
    """Is an LED on? No model call. Exact box, no padding.
    Against the off baseline from --calibrate (or the baseline you pass in): on if brightness is
    BRIGHTNESS_DELTA above it, or the LED's colour (redness for led_heat, greenness for led_cool)
    is COLOUR_DELTA above it, or the LED is blown out ("saturated"). No baseline = plain thresholds.
    Returns {"on", "brightness", "redness", "greenness", "saturated", "baseline", "bbox", "box_source"}."""
    box, source = resolve_box(image_path, control_id or "led", device_id, bbox=bbox)
    if baseline is None and control_id is not None:
        baseline = load_calibration(device_id)["baselines"].get(control_id)

    image = open_rgb(image_path)
    crop = image.crop(padded_box(box, image.width, image.height, 0.0))
    if draw_path is not None:
        crop.save(draw_path)

    scores = led_scores(crop)
    saturated = scores["white_fraction"] > SATURATED_FRACTION

    if baseline is not None:
        brighter = scores["brightness"] - baseline["brightness"] > BRIGHTNESS_DELTA
        colour = LED_COLOUR.get(control_id)
        if colour is not None:
            more_colour = scores[colour] - baseline[colour] > COLOUR_DELTA
        else:
            # an LED we don't know the colour of: either colour counts
            more_colour = (scores["redness"] - baseline["redness"] > COLOUR_DELTA
                           or scores["greenness"] - baseline["greenness"] > COLOUR_DELTA)
        on = brighter or more_colour or saturated
    else:
        on = (scores["brightness"] > ON_THRESHOLD or scores["redness"] > RED_THRESHOLD
              or scores["greenness"] > GREEN_THRESHOLD or saturated)

    return {
        "on": on,
        "brightness": scores["brightness"],
        "redness": scores["redness"],
        "greenness": scores["greenness"],
        "saturated": saturated,
        "baseline": baseline,
        "bbox": box,
        "box_source": source,
    }


# ---------------------------------------------------------------------------
# --calibrate
# ---------------------------------------------------------------------------

def profile_control_ids(device_id):
    """Control ids from Pam's backend/devices/<device>.json, in its order."""
    path = DEVICES_DIR / f"{device_id}.json"
    if not path.exists():
        raise SystemExit(f"No device profile at {path}")
    profile = json.loads(path.read_text(encoding="utf-8"))
    return list(profile.get("controls", {}).keys())


def ask(question):
    try:
        return input(question).strip()
    except EOFError:
        return ""


def pretty_json(data):
    """indent=2, but number lists like boxes stay on one line so the file is easy to fix by hand"""
    text = json.dumps(data, indent=2, ensure_ascii=False)

    def one_line(match):
        numbers = [part.strip() for part in match.group(1).split(",")]
        return "[" + ", ".join(numbers) + "]"

    return re.sub(r"\[\s*(-?[\d.]+(?:\s*,\s*-?[\d.]+)*)\s*\]", one_line, text) + "\n"


def calibrate(image_path, device_id, draw=False):
    """Run SAM on the frame, let you confirm or fix each box, measure the LEDs' off baselines, save."""
    control_ids = profile_control_ids(device_id)
    calibration = load_calibration(device_id)
    api_key = get_api_key()

    # what SAM thinks, per kind (one call per prompt, like detect_controls.py)
    found_by_kind = {}
    kinds_needed = []
    for control_id in control_ids:
        kind = control_kind(control_id)
        if kind not in kinds_needed:
            kinds_needed.append(kind)
    for kind in kinds_needed:
        log(f'Asking SAM for "{SAM_PROMPTS[kind]}"...')
        try:
            found_by_kind[kind] = sam_boxes(image_path, SAM_PROMPTS[kind], api_key)
        except ApiError as err:
            log(f"  SAM failed, you'll have to type the {kind} boxes: {err}")
            found_by_kind[kind] = []
        log(f"  {len(found_by_kind[kind])} found")

    print(f"\nConfirm each box for {device_id}. Enter = keep the suggestion, x,y,w,h = use that, - = no box.")
    boxes = {}
    for control_id in control_ids:
        suggestion = pick_sam_box(control_id, found_by_kind[control_kind(control_id)])
        where = "SAM"
        if suggestion is None and control_id in calibration["boxes"]:
            suggestion = calibration["boxes"][control_id]
            where = "saved"
        shown = ",".join(str(number) for number in suggestion) if suggestion else "none"

        while True:
            answer = ask(f"  {control_id} [{where}: {shown}] > ")
            if answer == "":
                if suggestion is not None:
                    boxes[control_id] = list(suggestion)
                break
            if answer == "-":
                break
            try:
                boxes[control_id] = parse_bbox(answer)
                break
            except argparse.ArgumentTypeError as err:
                print(f"    {err}, try again")

    # LED off baselines, only from a frame where they really are off
    baselines = dict(calibration["baselines"])
    leds = [control_id for control_id in boxes if control_kind(control_id) == "indicator"]
    if leds:
        answer = ask("\nAre all the LEDs OFF in this frame? Their baselines get saved from it. [y/N] > ")
        if answer.lower().startswith("y"):
            image = open_rgb(image_path)
            for control_id in leds:
                crop = image.crop(padded_box(boxes[control_id], image.width, image.height, 0.0))
                scores = led_scores(crop)
                baselines[control_id] = {
                    "brightness": scores["brightness"],
                    "redness": scores["redness"],
                    "greenness": scores["greenness"],
                }
                print(f"  {control_id} off baseline: {baselines[control_id]}")
        else:
            print("  Skipped. Keeping the old baselines (if any).")

    image = open_rgb(image_path)
    calibration_out = {
        "_comment": f"Calibrated on {Path(image_path).name} ({image.width}x{image.height}) at "
                    f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}. Redo if the camera moves.",
        "device_id": device_id,
        "frame_size": [image.width, image.height],
        "boxes": boxes,
        "baselines": baselines,
    }
    CALIBRATION_DIR.mkdir(exist_ok=True)
    path = calibration_path(device_id)
    path.write_text(pretty_json(calibration_out), encoding="utf-8")
    print(f"\nSaved {len(boxes)} boxes and {len(baselines)} baselines to {path}")

    if draw:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import detect_controls

        controls = []
        for control_id, box in boxes.items():
            controls.append({"label": control_id, "kind": control_kind(control_id), "bbox": box, "confidence": 1.0})
        draw_path = Path(f"{Path(image_path).with_suffix('')}.calibration.png")
        detect_controls.draw_boxes(Path(image_path), controls, draw_path)
        print(f"Boxes drawn on {draw_path}")


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
    parser = argparse.ArgumentParser(description="Read displays (Muse Spark) and LEDs (pixels) in a camera frame.")
    parser.add_argument("image", type=Path, help="the camera frame")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help=f"whose calibration to use (default {DEFAULT_DEVICE})")
    parser.add_argument("--control", help="one control_id, e.g. screen_sv or led_heat (box from calibration, else SAM)")
    parser.add_argument("--bbox", type=parse_bbox, help="x,y,w,h in frame pixels, instead of a saved box")
    parser.add_argument("--indicator", action="store_true", help="with --bbox and no --control: it's an LED")
    parser.add_argument("--controls", type=Path, help="controls JSON (detect_controls.py output, a Device or a fixture)")
    parser.add_argument("--calibrate", metavar="DEVICE", help="set up the boxes and LED baselines for DEVICE from this frame")
    parser.add_argument("--expect", help='with one display: PASS / FAIL / UNSURE against this text, e.g. "80"')
    parser.add_argument("--draw", action="store_true", help="save each crop next to the image to eyeball")
    parser.add_argument("--raw-out", type=Path, help="where to save raw API replies (default: <image>.read_raw.json)")
    args = parser.parse_args()
    load_dotenv()

    image_path = args.image
    if not image_path.is_file():
        raise SystemExit(f"No image at {image_path}")

    if args.calibrate:
        calibrate(image_path, args.calibrate, draw=args.draw)
        return

    base = image_path.with_suffix("")
    raw_path = args.raw_out or Path(f"{base}.read_raw.json")

    # what to read: (control_id or None, kind, bbox or None)
    targets = []
    if args.controls is not None:
        for control in load_controls(args.controls):
            kind = control.get("kind")
            if kind in ("screen", "indicator"):
                targets.append((control.get("control_id"), kind, control["bbox"]))
            # buttons have nothing to read
        if not targets:
            raise SystemExit(f"No screens or indicators in {args.controls}")
    elif args.control is not None:
        targets.append((args.control, control_kind(args.control), args.bbox))
    elif args.bbox is not None:
        targets.append((None, "indicator" if args.indicator else "screen", args.bbox))
    else:
        # everything readable in the calibration file
        calibration = load_calibration(args.device)
        for control_id in calibration["boxes"]:
            kind = control_kind(control_id)
            if kind in ("screen", "indicator"):
                targets.append((control_id, kind, None))
        if not targets:
            raise SystemExit(f"Nothing to read: no screens or LEDs in {calibration_path(args.device)}. "
                             f"Run --calibrate {args.device} first, or pass --control / --bbox")

    raw_log = []
    results = {}
    failed = False
    for control_id, kind, bbox in targets:
        name = control_id or "bbox"
        draw_path = Path(f"{base}.{name}.crop.png") if args.draw else None
        try:
            if kind == "indicator":
                result = read_indicator(image_path, control_id, bbox=bbox, device_id=args.device, draw_path=draw_path)
                log(f"{name}: {'ON' if result['on'] else 'off'} (brightness {result['brightness']}, "
                    f"redness {result['redness']}, greenness {result['greenness']}, saturated {result['saturated']}, "
                    f"{'against baseline' if result['baseline'] else 'no baseline, plain thresholds'}, "
                    f"box from {result['box_source']})")
            else:
                log(f"{name}: reading...")
                result = read_screen(image_path, control_id, bbox=bbox, device_id=args.device,
                                     raw_log=raw_log, draw_path=draw_path)
                log(f"  reads {result['reads']} -> \"{result['text']}\" ({result['confidence']}"
                    f"{', out of range: ' + ', '.join(result['out_of_range']) if result['out_of_range'] else ''})")
        except (ApiError, ValueError) as err:
            # keep going with the other controls, the error goes in the result
            result = {"error": str(err)}
            log(f"  {name} failed: {err}")

        if args.expect is not None and kind == "screen":
            if "error" in result:
                verdict = "UNSURE"
            else:
                verdict = matches_expectation(result["text"], args.expect, result["confidence"])
            result["expect"] = args.expect
            result["verdict"] = verdict
            log(f'  {verdict}: expected "{args.expect}"')
            if verdict != "PASS":
                failed = True
        if draw_path is not None and draw_path.exists():
            log(f"  crop saved to {draw_path}")
        results[name] = result

    if raw_log:
        raw_path.write_text(json.dumps(raw_log, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"Raw replies saved to {raw_path}")

    # one target -> just its result, several -> keyed by control_id
    output = results if len(results) > 1 else next(iter(results.values()))
    print(json.dumps(output, indent=2, ensure_ascii=False))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
