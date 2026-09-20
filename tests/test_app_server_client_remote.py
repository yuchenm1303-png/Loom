from __future__ import annotations

from app.app_server_client import LoomAppServerClient


class RecordingClient(LoomAppServerClient):
    def __init__(self) -> None:
        super().__init__(["unused"])
        self.calls = []

    def request(self, method, params=None, *, timeout_seconds=None):
        self.calls.append((method, params or {}))
        return {"accepted": True}

    def thread_read(self, thread_id):
        return {
            "pendingApproval": {
                "turnId": "turn-legacy",
                "requestId": "request-legacy",
                "callId": "call-legacy",
            }
        }


def test_app_server_client_serializes_steer_and_correlated_approval():
    client = RecordingClient()

    client.turn_steer(
        "thread-1",
        "turn-1",
        "use the safer plan",
        client_input_id="steer-1",
    )
    client.approval_respond(
        "thread-1",
        turn_id="turn-1",
        request_id="request-1",
        call_id="call-1",
        decision="accept",
    )

    assert client.calls[0] == (
        "turn/steer",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "input": "use the safer plan",
            "clientInputId": "steer-1",
        },
    )
    assert client.calls[1] == (
        "approval/respond",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "requestId": "request-1",
            "callId": "call-1",
            "decision": "accept",
        },
    )


def test_app_server_client_preserves_legacy_approval_helper():
    client = RecordingClient()

    client.approval_respond("thread-legacy", "call-legacy", approved=False)

    assert client.calls == [
        (
            "approval/respond",
            {
                "threadId": "thread-legacy",
                "turnId": "turn-legacy",
                "requestId": "request-legacy",
                "callId": "call-legacy",
                "decision": "decline",
            },
        )
    ]


def test_app_server_client_serializes_durable_start_ids():
    client = RecordingClient()

    client.thread_start(
        project_id="project-1",
        permission_mode="approval",
        client_input_id="task-1",
    )
    client.turn_start(
        "thread-1",
        "hello",
        client_input_id="task-1",
    )

    assert client.calls == [
        (
            "thread/start",
            {
                "projectId": "project-1",
                "permissionMode": "approval",
                "clientInputId": "task-1",
            },
        ),
        (
            "turn/start",
            {
                "threadId": "thread-1",
                "input": "hello",
                "clientInputId": "task-1",
            },
        ),
    ]
