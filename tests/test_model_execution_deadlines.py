"""What bounds a model request, and why it is not one number any more.

Reconstructed from a real session. deepseek-flash at max reasoning effort was
emitting 22,000-29,600 output tokens per response while the visible answer was
115-255 characters, i.e. almost all of it reasoning. Generation held steady at
about 195 tokens/second regardless of context size - response time correlated
with output volume at r=+1.00 and with context length at r=-0.10 - so the flat
150 second deadline was really a ceiling of roughly 29,000 output tokens. Past
that the turn could not succeed however long anyone waited, and two turns died
with "model request deadline exceeded" while the UI simply sat there.

The reason nothing downstream could tell that apart from a hang: reasoning
deltas are accumulated for the end of the turn and never become StreamEvents,
so a model thinking for two minutes looks exactly like a dead socket to
everything above the provider chunk loop.
"""

from __future__ import annotations

import time

import pytest

from app.agent_runtime.model_execution import ModelExecutor
from app.ai.execution_control import note_progress


class _Token:
    cancelled = False
    steering_revision = 0


class _Platform:
    """A backend that reports progress per chunk, like the provider loop does."""

    def __init__(self, chunks: int, gap: float, trailing_silence: float = 0.0) -> None:
        self.chunks = chunks
        self.gap = gap
        self.trailing_silence = trailing_silence

    def execute_chat(self, profile_id, request):
        for _ in range(self.chunks):
            time.sleep(self.gap)
            note_progress()
        time.sleep(self.trailing_silence)
        return "completed"


def _executor(*, max_duration: float = 8.0) -> ModelExecutor:
    """Scaled-down bounds, with room for a loaded machine.

    The gap between chunks has to stay far below stall_timeout or these tests
    start failing on CPU contention rather than on behaviour: at 0.2s against a
    1.5s stall window there is 7x of slack, where an earlier 0.5s gap left only
    3x and duly went red under the full suite.
    """

    return ModelExecutor(timeout=2.0, stall_timeout=1.5, max_duration=max_duration)


def test_a_long_response_that_keeps_streaming_is_not_killed():
    """The bug this whole file exists for.

    The response is healthy and simply long: it streams for three times the
    first-token deadline and must finish. Under the old flat deadline this is
    precisely the turn that died.
    """

    executor = _executor()
    started = time.monotonic()
    result = executor.execute(_Platform(chunks=15, gap=0.2), "p", {}, _Token())
    elapsed = time.monotonic() - started

    assert result == "completed"
    assert elapsed > executor.timeout, "the test did not actually outlive the old deadline"


def test_a_request_that_produces_nothing_keeps_the_old_deadline():
    """Unchanged on purpose.

    A backend with no streaming path never reports progress at all, so the
    first-token deadline has to keep meaning what it meant before or every
    non-streaming request would be judged stalled from the moment it started.
    """

    executor = _executor()
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="model request deadline exceeded"):
        executor.execute(_Platform(chunks=0, gap=0, trailing_silence=30), "p", {}, _Token())
    elapsed = time.monotonic() - started

    assert executor.timeout <= elapsed < executor.timeout + 3.0


def test_a_stream_that_dies_mid_response_is_caught_sooner_than_before():
    """A real hang is now detected faster, not slower.

    The point of the change is not patience for its own sake: it is asking the
    right question. "Nothing for 60 seconds" catches a dead stream well inside
    the 150 seconds it used to take.
    """

    executor = _executor()
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="stopped producing output"):
        executor.execute(_Platform(chunks=3, gap=0.2, trailing_silence=30), "p", {}, _Token())
    elapsed = time.monotonic() - started

    # Last chunk at ~0.6s, so the stall fires at ~2.1s - sooner than a flat wait
    # for max_duration, and it says the stream stopped rather than blaming a
    # deadline the request never had a chance to meet.
    assert elapsed < executor.max_duration


def test_a_runaway_that_never_stops_still_hits_a_ceiling():
    """Progress must not be a licence to stream forever."""

    executor = _executor(max_duration=3.0)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="maximum duration"):
        executor.execute(_Platform(chunks=10_000, gap=0.05), "p", {}, _Token())
    elapsed = time.monotonic() - started

    assert elapsed >= executor.max_duration


def test_the_three_failures_do_not_share_one_message():
    """Each says which of the three actually happened.

    "model request deadline exceeded" was returned for a dead connection, a
    slow page of thinking and a runaway alike, so the log could not tell anyone
    which had occurred - including, in the session this came from, that the
    answer was "none of the above, it was still working".
    """

    executor = _executor(max_duration=3.0)
    messages = []
    for platform in (
        _Platform(chunks=0, gap=0, trailing_silence=30),
        _Platform(chunks=3, gap=0.2, trailing_silence=30),
        _Platform(chunks=10_000, gap=0.05),
    ):
        with pytest.raises(TimeoutError) as caught:
            executor.execute(platform, "p", {}, _Token())
        messages.append(str(caught.value))

    assert len(set(messages)) == 3


def test_progress_is_recorded_per_provider_chunk_not_per_public_event():
    """Reasoning-only chunks have to count, or thinking looks like hanging.

    openai_streaming yields no StreamEvent for a reasoning delta - it
    accumulates the text and moves on - so the progress hook lives in the raw
    chunk loop. If it ever moves up to the event layer, a reasoning model at
    high effort goes back to being indistinguishable from a dead stream.
    """

    from pathlib import Path

    source = Path("app/ai/openai_streaming.py").read_text(encoding="utf-8")
    chunk_loop = source.index("for chunk in stream:")
    first_yield = source.index("yield StreamEvent", chunk_loop)
    assert "note_progress()" in source[chunk_loop:first_yield], (
        "progress must be recorded before any StreamEvent is produced, or "
        "reasoning-only responses report no progress at all"
    )
