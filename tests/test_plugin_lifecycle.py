import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from app import mcp_credentials
from app.plugin_manager import PluginManager, PluginManagerError
from app.plugin_packages import inspect_package
from app.plugin_runtime import PluginLifecycle
from app.agent_runtime import FileAgentSessionStore, SkillRuntime, ToolRegistry, ToolContext, ToolEffect
from app.ai import AGENT_FAST_ROLE, ModelResponse, ToolCall


def source(root, *, name='demo', version='1.0.0', mcp=False, claude=False):
    root.mkdir(parents=True)
    manifest = root / '.claude-plugin' / 'plugin.json' if claude else root / 'loom-plugin.json'
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({'name': name, 'version': version, 'description': 'A review bundle'}), encoding='utf-8')
    skill = root / 'skills' / 'review'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: review\ndescription: Review source changes.\n---\nPLUGIN_REVIEW_BODY\n', encoding='utf-8')
    if mcp:
        (root / '.mcp.json').write_text(json.dumps({'mcpServers': {'echo': {'command': sys.executable, 'args': ['${LOOM_PLUGIN_ROOT}/server.py'], 'env': {'API_KEY': '${SERVICE_KEY}'}}}}), encoding='utf-8')
        (root / 'server.py').write_text('raise AssertionError("installation must not execute scripts")\n', encoding='utf-8')
    return root


class Platform:
    def __init__(self):
        self.responses = []
        self.requests = []
    def execute_chat(self, profile, request):
        self.requests.append(request)
        return self.responses.pop(0)


@pytest.fixture
def live(tmp_path):
    from mcp.server import MCPServer
    server = MCPServer('Plugin lifecycle test')
    @server.tool()
    def echo(text: str) -> str:
        return 'echo:' + text
    platform = Platform()
    runtime = SkillRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / 'state'), tools=ToolRegistry(),
                           skill_roots=(), mcp_servers=(), mcp_target_factory=lambda config: server,
                           auto_configure_computer=False, auto_configure_browser=False, auto_configure_web_search=False)
    lifecycle = PluginLifecycle(runtime, tmp_path / 'state')
    yield runtime, lifecycle, platform
    runtime.close()


def test_preview_install_live_tools_skills_disable_enable_remove(live, tmp_path):
    runtime, lifecycle, _ = live
    original = source(tmp_path / 'bundle', mcp=True)
    preview = lifecycle.manager.preview(str(original))
    assert lifecycle.snapshot()['plugins'] == []
    assert runtime.mcp_clients.status()['tool_count'] == 0
    assert preview['servers'][0]['args'][0].endswith('server.py')
    result = lifecycle.manage({'action': 'install', 'token': preview['token']})
    row = result['plugins'][0]
    assert row['status'] == 'Active' and row['activeSkills'] == 1
    assert row['servers'][0]['connected']
    skill = runtime.skill_manager.discover(tmp_path).get('plugin.demo.review')
    assert skill and runtime.skill_manager.load(skill) == 'PLUGIN_REVIEW_BODY'
    tool = runtime.mcp_clients.agent_tools()[0]
    assert tool.effect is ToolEffect.SENSITIVE
    assert 'echo:hello' in tool.handler(ToolContext(session_id='s', turn_id='t', workspace=tmp_path), {'text': 'hello'}).content
    lifecycle.manage({'action': 'disable', 'name': 'demo'})
    assert not runtime.mcp_clients.capture_binding().tools()
    assert runtime.skill_manager.discover(tmp_path).get('plugin.demo.review') is None
    lifecycle.manage({'action': 'enable', 'name': 'demo'})
    assert runtime.mcp_clients.status()['tool_count'] == 1
    assert runtime.skill_manager.discover(tmp_path).get('plugin.demo.review')
    lifecycle.manage({'action': 'remove', 'name': 'demo'})
    assert lifecycle.snapshot()['plugins'] == []
    assert original.is_dir() and (original / 'server.py').exists()
    assert not list((tmp_path / 'state' / 'plugins' / 'packages').iterdir())


def test_reviewed_snapshot_does_not_follow_source_edits(live, tmp_path):
    runtime, lifecycle, _ = live
    original = source(tmp_path / 'bundle')
    preview = lifecycle.manager.preview(str(original))
    file = original / 'skills' / 'review' / 'SKILL.md'
    file.write_text(file.read_text().replace('PLUGIN_REVIEW_BODY', 'UNREVIEWED_BODY'), encoding='utf-8')
    lifecycle.manage({'action': 'install', 'token': preview['token']})
    definition = runtime.skill_manager.discover(tmp_path).get('plugin.demo.review')
    assert 'PLUGIN_REVIEW_BODY' in runtime.skill_manager.load(definition)


