#!/usr/bin/env python3
"""Find the buttons and the display screen in one photo of a control panel, using SAM 3.1.

Prints the result as device.controls from shared/types.ts:
    [{"control_id": "button_1", "label": "button_1", "kind": "button",
      "bbox": [x, y, width, height], "confidence": 0.96}, ...]

SAM only finds where things are, it can't read them, so labels are placeholders
(button_1, screen_1). Reading the real labels ("SET", "▲") is a later Muse Spark call.

Usage (MODEL_API_KEY comes from the repo-root .env, or the environment):
    python vision/detect_controls.py panel.jpg               # controls -> panel.controls.json
    python vision/detect_controls.py panel.jpg --draw        # also panel.boxes.png to eyeball
    python vision/detect_controls.py panel.jpg --from-raw panel.sam_raw.json --draw
                                                             # re-parse a saved response, no API call

The raw API responses always get saved to <image>.sam_raw.json for debugging.
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

# One concept per request (SAM wants a short noun phrase, no lists, no "and").
# Each prompt gets its own API call. "kind" is what we call matches in the contract,
# "id_prefix" is how their placeholder ids start.
PROMPTS = [
    {"text": "button", "kind": "button", "id_prefix": "button"},
    {"text": "display screen", "kind": "screen", "id_prefix": "screen"},
]

MIN_CONFIDENCE = 0.0  # drop matches below this. 0 = keep everything

# LED slits next to buttons (the timer's T1-T4 lights) come back as "button" too.
# real buttons are about square, slits are tall and thin, so skip by shape
MIN_BUTTON_CONFIDENCE = 0.0  # skip buttons below this. 0 = off (Inkbird buttons only score 0.83-0.87)
MIN_BUTTON_WIDTH = 20  # px. skip buttons narrower than this
MIN_BUTTON_RATIO = 0.6  # width / height. skip buttons thinner than this (slits are 0.34-0.47)

# ---- API details, from the SAM 3.1 docs (dev.meta.ai/docs/sam/segmenting) ----

API_URL = "https://api.meta.ai/v1/responses"  # same base URL as backend/planner.py
MODEL = "sam-3.1"
API_KEY_ENV = "MODEL_API_KEY"  # same key as the planner
TIMEOUT_SECONDS = 120

# one record per match looks like:
# <0f>0<|box;x1=181;y1=365;x2=556;y2=486;w=1516;h=1600;c=0.96|><|mask;...|>
# we only want the box tokens, masks get ignored
BOX_TOKEN = re.compile(r"<\|box;([^|]*)\|>")


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
# calling SAM
# ---------------------------------------------------------------------------

def image_to_data_url(image_path):
    """Read the image as a base64 data URL. Non-JPEGs get re-encoded as JPEG
    (same pixel size, so box coordinates don't change)."""
    raw_bytes = image_path.read_bytes()
    is_jpeg = raw_bytes[:3] == b"\xff\xd8\xff"

    if not is_jpeg:
        try:
            from PIL import Image
        except ImportError:
            raise SystemExit(f"{image_path} isn't a JPEG and Pillow isn't installed to convert it. "
                             "Use a .jpg or `pip install pillow`.")
        with Image.open(image_path) as image:
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=95)
            raw_bytes = buffer.getvalue()

    encoded = base64.b64encode(raw_bytes).decode("ascii")
    return "data:image/jpeg;base64," + encoded


def call_sam(api_key, prompt_text, image_data_url):
    """One non-streaming request for one concept. Returns the parsed JSON response."""
    body = {
        "model": MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt_text},
                    {"type": "input_image", "image_url": image_data_url},
                ],
            }
        ],
        "metadata": {
            "include_confidence": "true",  # has to be the string "true", not a boolean
            "mask_encoding": "one_bit",  # smallest masks, we only use the boxes anyway
        },
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
            # bad prompt or metadata. retrying the same thing won't help
            raise ApiError(f'400 for prompt "{prompt_text}": the prompt or metadata was rejected. '
                           f"Not retrying.\n{error_text}")
        if err.code == 404:
            raise ApiError(f"404 from {API_URL}: model \"{MODEL}\" wasn't found. Check that "
                           f"{API_KEY_ENV} is the right key and that it has access to SAM 3.1.\n{error_text}")
        raise ApiError(f"HTTP {err.code} for prompt \"{prompt_text}\":\n{error_text}")
    except urllib.error.URLError as err:
        raise ApiError(f"Couldn't reach {API_URL}: {err.reason}")


