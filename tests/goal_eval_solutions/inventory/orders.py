MOVES = {"new": ["paid", "cancelled"], "paid": ["shipped", "cancelled"], "shipped": ["done"], "done": [], "cancelled": []}


def advance(state, to):
    if to in MOVES[state]:
        return to
    raise ValueError(to)


def total(lines):
    if any(qty <= 0 for qty, _ in lines):
        raise ValueError("qty must be above 0")
    return sum(qty * price for qty, price in lines)