def test_changed_staging_snapshot_is_refused(live, tmp_path):
    _, lifecycle, _ = live
    preview = lifecycle.manager.preview(str(source(tmp_path / 'bundle')))
    staged = lifecycle.manager.root / '.staging' / preview['token'] / 'package' / 'skills' / 'review' / 'SKILL.md'
    staged.write_text(staged.read_text() + '\nchanged', encoding='utf-8')
    with pytest.raises(PluginManagerError, match='changed after preview'):
        lifecycle.manage({'action': 'install', 'token': preview['token']})
    assert lifecycle.snapshot()['plugins'] == []


def test_unsupported_hooks_require_explicit_partial_install(live, tmp_path):
    _, lifecycle, _ = live
    root = source(tmp_path / 'claude', claude=True)
    hooks = root / 'hooks'
    hooks.mkdir()
    (hooks / 'hooks.json').write_text('{"SessionStart":[]}', encoding='utf-8')
    preview = lifecycle.manager.preview(str(root))
    assert preview['format'] == 'claude' and preview['unsupported'] == ['hooks']
    with pytest.raises(PluginManagerError, match='unsupported components'):
        lifecycle.manage({'action': 'install', 'token': preview['token']})
    result = lifecycle.manage({'action': 'install', 'token': preview['token'], 'allowPartial': True})
    assert result['plugins'][0]['activeSkills'] == 1
    assert result['plugins'][0]['unsupported'] == ['hooks']


def test_credentials_stay_in_vault_and_are_revoked_on_remove(live, tmp_path, monkeypatch):
    runtime, lifecycle, _ = live
    vault = {}
    monkeypatch.setattr(mcp_credentials, 'resolve', lambda key: vault.get(key))
    monkeypatch.setattr(mcp_credentials, 'write', vault.__setitem__)
    monkeypatch.setattr(mcp_credentials, 'delete', lambda key: vault.pop(key, None))
    preview = lifecycle.manager.preview(str(source(tmp_path / 'bundle', mcp=True)))
    result = lifecycle.manage({'action': 'install', 'token': preview['token'], 'credentials': {'SERVICE_KEY': 'private-test-value'}})
    assert 'private-test-value' not in json.dumps(result)
    assert 'private-test-value' not in lifecycle.manager.registry_path.read_text()
    assert runtime.mcp_clients.configs[0].resolved_env() == {'API_KEY': 'private-test-value'}
    lifecycle.manage({'action': 'remove', 'name': 'demo'})
    assert not vault


def test_activation_failure_restores_registry_vault_and_old_binding(live, tmp_path, monkeypatch):
    runtime, lifecycle, _ = live
    preview = lifecycle.manager.preview(str(source(tmp_path / 'bundle', mcp=True)))
    vault = {}
    monkeypatch.setattr(mcp_credentials, 'resolve', vault.get)
    monkeypatch.setattr(mcp_credentials, 'write', vault.__setitem__)
    monkeypatch.setattr(mcp_credentials, 'delete', lambda key: vault.pop(key, None))
    old = runtime.mcp_clients
    def fail():
        raise RuntimeError('activation failed')
    monkeypatch.setattr(lifecycle, 'apply', fail)
    with pytest.raises(RuntimeError, match='activation failed'):
        lifecycle.manage({'action': 'install', 'token': preview['token'], 'credentials': {'SERVICE_KEY': 'not-persisted'}})
    assert runtime.mcp_clients is old
    assert lifecycle.snapshot()['plugins'] == []
    assert not vault


def test_upgrade_revokes_previous_skill_context_and_keeps_snapshot_names(live, tmp_path):
    runtime, lifecycle, platform = live
    original = source(tmp_path / 'bundle')
    preview = lifecycle.manager.preview(str(original))
    lifecycle.manage({'action': 'install', 'token': preview['token']})
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path)
    platform.responses.extend([ModelResponse(tool_calls=(ToolCall(call_id='load', name='skill_load', arguments={'name': 'plugin.demo.review'}),)), ModelResponse(text='done')])
    runtime.start_turn(session.session_id, 'Use the plugin review skill.')
    assert any(m.name == 'loom_active_skills' for m in platform.requests[-1].messages)
    manifest = original / 'loom-plugin.json'
    manifest.write_text(json.dumps({'name': 'demo', 'version': '2.0.0'}), encoding='utf-8')
    update = lifecycle.manager.preview(str(original))
    lifecycle.manage({'action': 'upgrade', 'name': 'demo', 'token': update['token']})
    platform.responses.append(ModelResponse(text='done'))
    runtime.start_turn(session.session_id, 'Continue without reloading skills.')
    current = [m for m in platform.requests[-1].messages if m.name == 'loom_active_skills'][-1]
    assert 'no longer active' in current.content
    assert 'PLUGIN_REVIEW_BODY' not in current.content
    assert runtime.store.load(session.session_id).active_skills == {}


