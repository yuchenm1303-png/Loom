"""What a long goal and its lines both need: what a run costs, how much time is left, how to wait out a provider."""
from __future__ import annotations

import time

from .budget import CACHED_WEIGHT

RETRY_BASE, RETRY_MAX = 60, 1800


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
