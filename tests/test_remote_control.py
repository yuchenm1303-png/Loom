from __future__ import annotations

import threading

import pytest

from app.remote_control import RemoteControlClient, RemoteControlError, approval_fingerprint


class FakeBackend:
    def __init__(self) -> None:
        self.started_threads = []
        self.started_turns = []
        self.steers = []
        self.interrupts = []
        self.approvals = []
        self.threads = {
            "approval-thread": {
                "thread": {
                    "id": "approval-thread",
                    "permissionMode": "approval",
                    "status": "idle",
                    "currentTurnId": "turn-old",
                },
                "pendingApproval": None,
            },
            "full-thread": {
                "thread": {
                    "id": "full-thread",
                    "permissionMode": "full-access",
                    "status": "idle",
                    "currentTurnId": "turn-full",
                },
                "pendingApproval": None,
            },
        }

    def runtime_status(self):
        return {"model": "fake", "activeThreadIds": []}

    def project_list(self):
        return {"projects": [{"id": "project-1", "name": "Loom"}]}

    def thread_list(self, *, limit=100):
        return {"threads": [value["thread"] for value in self.threads.values()][:limit]}

    def thread_read(self, thread_id):
        return self.threads[thread_id]

    def thread_start(self, *, workspace=None, project_id="", permission_mode=None):
        assert workspace is None
        self.started_threads.append((project_id, permission_mode))
        thread_id = f"new-{len(self.started_threads)}"
        record = {
            "id": thread_id,
            "permissionMode": permission_mode,
            "status": "idle",
            "currentTurnId": None,
        }
        self.threads[thread_id] = {"thread": record, "pendingApproval": None}
        return {"thread": record}

    def turn_start(self, thread_id, text, attachments=()):
        self.started_turns.append((thread_id, text))
        turn = {"id": f"turn-{len(self.started_turns)}", "status": "starting"}
        self.threads[thread_id]["thread"]["currentTurnId"] = turn["id"]
        return {"turn": turn}

    def turn_steer(self, thread_id, turn_id, text, *, client_input_id=""):
        self.steers.append((thread_id, turn_id, text, client_input_id))
        return {"threadId": thread_id, "turnId": turn_id, "accepted": True}

    def turn_interrupt(self, thread_id, turn_id):
        self.interrupts.append((thread_id, turn_id))
        return {"threadId": thread_id, "turnId": turn_id, "requested": True}

    def approval_respond(
        self,
        thread_id,
        *,
        turn_id,
        request_id,
        call_id,
        decision,
    ):
        self.approvals.append(
            (thread_id, turn_id, request_id, call_id, decision)
        )
        return {
            "accepted": True,
            "threadId": thread_id,
            "turnId": turn_id,
            "requestId": request_id,
            "callId": call_id,
            "decision": decision,
        }


def test_remote_start_uses_approval_ceiling_and_replays_idempotently():
    backend = FakeBackend()
    remote = RemoteControlClient(backend)

    first = remote.task_start(
        prompt="run the tests",
        project_id="project-1",
        idempotency_key="start-1",
    )
    second = remote.task_start(
        prompt="run the tests",
        project_id="project-1",
        idempotency_key="start-1",
    )

    assert backend.started_threads == [("project-1", "approval")]
    assert backend.started_turns == [(first["threadId"], "run the tests")]
    assert first["idempotentReplay"] is False
    assert second["idempotentReplay"] is True
    assert second["threadId"] == first["threadId"]


def test_idempotency_key_cannot_silently_replay_a_different_request():
    remote = RemoteControlClient(FakeBackend())
    remote.task_start(
        prompt="first request",
        project_id="project-1",
        idempotency_key="same-key",
    )

    with pytest.raises(RemoteControlError) as exc_info:
        remote.task_start(
            prompt="different request",
            project_id="project-1",
            idempotency_key="same-key",
        )

    assert exc_info.value.code == "invalid_request"


def test_remote_cannot_steer_thread_above_channel_permission_ceiling():
    remote = RemoteControlClient(FakeBackend())

    with pytest.raises(RemoteControlError) as exc_info:
        remote.task_steer(
            thread_id="full-thread",
            turn_id="turn-full",
            input_text="make another change",
        )

    assert exc_info.value.code == "permission_denied"


def test_stop_is_available_even_for_broader_thread_because_it_only_reduces_authority():
    backend = FakeBackend()
    remote = RemoteControlClient(backend)

    result = remote.task_stop(thread_id="full-thread", turn_id="turn-full")

    assert result["ok"] is True
    assert backend.interrupts == [("full-thread", "turn-full")]


def test_approval_fingerprint_fails_closed_when_request_changes():
    backend = FakeBackend()
    pending = {
        "turnId": "turn-approval",
        "requestId": "request-1",
        "callId": "call-1",
        "toolName": "exec",
        "arguments": {"command": "git push"},
        "approvalStage": "initial",
    }
    backend.threads["approval-thread"]["pendingApproval"] = pending
    backend.threads["approval-thread"]["thread"]["status"] = "waiting_approval"
    backend.threads["approval-thread"]["thread"]["currentTurnId"] = "turn-approval"
    remote = RemoteControlClient(backend)

    fingerprint = approval_fingerprint("approval-thread", pending)
    pending["arguments"] = {"command": "git push --force"}

    with pytest.raises(RemoteControlError) as exc_info:
        remote.approval_respond(
            thread_id="approval-thread",
            fingerprint=fingerprint,
            decision="accept",
        )

    assert exc_info.value.code == "stale_approval"
    assert backend.approvals == []


def test_exact_pending_approval_can_be_responded_to_and_replayed():
    backend = FakeBackend()
    pending = {
        "turnId": "turn-approval",
        "requestId": "request-1",
        "callId": "call-1",
        "toolName": "exec",
        "arguments": {"command": "git push"},
        "approvalStage": "initial",
    }
    backend.threads["approval-thread"]["pendingApproval"] = pending
    backend.threads["approval-thread"]["thread"]["status"] = "waiting_approval"
    backend.threads["approval-thread"]["thread"]["currentTurnId"] = "turn-approval"
    remote = RemoteControlClient(backend)

    fingerprint = approval_fingerprint("approval-thread", pending)
    first = remote.approval_respond(
        thread_id="approval-thread",
        fingerprint=fingerprint,
        decision="decline",
    )
    second = remote.approval_respond(
        thread_id="approval-thread",
        fingerprint=fingerprint,
        decision="decline",
    )

    assert first["idempotentReplay"] is False
    assert second["idempotentReplay"] is True
    assert backend.approvals == [
        ("approval-thread", "turn-approval", "request-1", "call-1", "decline")
    ]


def test_idempotency_store_coalesces_concurrent_duplicate_start():
    backend = FakeBackend()
    original = backend.turn_start
    entered = threading.Event()
    release = threading.Event()

    def blocking_turn_start(thread_id, text, attachments=()):
        entered.set()
        assert release.wait(2)
        return original(thread_id, text, attachments)

    backend.turn_start = blocking_turn_start
    remote = RemoteControlClient(backend)
    results = []

    def invoke():
        results.append(
            remote.task_start(
                prompt="do it once",
                project_id="project-1",
                idempotency_key="same",
            )
        )

    first = threading.Thread(target=invoke)
    second = threading.Thread(target=invoke)
    first.start()
    assert entered.wait(2)
    second.start()
    release.set()
    first.join(2)
    second.join(2)

    assert len(backend.started_threads) == 1
    assert len(backend.started_turns) == 1
    assert sorted(result["idempotentReplay"] for result in results) == [False, True]
