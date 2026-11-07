from datetime import timedelta

RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def _field(text, low, high):
    values = set()
    for part in text.split(","):
        body, _, step = part.partition("/")
        if body == "*":
            start, end = low, high
        elif "-" in body:
            start, end = map(int, body.split("-"))
        else:
            start = end = int(body)
        if start < low or end > high:
            raise ValueError(text)
        values.update(range(start, end + 1, int(step or 1)))
    return values


def next_run(expr, after):
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(expr)
    minutes, hours, days, months, weekdays = (_field(part, *span) for part, span in zip(parts, RANGES))
    both = parts[2] != "*" and parts[4] != "*"
    at = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60 * 9):
        weekday = (at.weekday() + 1) % 7
        day = (at.day in days or weekday in weekdays) if both else (at.day in days and weekday in weekdays)
        if at.month in months and day and at.hour in hours and at.minute in minutes:
            return at
        at += timedelta(minutes=1)
    raise ValueError(expr)