# ---------------------------------------------------------------------------
# reading the response
# ---------------------------------------------------------------------------

def output_text(response_json):
    """Glue together the text parts from output[].content[].text."""
    pieces = []
    for item in response_json.get("output", []) or []:
        for part in item.get("content", []) or []:
            text = part.get("text")
            if isinstance(text, str):
                pieces.append(text)
    return "".join(pieces)


def parse_boxes(text):
    """Pull every <|box;...|> token out of the output text.
    Empty text = SAM found nothing, which is fine, we just return []."""
    boxes = []
    for match in BOX_TOKEN.finditer(text):
        fields = {}
        for pair in match.group(1).split(";"):
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            fields[key.strip()] = value.strip()

        try:
            box = {
                "x1": int(float(fields["x1"])),
                "y1": int(float(fields["y1"])),
                "x2": int(float(fields["x2"])),
                "y2": int(float(fields["y2"])),
            }
        except (KeyError, ValueError):
            print(f"  skipping a box token I couldn't read: {match.group(0)}", file=sys.stderr)
            continue

        box["confidence"] = float(fields["c"]) if "c" in fields else None
        box["image_width"] = int(float(fields["w"])) if "w" in fields else None
        box["image_height"] = int(float(fields["h"])) if "h" in fields else None
        boxes.append(box)
    return boxes


def button_skip_reason(box):
    """Why a "button" box looks like an LED slit and not a button. None = keep it."""
    width = box["x2"] - box["x1"] + 1
    height = box["y2"] - box["y1"] + 1
    reasons = []
    if width < MIN_BUTTON_WIDTH:
        reasons.append(f"narrower than {MIN_BUTTON_WIDTH}px")
    if width / height < MIN_BUTTON_RATIO:
        reasons.append(f"width/height under {MIN_BUTTON_RATIO}")
    confidence = box["confidence"]
    if confidence is not None and confidence < MIN_BUTTON_CONFIDENCE:
        reasons.append(f"confidence under {MIN_BUTTON_CONFIDENCE}")
    if not reasons:
        return None
    return ", ".join(reasons)


def to_controls(boxes_by_prompt):
    """SAM boxes -> device.controls entries, numbered per kind in reading order."""
    controls = []
    for prompt in PROMPTS:
        boxes = boxes_by_prompt.get(prompt["text"], [])

        kept = []
        for box in boxes:
            confidence = box["confidence"]
            if confidence is not None and confidence < MIN_CONFIDENCE:
                continue
            if prompt["kind"] == "button":
                reason = button_skip_reason(box)
                if reason is not None:
                    # say what got dropped, so a missing button is easy to track down
                    width = box["x2"] - box["x1"] + 1
                    height = box["y2"] - box["y1"] + 1
                    shown_confidence = "?" if confidence is None else round(confidence, 4)
                    print(f"  skipping button at [{box['x1']}, {box['y1']}, {width}, {height}]: "
                          f"{width}x{height}px, ratio {width / height:.2f}, confidence {shown_confidence} "
                          f"({reason})", file=sys.stderr)
                    continue
            kept.append(box)

        # top-to-bottom, then left-to-right, so button_1 is roughly the top-left one
        kept.sort(key=lambda box: (box["y1"], box["x1"]))

        number = 0
        for box in kept:
            number += 1
            control_id = f"{prompt['id_prefix']}_{number}"
            confidence = box["confidence"]
            if confidence is None:
                print(f"  {control_id}: no confidence in the response, using 0", file=sys.stderr)
                confidence = 0.0

            controls.append({
                "control_id": control_id,
                "label": control_id,  # placeholder until Muse Spark reads the panel
                "kind": prompt["kind"],
                # x2/y2 are inclusive, so +1 for the width/height
                "bbox": [box["x1"], box["y1"], box["x2"] - box["x1"] + 1, box["y2"] - box["y1"] + 1],
                "confidence": round(confidence, 4),
            })
    return controls


# ---------------------------------------------------------------------------
# --draw
# ---------------------------------------------------------------------------

