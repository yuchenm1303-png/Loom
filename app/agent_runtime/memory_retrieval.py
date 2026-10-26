"""Local, explainable retrieval without network/model work on the request path.

BM25 handles identifiers and uncommon terms; bilingual concepts bridge common
project vocabulary. This is deliberately not an embedding model.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_WORDS = re.compile(r"[a-z0-9_]+(?:[.\-/][a-z0-9_]+)*|[\u4e00-\u9fff]+", re.I)
_STOP = frozenset("the a an is are was were to of for in on at and or with this that it uses user project loom com https http please code look 代码 看看 我们 项目 之前".split())
_CONCEPTS = (
    ("homepage", "home page", "landing", "首页", "主页"),
    ("editorial", "编辑式", "编辑风格", "出版物"),
    ("redesign", "改版", "改造", "重新设计"),
    ("frontend", "front-end", "前端"),
    ("backend", "back-end", "后端"),
    ("deploy", "deployment", "上线", "部署"),
    ("domain", "域名"),
    ("font", "字体"),
    ("login", "登录"),
    ("memory", "记忆"),
    ("layout", "布局", "版式"),
    ("portal", "门户"),
    ("test", "tests", "测试"),
)


def terms(text: str) -> list[str]:
    result: list[str] = []
    for match in _WORDS.finditer(text.casefold()):
        word = match.group()
        if "\u4e00" <= word[0] <= "\u9fff":
            # Single Han characters caused spurious matches on ordinary prose.
            result.extend(word[i:i + 2] for i in range(len(word) - 1) if word[i:i + 2] not in _STOP)
        elif len(word) > 1 and word not in _STOP:
            result.append(word)
            # Keep a domain/path as one identifier, rather than double-counting
            # its host fragments and drowning out the actual task vocabulary.
    normalized = text.casefold()
    for index, aliases in enumerate(_CONCEPTS):
        if any((alias in normalized if not alias.isascii() or " " in alias
                else re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", normalized))
               for alias in aliases):
            result.append(f"concept:{index}")
    return result


def rank(query: str, documents: list[str]) -> list[tuple[float, tuple[str, ...]]]:
    """Return corpus-aware scores in input order; zero means no evidence match."""
    query_terms = set(terms(query))
    bodies = [Counter(terms(body)) for body in documents]
    frequency = Counter(term for body in bodies for term in body)
    average = sum(sum(body.values()) for body in bodies) / max(1, len(bodies)) or 1
    scores = []
    for body in bodies:
        overlap = query_terms.intersection(body)
        score = 0.0
        for term in overlap:
            idf = math.log(1 + (len(bodies) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            count = body[term]
            weight = 1.6 if term.startswith("concept:") else 1.0
            score += weight * idf * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * sum(body.values()) / average))
        reasons = (f"bm25_terms:{len(overlap)}",) if overlap else ()
        if any(term.startswith("concept:") for term in overlap):
            reasons += ("bilingual_concept",)
        scores.append((score, reasons))
    return scores
