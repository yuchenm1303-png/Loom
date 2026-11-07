import re
from fractions import Fraction

FACTOR = {"g": 1, "kg": 1000, "t": 1000000}


def convert(value, frm, to):
    if frm not in FACTOR or to not in FACTOR:
        raise ValueError(f"{frm} {to}")
    return Fraction(value) * FACTOR[frm] / FACTOR[to]


def parse(text):
    found = re.fullmatch(r"\s*([-\d./]+)\s*([a-z]+)\s*", text)
    if not found or found[2] not in FACTOR:
        raise ValueError(text)
    return Fraction(found[1]), found[2]
