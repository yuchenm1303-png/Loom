def page(items, number, size):
    if number < 1 or size < 1:
        raise ValueError("number and size start at 1")
    return items[(number - 1) * size:number * size]


def pages(total, size):
    if size < 1:
        raise ValueError("size starts at 1")
    return (total + size - 1) // size
