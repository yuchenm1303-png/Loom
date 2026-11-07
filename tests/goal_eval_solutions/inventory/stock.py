def remove(levels, sku, n):
    left = levels.get(sku, 0) - n
    if left < 0:
        raise ValueError(sku)
    if left == 0:
        levels.pop(sku, None)
    else:
        levels[sku] = left


def low(levels, limit):
    return sorted(sku for sku, level in levels.items() if level < limit)
