"""Pull the one number out of a screen reading, e.g. "TS 79.0" -> 79.0.

Used by the retry logic in api.py and by FakeHardware's undershoot.
Times like 02:00 don't count as numbers, and a reading with two numbers in it
("PV 72.0 SV 80.0") counts as no number, since we can't tell which one is meant.
"""

import re

# a plain decimal that isn't stuck to a letter, digit, colon or dot, so 02:00 and error
# codes like E1 don't count. something like 80.0F doesn't either, which just means a re-read
NUMBER_PATTERN = re.compile(r"(?<![\w.:])-?\d+(?:\.\d+)?(?![\w.:])")


def find_number(text):
    """(value, as_written) for the single number in text, or None."""
    if not text:
        return None
    matches = []
    for match in NUMBER_PATTERN.finditer(text):
        matches.append(match)
    if len(matches) != 1:
        return None
    as_written = matches[0].group(0)
    return float(as_written), as_written


def replace_number(text, value):
    """text with its single number swapped for value, same number of decimals.
    None if text doesn't have exactly one number."""
    found = find_number(text)
    if found is None:
        return None
    as_written = found[1]
    decimals = 0
    if "." in as_written:
        decimals = len(as_written.split(".")[1])
    return text.replace(as_written, f"{value:.{decimals}f}", 1)
