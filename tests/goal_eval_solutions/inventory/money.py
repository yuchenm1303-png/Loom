import re


def to_cents(text):
    if not re.fullmatch(r"-?\d+(\.\d{1,2})?", text):
        raise ValueError(text)
    sign = -1 if text.startswith("-") else 1
    whole, _, part = text.lstrip("-").partition(".")
    return sign * (int(whole) * 100 + int(part.ljust(2, "0") or 0))


def from_cents(cents):
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"