def test_restart_restores_enabled_plugin_and_disabled_state(tmp_path):
    root = tmp_path / 'state'
    manager = PluginManager(root)
    preview = manager.preview('builtin:review-assistant')
    manager.commit_preview(preview['token'])
    def boot():
        return SkillRuntime(platform=Platform(), store=FileAgentSessionStore(root), tools=ToolRegistry(), skill_roots=(), mcp_servers=(),
                            auto_configure_computer=False, auto_configure_browser=False, auto_configure_web_search=False)
    runtime = boot()
    try:
        assert runtime.skill_manager.discover(tmp_path).get('plugin.review-assistant.review-change')
        PluginLifecycle(runtime, root).manage({'action': 'disable', 'name': 'review-assistant'})
    finally:
        runtime.close()
    restored = boot()
    try:
        assert restored.skill_manager.discover(tmp_path).get('plugin.review-assistant.review-change') is None
    finally:
        restored.close()


def test_claude_commands_are_namespaced_skill_workflows(tmp_path):
    root = source(tmp_path / 'bundle', claude=True)
    commands = root / 'commands'
    commands.mkdir()
    (commands / 'audit.md').write_text('---\ndescription: Audit the current change.\n---\nAudit it.\n', encoding='utf-8')
    plan = inspect_package(root)
    assert {s['name'] for s in plan['skills']} == {'plugin.demo.review', 'plugin.demo.audit'}


@pytest.mark.parametrize('bad', ['../escape', '/absolute', 'C:/escape', 'folder/../../escape'])
def test_manifest_component_paths_cannot_escape_bundle(tmp_path, bad):
    root = source(tmp_path / 'bundle')
    (root / 'loom-plugin.json').write_text(json.dumps({'name': 'demo', 'skills': [bad]}), encoding='utf-8')
    with pytest.raises((ValueError, OSError)):
        PluginManager(tmp_path / 'state').preview(str(root))


@pytest.mark.parametrize('bad', ['../escape.txt', 'C:/escape.txt', 'safe:stream', 'CON.txt', '/absolute.txt'])
def test_zip_paths_are_rejected(tmp_path, bad):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr(bad, 'bad')
    with pytest.raises(PluginManagerError):
        PluginManager(tmp_path / 'state').preview(str(archive))


def test_legacy_registration_is_not_reported_active(tmp_path):
    manager = PluginManager(tmp_path)
    manager.restore({'legacy': {'name': 'legacy', 'source': 'https://example.com/plugin.git', 'enabled': True}})
    assert manager.list()[0]['status'] == 'Needs reinstall'


def test_literal_mcp_keys_are_rejected_before_install(live, tmp_path):
    _, lifecycle, _ = live
    root = source(tmp_path / 'bundle', mcp=True)
    (root / '.mcp.json').write_text(json.dumps({'bad': {'command': 'python', 'env': {'API_KEY': 'literal-value'}}}), encoding='utf-8')
    with pytest.raises(PluginManagerError, match='literal'):
        lifecycle.manager.preview(str(root))
    assert lifecycle.snapshot()['plugins'] == []


def test_real_stdio_plugin_restores_after_restart(tmp_path):
    root = source(tmp_path / 'bundle', mcp=True)
    fixture = Path(__file__).parent / 'fixtures' / 'mcp_stdio_server.py'
    (root / 'server.py').write_text(fixture.read_text(), encoding='utf-8')
    definition = {'command': sys.executable, 'args': ['${LOOM_PLUGIN_ROOT}/server.py']}
    if os.environ.get('PYTHONPATH'):
        definition['env'] = {'PYTHONPATH': '${PYTHONPATH}'}
    (root / '.mcp.json').write_text(json.dumps({'echo': definition}), encoding='utf-8')
    home = tmp_path / 'state'
    def boot():
        return SkillRuntime(platform=Platform(), store=FileAgentSessionStore(home), tools=ToolRegistry(), skill_roots=(), mcp_servers=(),
                            auto_configure_computer=False, auto_configure_browser=False, auto_configure_web_search=False)
    runtime = boot()
    try:
        lifecycle = PluginLifecycle(runtime, home)
        preview = lifecycle.manager.preview(str(root))
        lifecycle.manage({'action': 'install', 'token': preview['token']})
        tool = runtime.mcp_clients.agent_tools()[0]
        assert 'stdio:hello' in tool.handler(ToolContext(session_id='s', turn_id='t', workspace=tmp_path), {'text': 'hello'}).content
    finally:
        runtime.close()
    restored = boot()
    try:
        assert restored.mcp_clients.status()['connected_servers'] == 1
        assert restored.skill_manager.discover(tmp_path).get('plugin.demo.review')
    finally:
        restored.close()


