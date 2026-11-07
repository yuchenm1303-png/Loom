import re


def _has(item, word):
    return re.search(rf"\b{re.escape(word)}\b", item, re.IGNORECASE) is not None


def find(items, word):
    return [item for item in items if _has(item, word)]


def rank(items, words):
    distinct = {word.lower() for word in words}
    return sorted(items, key=lambda item: -sum(_has(item, word) for word in distinct))
