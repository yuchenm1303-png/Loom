from __future__ import annotations

from app.app_server_client import LoomAppServerClient


class RecordingClient(LoomAppServerClient):
    def __init__(self) -> None:
        super().__init__(["unused"])
        self.calls = []

    def request(self, method, params=None, *, timeout_seconds=None):
        self.calls.append((method, params or {}))
        return {"accepted": True}


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