def test_broken_legacy_plugin_does_not_block_install_or_removal(live):
    _, lifecycle, _ = live
    lifecycle.manager.restore({name: {'name': name, 'enabled': True, 'source': 'missing'} for name in ('old-one', 'old-two')})
    preview = lifecycle.manager.preview('builtin:review-assistant')
    result = lifecycle.manage({'action': 'install', 'token': preview['token']})
    assert next(r for r in result['plugins'] if r['name'] == 'review-assistant')['status'] == 'Active'
    lifecycle.manage({'action': 'remove', 'name': 'old-one'})
    lifecycle.manage({'action': 'remove', 'name': 'old-two'})
    assert len(lifecycle.snapshot()['plugins']) == 1


def test_plugin_servers_are_owned_in_mcp_settings(live, tmp_path):
    from app.mcp_settings import MCPSettings
    runtime, lifecycle, _ = live
    preview = lifecycle.manager.preview(str(source(tmp_path / 'bundle', mcp=True)))
    lifecycle.manage({'action': 'install', 'token': preview['token']})
    settings = MCPSettings(runtime)
    row = settings.snapshot()['servers'][0]
    assert row['plugin'] == 'demo' and not row['editable']
    with pytest.raises(ValueError, match='external configuration'):
        settings.manage({'action': 'save', 'server': {'name': row['name'], 'transport': 'stdio', 'command': 'other'}})


def test_rpc_preview_is_inert_and_active_turn_refuses_install(live, tmp_path):
    from app.app_server import LoomAppServerService, LoomRpcController
    runtime, lifecycle, _ = live
    service = LoomAppServerService(runtime=runtime, store=runtime.store, model='test', default_workspace=tmp_path)
    rpc = LoomRpcController(service)
    preview = rpc._dispatch('plugin/preview', {'source': 'builtin:review-assistant'})
    assert rpc._dispatch('plugin/list', {})['plugins'] == []
    service._active_sessions.add('active')
    with pytest.raises(RuntimeError, match='active turns'):
        rpc._dispatch('plugin/manage', {'action': 'install', 'token': preview['token']})
    service._active_sessions.clear()
    assert rpc._dispatch('plugin/manage', {'action': 'install', 'token': preview['token']})['plugins'][0]['status'] == 'Active'


def test_manifest_cannot_claim_another_stored_credential(live, tmp_path):
    _, lifecycle, _ = live
    root = source(tmp_path / 'bundle', mcp=True)
    (root / '.mcp.json').write_text(json.dumps({'echo': {'command': 'python', 'env_from': {'API_KEY': mcp_credentials.reference('another-server')}}}), encoding='utf-8')
    with pytest.raises(PluginManagerError, match='host credential-store identifiers'):
        lifecycle.manager.preview(str(root))


def test_regular_skill_with_plugin_prefix_keeps_its_context(live, tmp_path):
    runtime, _, platform = live
    folder = tmp_path / '.agents' / 'skills' / 'plugin.custom'
    folder.mkdir(parents=True)
    (folder / 'SKILL.md').write_text('---\nname: plugin.custom\ndescription: A user workflow with an existing dotted name.\n---\nREGULAR_PREFIX_SKILL\n', encoding='utf-8')
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=tmp_path)
    platform.responses.extend([ModelResponse(tool_calls=(ToolCall(call_id='load', name='skill_load', arguments={'name': 'plugin.custom'}),)), ModelResponse(text='done')])
    runtime.start_turn(session.session_id, 'Use my regular skill.')
    current = [m for m in platform.requests[-1].messages if m.name == 'loom_active_skills'][-1]
    assert 'REGULAR_PREFIX_SKILL' in current.content
    assert 'plugin.custom' in runtime.store.load(session.session_id).active_skills
