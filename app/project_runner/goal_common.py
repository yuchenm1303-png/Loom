"""What a long goal and its lines both need: what a run costs, how much time is left, how to wait out a provider."""
from __future__ import annotations

import time

from app.agent_runtime.development_verification import read_only_command
from app.agent_runtime.tool_selection import WEAVE_PREFIX
from app.agent_runtime.turn_plan import PLAN_TOOLS

from .budget import CACHED_WEIGHT

RETRY_BASE, RETRY_MAX = 60, 1800
# The goal's own controls: writing the list, finding a tool, reading or marking the goal.
CONTROL_TOOLS = PLAN_TOOLS | {"tool_search", "get_thread_state", "mark_thread_goal"}


def changes(step, call):
    """Whether a call can change files, processes or outside state: the one question every build-mode gate asks.

    Each gate used to keep its own copy with its own exceptions, and all read the permission effect: web_search and
    the browser's page reads are SENSITIVE (they need permission, the request leaves the machine), so a real research
    goal had four literature searches refused before its charter. A tool says what it does with read_only (MCP's
    readOnlyHint); exec reads when its command provably cannot write."""
    if call.name in CONTROL_TOOLS or call.name.startswith(WEAVE_PREFIX):
        return False
    tool = step.tool_router.get(call.name) if step is not None else None
    if tool is None or call.name == "spawn_agent":
        return True
    if tool.reads_only:
        return False
    if call.name == "exec":
        return not read_only_command((call.arguments or {}).get("argv"))
    return True


def looks_up(tool):
    """A call that reads from outside this computer: the web, a remote service, an investigator that searches."""
    return tool is not None and tool.reads_only and tool.open_world


MAX_DECISIONS = 60


def decide(record, what, **values):
    """Record a threshold decision with the numbers it was made on: the thresholds (when to deepen, to hold a claim,
    to ask for a note or a split) were set without data, and are tuned from these (scripts/gate_audit.py)."""
    entry = {"at": round(time.time(), 1), "what": what,
             "tokens": record.get("tokens"), "budget": record.get("tokenBudget"), **values}
    record["decisions"] = [*(record.get("decisions") or ()), entry][-MAX_DECISIONS:]


def charged(usage):
    """What a usage record costs against a goal's budget."""
    return (usage.total_tokens - usage.cached_input_tokens) + usage.cached_input_tokens * CACHED_WEIGHT


def span(seconds):
    seconds = max(0, int(seconds))
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def time_left(record):
    hours = record.get("hours") or 0
    return hours * 3600 - (time.time() - record["startedAt"]) if hours else 0


def transient(data):
    """The provider failed and said trying again may work: capacity, not credentials or the request."""
    return bool(data.get("provider_failure") and data.get("retryable"))


def delay(failures):
    return min(RETRY_BASE * 2 ** (max(1, failures) - 1), RETRY_MAX)
