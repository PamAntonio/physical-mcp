#!/usr/bin/env python3
"""Tests for read_screen.py that don't hit the API. Plain asserts.

    python vision/test_read_screen.py

The LED images are made here in code and deleted afterwards, nothing gets committed.
Screen reads are stubbed, so the voting gets tested without calling Muse Spark.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from read_screen import (  # noqa: E402
    clean_reply,
    led_scores,
    matches_expectation,
    padded_box,
    parse_inkbird,
    read_indicator,
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
        test_read_indicator,
    ]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
