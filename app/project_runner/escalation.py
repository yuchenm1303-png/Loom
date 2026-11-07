"""How much checking a project needs: one independent review always, more only where there is a reason.

Stored runs (142 runs, 2026-10-28 to 11-04): review was 55% of all tokens. A team task's own review cost 34M for
92 assessments and found 3 problems; the merged result's review cost 33M for 12 and found 2. The whole-project
review re-verifies every criterion of every task anyway, so a task's own review mostly checks the same thing twice
(docs/goal-lines-design-2026-11-07.md).

So the floor is that one whole-project review, which no level removes: it is what delivery and integration require.
A task's own review is added by what the Host can see for itself, never by the worker's word: a person asked for it,
a review has already rejected something in this run, the task declares a shared resource, the change touches
something sensitive, or it is large. A waived task can only reach the aggregate; delivery refuses it anywhere else.
"""
from __future__ import annotations

import re

from .review import git

LEVELS = ("auto", "thorough")
MAX_FILES = 10
MAX_LINES = 400
SENSITIVE = re.compile(
    r"(^|/)(\.github|\.gitlab|\.circleci|deploy|deployment|infra|terraform|migrations?|secrets?|credentials?)(/|$)"
    r"|(^|/)(dockerfile|docker-compose[^/]*|makefile)$"
    r"|(auth|login|password|passwd|token|credential|secret|crypto|payment|billing|sandbox|permission|keyring)[^/]*(/|$)"
    r"|\.(pem|key|sql|tf|ps1|sh|bat)$", re.IGNORECASE)


def _changed(source):
    """What the worker changed in its checkout: paths and lines, from Git rather than from its report."""
    paths, lines = [], 0
    for row in git(source, "diff", "--numstat", "HEAD").splitlines():
        added, removed, path = row.split("\t", 2)
        paths.append(path)
        lines += sum(int(value) for value in (added, removed) if value.isdigit())
    for path in filter(None, git(source, "ls-files", "--others", "--exclude-standard", "-z", raw=True).split("\0")):
        paths.append(path)
        try:
            with (source / path).open("rb") as handle:
                lines += handle.read(4 * 1024 * 1024).count(b"\n")
        except OSError:
            lines += MAX_LINES  # cannot be read: not something to wave through
    return paths, lines


def reason(run, source):
    """Why this candidate gets its own independent review; '' when the whole-project review is enough."""
    if run.get("reviewLevel") != "auto":
        return "asked for"
    if run.get("reviewFailures"):
        return "a review rejected work in this run"
    task = run["plan"][run["taskIndex"]]
    if task.get("sharedResources"):
        return "declares a shared resource"
    paths, lines = _changed(source)
    risky = next((path for path in paths if SENSITIVE.search(path)), None)
    if risky:
        return f"touches {risky}"
    if len(paths) > MAX_FILES:
        return f"{len(paths)} files changed"
    if lines > MAX_LINES:
        return f"{lines} lines changed"
    return ""


def waivable(run):
    """Only a review that is not the floor: a team task's own, or one before the last task of a serial run."""
    return run.get("reviewLevel") == "auto" and (bool(run.get("teamOwnerId")) or run["taskIndex"] < len(run["plan"]) - 1)
