def _half_up(numerator, denominator):
    return (2 * numerator + denominator) // (2 * denominator)


def discount(cents, percent):
    return _half_up(cents * (100 - percent), 100)


def bulk(unit, qty):
    line = unit * qty
    if qty >= 100:
        return discount(line, 20)
    if qty >= 10:
        return discount(line, 10)
    return line


def tax(cents, rate_percent):
    return _half_up(cents * rate_percent, 100)
