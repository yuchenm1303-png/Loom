"""Deterministic provider for execution lifecycle tests; no hidden review."""
class Scripted:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile_id, request):
        self.requests.append(request)
        item = self.responses.pop(0)
        if callable(item):
            return item(request)
        if isinstance(item, Exception):
            raise item
        return item


def runtime(tmp_path, responses, **kwargs):
    from app.agent_runtime import DurableAgentRuntime, FileAgentSessionStore
    platform = Scripted(responses)
    rt = DurableAgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"), **kwargs)
    session = rt.create_session("agent.fast", workspace_dir=tmp_path)
    return rt, session, platform
