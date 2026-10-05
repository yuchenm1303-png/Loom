"""Validate Loom's explicit decision-card protocol, not ordinary answer prose.

The shared tests/fixtures/decision_protocol.json contract also exercises the
actual frontend parser in DecisionPromptCard.tsx.
"""
from __future__ import annotations

import json
import re


def _valid_decision(payload: object) -> bool:
    def text(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    if not isinstance(payload, dict) or not text(payload.get("title")):
        return False
    options = payload.get("options")
    if not isinstance(options, list) or not 2 <= len(options) <= 6:
        return False
    ids: set[str] = set()
    for option in options:
        if not isinstance(option, dict):
            return False
        key = text(option.get("id"))
        if not key or not text(option.get("title")) or key in ids:
            return False
        ids.add(key)
    return True


def invalid_decision_block(source: str) -> bool:
    """Honor Markdown fence nesting so quoted protocol examples remain prose."""
    fence = ""
    decision = False
    body: list[str] = []

    def invalid_json(value: str) -> bool:
        try:
            def reject_non_json_constant(value: str):
                raise ValueError(f"invalid JSON constant: {value}")
            payload = json.loads(value, parse_constant=reject_non_json_constant)
        except (ValueError, TypeError):
            return True
        return not _valid_decision(payload)

    for line in source.splitlines():
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*", line):
                if decision and invalid_json("\n".join(body)):
                    return True
                fence, decision, body = "", False, []
            elif decision:
                body.append(line)
            continue
        opening = re.fullmatch(r" {0,3}(`{3,}|~{3,})(.*)", line)
        if opening is None:
            continue
        fence, info = opening.groups()
        marker = re.match(r"loom-decision\b[ \t]*(.*)", info, re.IGNORECASE)
        decision = marker is not None and fence[0] == "`"
        body = []
        if decision:
            inline = marker.group(1)
            if inline.endswith(fence):
                if invalid_json(inline[:-len(fence)]):
                    return True
                fence, decision = "", False
            elif inline:
                body.append(inline)
    return bool(fence and decision)
