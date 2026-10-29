#!/usr/bin/env python3
"""Tests for read_screen.py that don't hit the API. Plain asserts.

    python vision/test_read_screen.py

The LED images are made here in code and deleted afterwards, nothing gets committed.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from read_screen import (  # noqa: E402
    clean_reply,
    matches_expectation,
    padded_box,
    parse_inkbird,
    read_indicator,
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
    assert parse_inkbird("HELLO 42") == {}  # unknown text stays only in "text"
    assert parse_inkbird("") == {}


def test_matches_expectation():
    assert matches_expectation("TS 80.0", "ts80")
    assert matches_expectation("ts80", "TS 80.0")
    assert matches_expectation("SV 80", "SV 80.0")
    assert matches_expectation("SV 80.0", "SV 80")
    assert matches_expectation("PV 72.0 SV 80.0", "80")
    assert matches_expectation("80°F", "80 F")
    assert not matches_expectation("TS 79.0", "TS 80.0")
    assert not matches_expectation("TS 800", "TS 80")
    assert not matches_expectation("TS 80.5", "TS 80")
    assert not matches_expectation("SV 180", "80")
    assert not matches_expectation("", "TS")
    assert matches_expectation("anything", "")  # "" = nothing to check (indicator steps)


def test_padded_box_clamps():
    # 8% of 100 = 8 px each side
    assert padded_box([100, 100, 100, 50], 1000, 1000, 0.08) == (92, 96, 208, 154)
    # right at the corner, gets clamped to the frame
    assert padded_box([0, 0, 100, 50], 1000, 1000, 0.08) == (0, 0, 108, 54)
    assert padded_box([950, 980, 50, 20], 1000, 1000, 0.08) == (946, 978, 1000, 1000)


def test_read_indicator():
    from PIL import Image

    with tempfile.TemporaryDirectory() as folder:
        frame = Path(folder) / "frame.png"
        image = Image.new("RGB", (60, 20), (20, 20, 20))  # dark panel
        for x in range(20, 40):
            for y in range(20):
                image.putpixel((x, y), (230, 30, 30))  # bright red LED (HEAT) in the middle
        for x in range(40, 60):
            for y in range(20):
                image.putpixel((x, y), (30, 230, 30))  # bright green LED (COOL) on the right
        image.save(frame)

        dark = read_indicator(frame, [0, 0, 20, 20])
        assert dark["on"] is False, dark
        assert dark["brightness"] < 0.1, dark

        red = read_indicator(frame, [20, 0, 20, 20])
        assert red["on"] is True, red
        assert red["redness"] > 0.5, red
        assert red["greenness"] < 0, red

        green = read_indicator(frame, [40, 0, 20, 20])
        assert green["on"] is True, green
        assert green["greenness"] > 0.5, green
        assert green["redness"] < 0, green
        # it's the colour that turns it on here, not the brightness
        assert green["brightness"] < 0.6, green


def main():
    tests = [test_clean_reply, test_parse_inkbird, test_matches_expectation, test_padded_box_clamps, test_read_indicator]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
