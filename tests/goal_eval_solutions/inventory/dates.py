import calendar
from datetime import date


def add_months(day, months):
    index = day.year * 12 + day.month - 1 + months
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def quarter(day):
    return (day.month - 1) // 3 + 1


def days_between(a, b):
    return abs((b - a).days)
