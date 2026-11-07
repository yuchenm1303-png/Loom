import re
from fractions import Fraction

TOKEN = re.compile(r"\s*(\d+\.\d+|\d+|[-+*/^()])")


def _tokens(text):
    at, out = 0, []
    text = text.rstrip()
    while at < len(text):
        found = TOKEN.match(text, at)
        if not found:
            raise ValueError(text)
        out.append(found[1])
        at = found.end()
    return out


def evaluate(text):
    tokens, at = _tokens(text), [0]

    def peek():
        return tokens[at[0]] if at[0] < len(tokens) else None

    def take():
        at[0] += 1
        return tokens[at[0] - 1]

    def atom():
        token = peek()
        if token is None:
            raise ValueError(text)
        take()
        if token == "(":
            value = add()
            if peek() != ")":
                raise ValueError(text)
            take()
            return value
        if token[0].isdigit():
            return Fraction(token)
        raise ValueError(text)

    def power():
        base = atom()
        if peek() == "^":
            take()
            exponent = unary()
            if exponent.denominator != 1:
                raise ValueError(text)
            return base ** int(exponent)
        return base

    def unary():
        if peek() == "-":
            take()
            return -unary()
        return power()

    def mul():
        value = unary()
        while peek() in ("*", "/"):
            value = value * unary() if take() == "*" else value / unary()
        return value

    def add():
        value = mul()
        while peek() in ("+", "-"):
            value = value + mul() if take() == "+" else value - mul()
        return value

    value = add()
    if peek() is not None:
        raise ValueError(text)
    return value
