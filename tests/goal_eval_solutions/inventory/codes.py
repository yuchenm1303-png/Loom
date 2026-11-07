import re


def valid(code):
    return re.fullmatch(r"[A-Z]{2}-\d{4}", code) is not None


def normalize(text):
    text = text.strip().upper()
    return text[:2] + "-" + text[2:] if "-" not in text else text


def check_digit(code):
    return sum(int(ch) for ch in code if ch.isdigit()) % 10
