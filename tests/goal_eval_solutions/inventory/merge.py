def merge_counts(a, b):
    merged = dict(a)
    for key, value in b.items():
        merged[key] = merged.get(key, 0) + value
    return merged


def dedupe(items):
    return list(dict.fromkeys(items))


def chunks(items, n):
    return [items[at:at + n] for at in range(0, len(items), n)]
