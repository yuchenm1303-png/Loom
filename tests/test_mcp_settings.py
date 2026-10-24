import json
from types import SimpleNamespace

import pytest

from app import mcp_credentials
from app.agent_runtime.mcp_runtime import MCPClientManager, MCPServerConfig
from app.mcp_settings import MCPSettings, load_managed, parse_server


@pytest.fixture
def configured(tmp_path):
    from mcp.server import MCPServer
    server = MCPServer('Settings integration')

    @server.tool()
    def echo(text: str) -> str:
        return text

    runtime = SimpleNamespace(mcp_managed_path=tmp_path / 'mcp-servers.json',
                              mcp_external_configs=(),
                              mcp_clients=MCPClientManager((), target_factory=lambda config: server))
    yield runtime, MCPSettings(runtime)
    runtime.mcp_clients.close()


def definition(**updates):
    return dict(name='demo', transport='stdio', command='unused', **updates)


def test_save_toggle_reconnect_delete_updates_live_binding_and_disk(configured):
    runtime, settings = configured
    result = settings.manage({'action': 'save', 'server': definition()})
    assert result['connected_servers'] == 1
    assert result['remote_tool_count'] == 1
    assert result['tools'][0]['name'] == 'echo'
    assert load_managed(settings.path)[0].name == 'demo'
    original = runtime.mcp_clients.capture_binding()
    result = settings.manage({'action': 'toggle', 'name': 'demo'})
    assert result['connected_servers'] == 0
    assert result['tools'] == []
    assert runtime.mcp_clients.capture_binding().tools() == ()
    assert not load_managed(settings.path)[0].enabled
    settings.manage({'action': 'toggle', 'name': 'demo'})
    assert runtime.mcp_clients.capture_binding() is not original
    assert settings.manage({'action': 'reconnect', 'name': 'demo'})['connected_servers'] == 1
    assert settings.manage({'action': 'delete', 'name': 'demo'})['servers'] == []
    assert load_managed(settings.path) == ()


@pytest.mark.parametrize('updates', [
    {'name': '../bad'}, {'args': 'bad'}, {'args': [4]},
    {'env_from': {'KEY': 'secret-with-punctuation!'}},
    {'bearer_token_env': 'literal-token!'}, {'enabled': 'yes'},
    {'timeout_seconds': float('nan')}, {'timeout_seconds': 121},
    {'transport': 'http', 'url': 'http://localhost.evil/mcp'},
    {'transport': 'http', 'url': 'https://user:pass@example.com/mcp'},
    {'transport': 'http', 'url': 'https://example.com/mcp?token=secret'},
    {'headers': {'Authorization': 'secret'}},
])
def test_invalid_configuration_rejected(updates):
    raw = definition()
    raw.update(updates)
    with pytest.raises((ValueError, TypeError)):
        parse_server(raw)


def test_failed_persistence_keeps_current_manager(configured, monkeypatch):
    runtime, settings = configured
    old = runtime.mcp_clients
    import app.mcp_settings as module
    monkeypatch.setattr(module.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('disk failure')))
    with pytest.raises(OSError):
        settings.manage({'action': 'save', 'server': definition()})
    assert runtime.mcp_clients is old
    assert not settings.path.exists()


def test_external_servers_are_read_only(configured):
    runtime, settings = configured
    runtime.mcp_external_configs = (MCPServerConfig(name='external', transport='stdio', command='unused'),)
    assert not settings.snapshot()['servers'][0]['editable']
    with pytest.raises(ValueError, match='external'):
        settings.manage({'action': 'save', 'server': {**definition(), 'name': 'external'}})
    with pytest.raises(ValueError, match='not found'):
        settings.manage({'action': 'delete', 'name': 'external'})


def test_credentials_never_written_or_returned(configured, monkeypatch):
    runtime, settings = configured
    secrets = {}
    monkeypatch.setattr(mcp_credentials, 'resolve', secrets.get)
    monkeypatch.setattr(mcp_credentials, 'write', secrets.__setitem__)
    monkeypatch.setattr(mcp_credentials, 'delete', lambda name: secrets.pop(name, None))
    raw = {**definition(), 'transport': 'http', 'url': 'https://example.com/mcp'}
    result = settings.manage({'action': 'save', 'server': raw, 'token': 'private-token-123'})
    assert 'private-token-123' not in json.dumps(result)
    assert 'private-token-123' not in settings.path.read_text()
    assert list(secrets.values()) == ['private-token-123']
    settings.manage({'action': 'delete', 'name': 'demo'})
    assert not secrets