def draw_boxes(image_path, controls, out_path):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("--draw needs Pillow: pip install pillow", file=sys.stderr)
        return

    colors = {"button": (92, 200, 255), "screen": (109, 255, 158)}

    with Image.open(image_path) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)

    # scale line width and text with the image so it's readable on big frames too
    line_width = max(2, image.width // 400)
    try:
        font = ImageFont.load_default(size=max(12, image.width // 60))
    except TypeError:  # older Pillow without size=
        font = ImageFont.load_default()

    # boxes first, so labels always end up on top
    for control in controls:
        x, y, width, height = control["bbox"]
        color = colors.get(control["kind"], (255, 176, 65))
        draw.rectangle([x, y, x + width - 1, y + height - 1], outline=color, width=line_width)

    # then labels: try above the box, then below, then inside, whichever doesn't
    # cover a label we already placed (small neighbouring buttons fight for space)
    placed_labels = []
    for control in controls:
        x, y, width, height = control["bbox"]
        color = colors.get(control["kind"], (255, 176, 65))
        caption = f"{control['label']} {round(control['confidence'] * 100)}%"
        text_box = draw.textbbox((0, 0), caption, font=font)
        label_width = text_box[2] - text_box[0] + 6
        label_height = text_box[3] - text_box[1] + 6

        spots = [y - label_height - 2, y + height + 2, y + 2]
        label_y = spots[0]
        for spot in spots:
            if spot < 0 or spot + label_height > image.height:
                continue
            rect = (x, spot, x + label_width, spot + label_height)
            if not overlaps_any(rect, placed_labels):
                label_y = spot
                break

        rect = (x, label_y, x + label_width, label_y + label_height)
        placed_labels.append(rect)
        draw.rectangle(rect, fill=(0, 0, 0))
        draw.text((x + 3, label_y + 1), caption, fill=color, font=font)

    image.save(out_path)


def overlaps_any(rect, others):
    left, top, right, bottom = rect
    for other_left, other_top, other_right, other_bottom in others:
        if left < other_right and right > other_left and top < other_bottom and bottom > other_top:
            return True
    return False


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Find buttons and the screen on a control panel with SAM 3.1.")
    parser.add_argument("image", type=Path, help="photo of the control panel (.jpg works best)")
    parser.add_argument("--out", type=Path, help="where to write the controls JSON (default: <image>.controls.json)")
    parser.add_argument("--raw-out", type=Path, help="where to save the raw API responses (default: <image>.sam_raw.json)")
    parser.add_argument("--draw", action="store_true", help="also save <image>.boxes.png with the boxes drawn on")
    parser.add_argument("--from-raw", type=Path, help="re-parse a saved .sam_raw.json instead of calling the API")
    args = parser.parse_args()

    image_path = args.image
    if not image_path.is_file():
        raise SystemExit(f"No image at {image_path}")

    base = image_path.with_suffix("")
    out_path = args.out or Path(f"{base}.controls.json")
    raw_path = args.raw_out or Path(f"{base}.sam_raw.json")

    if args.from_raw:
        # debugging: reuse responses we already paid for
        raw_responses = json.loads(args.from_raw.read_text(encoding="utf-8"))
        print(f"Using saved responses from {args.from_raw}", file=sys.stderr)
    else:
        load_dotenv()
        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            raise SystemExit(f"{API_KEY_ENV} isn't set. export {API_KEY_ENV}=... (same key as the planner)")

        image_data_url = image_to_data_url(image_path)
        raw_responses = {}
        try:
            for prompt in PROMPTS:
                print(f'Asking SAM for "{prompt["text"]}"...', file=sys.stderr)
                raw_responses[prompt["text"]] = call_sam(api_key, prompt["text"], image_data_url)
        except ApiError as err:
            # still save whatever came back before the failure
            if raw_responses:
                raw_path.write_text(json.dumps(raw_responses, indent=2), encoding="utf-8")
                print(f"Saved the responses we did get to {raw_path}", file=sys.stderr)
            raise SystemExit(f"SAM call failed: {err}")

        raw_path.write_text(json.dumps(raw_responses, indent=2), encoding="utf-8")
        print(f"Raw responses saved to {raw_path}", file=sys.stderr)

    boxes_by_prompt = {}
    for prompt in PROMPTS:
        response_json = raw_responses.get(prompt["text"])
        if response_json is None:
            print(f'  no response saved for "{prompt["text"]}", skipping', file=sys.stderr)
            continue
        boxes = parse_boxes(output_text(response_json))
        boxes_by_prompt[prompt["text"]] = boxes
        print(f'  "{prompt["text"]}": {len(boxes)} found', file=sys.stderr)

    controls = to_controls(boxes_by_prompt)
    out_path.write_text(json.dumps(controls, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(controls)} controls written to {out_path}", file=sys.stderr)

    if args.draw:
        draw_path = Path(f"{base}.boxes.png")
        draw_boxes(image_path, controls, draw_path)
        print(f"Boxes drawn on {draw_path}", file=sys.stderr)

    print(json.dumps(controls, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
