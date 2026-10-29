#!/usr/bin/env python3
"""Tests for read_screen.py and the LED slit filter in detect_controls.py that don't hit the API. Plain asserts.

    python vision/test_read_screen.py

The LED images are made here in code and deleted afterwards, nothing gets committed.
Screen reads are stubbed, so the voting gets tested without calling Muse Spark.
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import detect_controls  # noqa: E402
from read_screen import (  # noqa: E402
    CALIBRATION_DIR,
    DEVICE_SETTINGS,
    TIMER_CONTROLS,
    clean_reply,
    control_kind,
    find_lcd_rows,
    led_scores,
    matches_expectation,
    padded_box,
    parse_inkbird,
    parse_timer,
    read_indicator,
    row_box,
    suggest_timer_boxes,
    vote_reads,
)


def test_clean_reply():
    assert clean_reply('  "TS 80.0"  ') == "TS 80.0"
    assert clean_reply("`TS 80.0`") == "TS 80.0"
    assert clean_reply("“PV 72.0”") == "PV 72.0"
    assert clean_reply("PV   72.0\n SV 80.0") == "PV 72.0 SV 80.0"
    assert clean_reply("80F") == "80°F"
    assert clean_reply("80 F") == "80°F"
    assert clean_reply("80 ° F") == "80°F"
    assert clean_reply("80ºF") == "80°F"
    assert clean_reply("OFF") == "OFF"  # no digit before the F, leave it
    assert clean_reply("") == ""
    assert clean_reply('""') == ""


def test_parse_inkbird():
    assert parse_inkbird("TS 80.0") == {"TS": 80.0}
    assert parse_inkbird("PV 72.0 SV 80.0") == {"PV": 72.0, "SV": 80.0}
    assert parse_inkbird("pv 72 sv 80") == {"PV": 72.0, "SV": 80.0}
    assert parse_inkbird("HD 1.5") == {"HD": 1.5}
    assert parse_inkbird("CA -2.0") == {"CA": -2.0}
    assert parse_inkbird("86.8") == {"value": 86.8}  # one readout, just a number
    assert parse_inkbird("86.8°F") == {"value": 86.8}
    assert parse_inkbird("TS") == {}
    assert parse_inkbird("HELLO 42") == {}  # unknown text stays only in "text"
    assert parse_inkbird("") == {}


def test_matches_expectation():
    assert matches_expectation("TS 80.0", "ts80") == "PASS"
    assert matches_expectation("ts80", "TS 80.0") == "PASS"
    assert matches_expectation("SV 80", "SV 80.0") == "PASS"
    assert matches_expectation("SV 80.0", "SV 80") == "PASS"
    assert matches_expectation("PV 72.0 SV 80.0", "80") == "PASS"
    assert matches_expectation("80°F", "80 F") == "PASS"
    assert matches_expectation("TS 79.0", "TS 80.0") == "FAIL"
    assert matches_expectation("TS 800", "TS 80") == "FAIL"
    assert matches_expectation("TS 80.5", "TS 80") == "FAIL"
    assert matches_expectation("SV 180", "80") == "FAIL"
    assert matches_expectation("", "TS") == "FAIL"
    assert matches_expectation("anything", "") == "PASS"  # "" = nothing to check (indicator steps)
    # never PASS (or FAIL) on a read we don't trust
    assert matches_expectation("80.0", "80", confidence="low") == "UNSURE"
    assert matches_expectation("79.0", "80", confidence="low") == "UNSURE"
    assert matches_expectation("80.0", "80", confidence="medium") == "PASS"
    assert matches_expectation("80.0", "80", confidence="high") == "PASS"


def test_padded_box_clamps():
    # 8% of 100 = 8 px each side
    assert padded_box([100, 100, 100, 50], 1000, 1000, 0.08) == (92, 96, 208, 154)
    # right at the corner, gets clamped to the frame
    assert padded_box([0, 0, 100, 50], 1000, 1000, 0.08) == (0, 0, 108, 54)
    assert padded_box([950, 980, 50, 20], 1000, 1000, 0.08) == (946, 978, 1000, 1000)


# ---- voting, with stubbed reads ----

def stub_reader(answers):
    """A read_once() that hands out answers in order and counts the calls."""
    calls = []

    def read_once():
        calls.append(1)
        return answers[len(calls) - 1]

    return read_once, calls


def test_vote_agree():
    read_once, calls = stub_reader(["86.8", "86.80"])  # same number, written differently
    result = vote_reads(read_once)
    assert len(calls) == 2, "two agreeing reads shouldn't need a third"
    assert result["confidence"] == "high", result
    assert result["text"] == "86.8"
    assert result["parsed"] == {"value": 86.8}


def test_vote_two_of_three():
    read_once, calls = stub_reader(["88.8", "86.8", "86.8"])
    result = vote_reads(read_once)
    assert len(calls) == 3
    assert result["confidence"] == "medium", result
    assert result["text"] == "86.8"  # the majority, not the first read

    read_once, calls = stub_reader(["86.8", "88.8", "86.8"])
    result = vote_reads(read_once)
    assert result["confidence"] == "medium", result
    assert result["text"] == "86.8"


def test_vote_all_differ():
    read_once, calls = stub_reader(["86.8", "88.8", "66.8"])
    result = vote_reads(read_once)
    assert len(calls) == 3
    assert result["confidence"] == "low", result
    assert result["text"] == "86.8"  # the first read
    assert matches_expectation(result["text"], "86.8", result["confidence"]) == "UNSURE"


def test_vote_out_of_range():
    # both reads agree, but 888 °F can't be right
    read_once, calls = stub_reader(["888", "888"])
    result = vote_reads(read_once)
    assert result["confidence"] == "low", result
    assert result["out_of_range"] == ["value"]

    read_once, calls = stub_reader(["PV 20.0", "PV 20.0"])  # below freezing too
    result = vote_reads(read_once)
    assert result["confidence"] == "low", result

    # a menu code with no number is fine
    read_once, calls = stub_reader(["TS", "TS"])
    assert vote_reads(read_once)["confidence"] == "high"

    # in range stays high
    read_once, calls = stub_reader(["PV 72.0", "PV 72.0"])
    assert vote_reads(read_once)["confidence"] == "high"


# ---- the timer: reply cleanup, row strips, voting without a range check ----

def test_clean_timer_reply():
    four_rows = "00:00 00\n00:00 00\n00:00 00\n00:00 00"
    assert clean_reply(four_rows, multiline=True) == four_rows
    # code fences, stray spaces and blank lines get tidied, the rows stay apart
    assert clean_reply("```\n 00:00  00 \n\n00:05 30\n```", multiline=True) == "00:00 00\n00:05 30"
    assert clean_reply('"00:00 00"', multiline=True) == "00:00 00"
    assert clean_reply("", multiline=True) == ""
    # the Inkbird (single line) still glues lines together, same as before
    assert clean_reply("00:00 00\n00:05 30") == "00:00 00 00:05 30"


def test_parse_timer():
    assert parse_timer("00:00 00\n00:05 30") == {"rows": ["00:00 00", "00:05 30"]}
    assert parse_timer("00:00 00") == {"rows": ["00:00 00"]}
    assert parse_timer("") == {}


def test_row_box():
    # 100 tall in 4 strips = 25 each, top first
    assert row_box([10, 200, 50, 100], 4, 1) == [10, 200, 50, 25]
    assert row_box([10, 200, 50, 100], 4, 4) == [10, 275, 50, 25]
    # uneven split: the strips still cover the whole box with no gaps
    strips = []
    for row in range(1, 4):
        strips.append(row_box([0, 0, 30, 10], 3, row))
    assert strips[0][1] == 0
    for index in range(1, 3):
        assert strips[index][1] == strips[index - 1][1] + strips[index - 1][3]
    assert strips[-1][1] + strips[-1][3] == 10
    # one strip = the whole box
    assert row_box([5, 6, 7, 8], 1, 1) == [5, 6, 7, 8]
    for rows, row in [(4, 0), (4, 5), (0, 1)]:
        try:
            row_box([0, 0, 10, 10], rows, row)
        except ValueError:
            continue
        raise AssertionError(f"row {row} of {rows} should be refused")


def test_vote_timer():
    timer = DEVICE_SETTINGS["timer-01"]
    read_once, calls = stub_reader(["00:00 00\n00:00 00", "00:00 00\n00:00 00"])
    result = vote_reads(read_once, timer)
    assert len(calls) == 2
    assert result["confidence"] == "high", result
    assert result["parsed"] == {"rows": ["00:00 00", "00:00 00"]}
    # no range check on the timer: "0000" would be 0 °F for the Inkbird
    read_once, calls = stub_reader(["0000", "0000"])
    result = vote_reads(read_once, timer)
    assert result["confidence"] == "high", result
    assert result["out_of_range"] == []
    read_once, calls = stub_reader(["0000", "0000"])
    assert vote_reads(read_once)["confidence"] == "low"  # Inkbird is still the default


# ---- LED slits that SAM calls buttons ----

def sam_box(x, y, width, height, confidence):
    """A parsed SAM box like detect_controls.parse_boxes gives (x2/y2 inclusive)."""
    return {"x1": x, "y1": y, "x2": x + width - 1, "y2": y + height - 1, "confidence": confidence,
            "image_width": 959, "image_height": 1280}


def test_skip_led_slits():
    boxes = {
        "button": [
            sam_box(100, 100, 20, 43, 0.64),  # timer LED slit: 20px wide but ratio 0.47
            sam_box(130, 100, 21, 21, 0.73),  # small square Inkbird-style button, low confidence
            sam_box(160, 100, 19, 19, 0.95),  # square but under 20px
            sam_box(10, 200, 62, 46, 0.94),  # a normal timer button
        ],
        "display screen": [sam_box(300, 300, 15, 60, 0.9)],  # screens never get the button filter
    }
    errors = io.StringIO()
    with contextlib.redirect_stderr(errors):
        controls = detect_controls.to_controls(boxes)

    buttons = [control for control in controls if control["kind"] == "button"]
    assert [button["bbox"] for button in buttons] == [[130, 100, 21, 21], [10, 200, 62, 46]], buttons
    # ids still go top-to-bottom, left-to-right over what's left
    assert [button["control_id"] for button in buttons] == ["button_1", "button_2"]
    screens = [control for control in controls if control["kind"] == "screen"]
    assert len(screens) == 1

    # every skip gets printed with its size, ratio and confidence
    printed = errors.getvalue()
    assert "20x43px, ratio 0.47, confidence 0.64" in printed, printed
    assert "19x19px, ratio 1.00, confidence 0.95" in printed, printed
    assert printed.count("skipping button") == 2, printed


# ---- timer-01 calibration ----

def test_timer_calibration_file():
    """timer-01.boxes.json has a box for all 4 LCD rows and all 12 buttons."""
    calibration = json.loads((CALIBRATION_DIR / "timer-01.boxes.json").read_text(encoding="utf-8"))
    assert calibration["device_id"] == "timer-01"
    assert calibration["_comment"].startswith("PLACEHOLDER"), "hand-held photo, has to be redone on the rig"
    boxes = calibration["boxes"]

    rows = ["screen_t1", "screen_t2", "screen_t3", "screen_t4"]
    buttons = ["btn_t1", "btn_t2", "btn_t3", "btn_t4", "btn_h", "btn_m", "btn_s",
               "btn_10m", "btn_5m", "btn_10s", "btn_reset", "btn_stsp"]
    for name in rows + buttons:
        assert name in boxes, f"{name} missing from timer-01.boxes.json"
    assert len(boxes) == 16, sorted(boxes)
    assert sorted(boxes) == sorted(TIMER_CONTROLS)

    frame_width, frame_height = calibration["frame_size"]
    for name, box in boxes.items():
        x, y, w, h = box
        assert w > 0 and h > 0, (name, box)
        assert x >= 0 and y >= 0 and x + w <= frame_width and y + h <= frame_height, (name, box)
    for name in rows:
        assert control_kind(name) == "screen"
    for name in buttons:
        assert control_kind(name) == "button"

    # rows go top to bottom and don't overlap, so each box only has its own row in it
    for index in range(1, 4):
        above = boxes[rows[index - 1]]
        assert boxes[rows[index]][1] >= above[1] + above[3], (rows[index - 1], rows[index])


def test_find_lcd_rows():
    from PIL import Image, ImageDraw

    # a 400x330 LCD at (20, 20) in a dark frame: 4 rows split by divider lines, lit digit blocks
    # in each row, ghost blocks (only a bit darker than the background) and a "T4" label in row 4
    image = Image.new("RGB", (440, 370), (30, 30, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 419, 349], fill=(180, 180, 180))
    band_tops = [20, 103, 183, 263]
    for divider_y in [100, 180, 260]:
        draw.rectangle([30, divider_y, 409, divider_y + 2], fill=(20, 20, 20))
    for top in band_tops:
        for block_x in [120, 165, 210, 255, 300]:
            draw.rectangle([block_x, top + 15, block_x + 29, top + 60], fill=(20, 20, 20))
        draw.rectangle([345, top + 10, 380, top + 65], fill=(150, 150, 150))  # ghost, not lit
    draw.rectangle([60, band_tops[3] + 30, 80, band_tops[3] + 60], fill=(20, 20, 20))  # "T4"

    row_boxes = find_lcd_rows(image, [20, 20, 400, 330], 4)
    assert row_boxes is not None
    expected = []
    for top in band_tops:
        expected.append([120, top + 15, 210, 46])  # first block's left to last block's right
    assert row_boxes == expected, row_boxes

    # a plain screen with no divider lines can't be split
    blank = Image.new("RGB", (100, 100), (180, 180, 180))
    with contextlib.redirect_stderr(io.StringIO()):
        assert find_lcd_rows(blank, [0, 0, 100, 100], 4) is None


def test_suggest_timer_buttons():
    """Buttons get their real names from the layout, LED slits are left out."""
    real = {
        "btn_t1": [697, 289, 81, 81], "btn_t2": [700, 386, 82, 81],
        "btn_t3": [705, 485, 83, 81], "btn_t4": [710, 585, 85, 83],
        "btn_h": [163, 731, 94, 69], "btn_m": [276, 729, 94, 68], "btn_s": [389, 727, 93, 69],
        "btn_10m": [160, 818, 96, 71], "btn_5m": [276, 816, 94, 70], "btn_10s": [390, 814, 93, 70],
        "btn_reset": [521, 780, 103, 100], "btn_stsp": [660, 777, 153, 99],
    }
    found = []
    for name in reversed(list(real)):  # SAM's order shouldn't matter
        found.append({"bbox": real[name], "confidence": 0.95})
    for slit_y in [300, 400, 500, 600]:
        found.append({"bbox": [640, slit_y, 16, 40], "confidence": 0.7})

    with contextlib.redirect_stderr(io.StringIO()):
        suggestions = suggest_timer_boxes(None, {"button": found, "screen": []})
    assert suggestions == real, suggestions

    # not 12 buttons = no guessing, the boxes get typed in by hand
    with contextlib.redirect_stderr(io.StringIO()):
        assert suggest_timer_boxes(None, {"button": found[:10], "screen": []}) == {}


# ---- LEDs against a baseline ----

def test_read_indicator():
    from PIL import Image

    # four 20x20 LEDs side by side: off (dull red, how a dark LED looks), lit red, lit green, blown out
    colours = [(70, 40, 40), (230, 30, 30), (30, 230, 30), (252, 252, 252)]
    with tempfile.TemporaryDirectory() as folder:
        frame = Path(folder) / "frame.png"
        image = Image.new("RGB", (80, 20), (20, 20, 20))
        for index, colour in enumerate(colours):
            for x in range(index * 20, index * 20 + 20):
                for y in range(20):
                    image.putpixel((x, y), colour)
        image.save(frame)

        off_box, red_box, green_box, white_box = [[index * 20, 0, 20, 20] for index in range(4)]
        # the off baseline is what --calibrate would save from an off frame
        baseline = led_scores(image.crop(padded_box(off_box, 80, 20, 0.0)))
        baseline = {"brightness": baseline["brightness"], "redness": baseline["redness"],
                    "greenness": baseline["greenness"]}

        off = read_indicator(frame, "led_heat", bbox=off_box, baseline=baseline)
        assert off["on"] is False, off
        assert off["saturated"] is False, off

        red = read_indicator(frame, "led_heat", bbox=red_box, baseline=baseline)
        assert red["on"] is True, red
        assert red["redness"] > baseline["redness"] + 0.15, red

        green = read_indicator(frame, "led_cool", bbox=green_box, baseline=baseline)
        assert green["on"] is True, green
        assert green["greenness"] > baseline["greenness"] + 0.15, green
        assert green["saturated"] is False, green

        white = read_indicator(frame, "led_heat", bbox=white_box, baseline=baseline)
        assert white["saturated"] is True, white
        assert white["on"] is True, white
        assert white["redness"] < baseline["redness"], "white isn't red, saturation is what turns it on"

        # the off LED read as COOL (greenness) against its own baseline is off too
        assert read_indicator(frame, "led_cool", bbox=off_box, baseline=baseline)["on"] is False


def main():
    tests = [
        test_clean_reply,
        test_parse_inkbird,
        test_matches_expectation,
        test_padded_box_clamps,
        test_vote_agree,
        test_vote_two_of_three,
        test_vote_all_differ,
        test_vote_out_of_range,
        test_clean_timer_reply,
        test_parse_timer,
        test_row_box,
        test_vote_timer,
        test_skip_led_slits,
        test_timer_calibration_file,
        test_find_lcd_rows,
        test_suggest_timer_buttons,
        test_read_indicator,
    ]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
