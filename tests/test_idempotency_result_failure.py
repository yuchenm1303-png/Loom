import threading

import pytest

from app.remote_control.idempotency import IdempotencyStore


def test_a_completed_write_with_an_uncopyable_result_neither_hangs_nor_runs_twice():
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    store = IdempotencyStore()
    calls, errors = [], []

    class Uncopyable:
        def __deepcopy__(self, memo):
            raise ValueError("response cannot be copied")

    def action():
        calls.append("write")
        entered.set()
        assert release.wait(5)
        return Uncopyable()

    def owner():
        try:
            store.run("key", "write", action)
        except ValueError as exc:
            errors.append(str(exc))

    def duplicate():
        try:
            store.run("key", "write", action)
        except ValueError as exc:
            errors.append(str(exc))
        finally:
            done.set()

    first = threading.Thread(target=owner, daemon=True)
    second = threading.Thread(target=duplicate, daemon=True)
    first.start()
    assert entered.wait(5)
    second.start()
    release.set()
    assert done.wait(5), "response serialization failure must release duplicate waiters"
    first.join(5)
    second.join(5)
    assert errors == ["response cannot be copied"] * 2
    with pytest.raises(ValueError, match="cannot be copied"):
        store.run("key", "write", action)
    assert calls == ["write"]
