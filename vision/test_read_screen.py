#!/usr/bin/env python3
"""Tests for read_screen.py and the LED slit filter in detect_controls.py that don't hit the API. Plain asserts.

    python vision/test_read_screen.py

The LED images are made here in code and deleted afterwards, nothing gets committed.
Screen reads are stubbed, so the voting gets tested without calling Muse Spark.
"""

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import detect_controls  # noqa: E402
from read_screen import (  # noqa: E402
    DEVICE_SETTINGS,
    clean_reply,
    led_scores,
    matches_expectation,
    padded_box,
    parse_inkbird,
    parse_timer,
    read_indicator,
    row_box,
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
        test_read_indicator,
    ]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