def test_unreachable_server_is_saved_with_actionable_status(configured):
    runtime, settings = configured
    runtime.mcp_clients._target_factory = None
    result = settings.manage({'action': 'save', 'server': {**definition(), 'command': 'loom-does-not-exist-123', 'timeout_seconds': 1}})
    assert result['connected_servers'] == 0
    assert result['servers'][0]['error']
    assert load_managed(settings.path)[0].name == 'demo'


def test_service_refuses_changes_during_turn(configured):
    from app.app_server import LoomAppServerService
    import threading
    runtime, settings = configured
    service = SimpleNamespace(runtime=runtime, _guard=threading.RLock(), _active_sessions={'active'})
    with pytest.raises(RuntimeError, match='active turns'):
        LoomAppServerService.mcp_manage(service, {'action': 'save', 'server': definition()})
    assert not settings.path.exists()


def test_runtime_restores_managed_servers_after_restart(tmp_path):
    from app.agent_runtime import ConfiguredMCPRuntime, FileAgentSessionStore, ToolRegistry
    from app.ai import AGENT_FAST_ROLE
    root = tmp_path / 'state'
    root.mkdir()
    config = root / 'config.toml'
    config.write_text('[mcp_servers]\n', encoding='utf-8')
    kwargs = dict(platform=object(), store=FileAgentSessionStore(root), tools=ToolRegistry(),
                  mcp_servers=(), mcp_config_path=config, auto_configure_computer=False,
                  auto_configure_browser=False, auto_configure_web_search=False,
                  auto_connect_mcp=False)
    runtime = ConfiguredMCPRuntime(**kwargs)
    try:
        settings = MCPSettings(runtime)
        settings.manage({'action': 'save', 'server': definition(enabled=False)})
        assert settings.path.parent == root
    finally:
        runtime.close()
    kwargs['tools'] = ToolRegistry()
    restored = ConfiguredMCPRuntime(**kwargs)
    try:
        snapshot = MCPSettings(restored).snapshot()
        assert snapshot['servers'][0]['name'] == 'demo'
        assert snapshot['servers'][0]['enabled'] is False
        session = restored.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path)
        step = restored._build_step_context(session, next_model_step=True)
        assert not any(t.name == 'mcp.demo.echo' for t in step.tool_router.all())
    finally:
        restored.close()


def test_credential_store_failure_does_not_save_configuration(configured, monkeypatch):
    runtime, settings = configured
    def unavailable(*args):
        raise RuntimeError('Credential store unavailable')
    monkeypatch.setattr(mcp_credentials, 'resolve', lambda name: None)
    monkeypatch.setattr(mcp_credentials, 'write', unavailable)
    old = runtime.mcp_clients
    with pytest.raises(RuntimeError, match='Credential store'):
        settings.manage({'action': 'save', 'server': {**definition(), 'transport': 'http', 'url': 'https://example.com/mcp'}, 'token': 'private'})
    assert runtime.mcp_clients is old
    assert not settings.path.exists()


def test_canonical_server_name_collision_is_rejected(configured):
    _, settings = configured
    settings.manage({'action': 'save', 'server': definition(enabled=False)})
    with pytest.raises(ValueError, match='conflicting tool names'):
        settings.manage({'action': 'save', 'server': {**definition(enabled=False), 'name': 'demo.'}})


def test_managed_tools_enter_and_leave_next_step(tmp_path):
    from mcp.server import MCPServer
    from app.agent_runtime import ConfiguredMCPRuntime, FileAgentSessionStore, ToolRegistry
    from app.ai import AGENT_FAST_ROLE
    server = MCPServer('Step lifecycle')

    @server.tool()
    def echo(text: str) -> str:
        return text

    runtime = ConfiguredMCPRuntime(
        platform=object(), store=FileAgentSessionStore(tmp_path), tools=ToolRegistry(),
        mcp_servers=(), mcp_target_factory=lambda config: server,
        auto_configure_computer=False, auto_configure_browser=False,
        auto_configure_web_search=False,
    )
    try:
        session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path)
        settings = MCPSettings(runtime)
        settings.manage({'action': 'save', 'server': definition()})
        step = runtime._build_step_context(session, next_model_step=True)
        assert any(t.name == 'mcp.demo.echo' for t in step.tool_router.all())
        settings.manage({'action': 'delete', 'name': 'demo'})
        next_step = runtime._build_step_context(session, next_model_step=True)
        assert not any(t.name == 'mcp.demo.echo' for t in next_step.tool_router.all())
    finally:
        runtime.close()
