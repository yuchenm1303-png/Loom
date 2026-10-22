from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from app.agent_runtime.contracts import PermissionMode
from app.agent_runtime.process_runtime import ProcessStore
from app.code_block_terminal import _dispatch, _shell_argv


@pytest.fixture
def terminal(tmp_path):
    store = ProcessStore()
    sessions = [SimpleNamespace(session_id='terminal-test', workspace_dir=tmp_path,
                                permission_mode=PermissionMode.FULL_ACCESS)]
    service = SimpleNamespace(runtime=SimpleNamespace(process_store=store),
                              _list_session_objects=lambda: sessions, _is_active=lambda _: False)
    def call(method, **params):
        return _dispatch(service, method, {'workspace': str(tmp_path), **params})
    yield call, service, tmp_path
    store.terminate_all()


def wait_output(call, process_id, text, cursor=0):
    deadline = time.monotonic() + 15
    output = ''
    while time.monotonic() < deadline:
        result = call('terminal/session/read', processId=process_id, cursor=cursor)
        cursor = result['cursor']
        output += result['output']
        if text in output:
            return result, output
        time.sleep(.05)
    raise AssertionError(f'No {text!r} in terminal output: {output[-2000:]!r}')


def test_real_shell_input_resize_retention_and_close(terminal):
    call, service, root = terminal
    profiles = call('terminal/profiles')['profiles']
    assert profiles and all('argv' not in profile for profile in profiles)
    created = call('terminal/session/create', shell=profiles[0]['id'], rows=25, cols=99)
    pid = created['processId']
    assert created['pty'] and created['running']
    assert service.runtime.process_store.get(pid, session_id='terminal-test').timeout_seconds is None
    # No reader or list operation kills the shell. A subsequent write uses the
    # same process, environment and working directory.
    call('terminal/session/write', processId=pid, text='echo LOOM_PTY_READY\r')
    result, output = wait_output(call, pid, 'LOOM_PTY_READY')
    assert result['running']
    resized = call('terminal/session/resize', processId=pid, rows=31, cols=113)
    assert resized['rows'] == 31 and resized['cols'] == 113
    call('terminal/session/rename', processId=pid, title='Development')
    listed = call('terminal/session/list')['sessions']
    assert listed[0]['title'] == 'Development'
    assert listed[0]['cwd'] == str(root)
    # A second viewer gets the same output without consuming the first one's
    # cursor or the agent's process snapshot.
    _, replay = wait_output(call, pid, 'LOOM_PTY_READY', cursor=0)
    assert 'LOOM_PTY_READY' in replay
    assert call('terminal/session/close', processId=pid)['closed']
    assert call('terminal/session/list')['sessions'] == []
    deadline = time.monotonic() + 5
    while service.runtime.process_store.get(pid, session_id='terminal-test').running and time.monotonic() < deadline:
        time.sleep(.05)
    assert not service.runtime.process_store.get(pid, session_id='terminal-test').running


def test_cwd_and_process_ownership_are_enforced(terminal, tmp_path):
    call, service, root = terminal
    (root / 'sub').mkdir()
    created = call('terminal/session/create', cwd='sub')
    assert created['cwd'] == str(root / 'sub')
    with pytest.raises(ValueError, match='inside'):
        call('terminal/session/create', cwd='..')
    other = root / 'other'
    other.mkdir()
    service._list_session_objects = lambda: [SimpleNamespace(session_id='unrelated', workspace_dir=other, permission_mode=PermissionMode.FULL_ACCESS)]
    with pytest.raises(ValueError, match='does not belong'):
        call('terminal/session/read', workspace=str(other), processId=created['processId'])


def test_command_exit_and_independent_output_cursors(terminal):
    call, service, root = terminal
    created = call('terminal/session/create', language='python', command="print('终端运行成功', flush=True)")
    pid = created['processId']
    result, output = wait_output(call, pid, '终端运行成功')
    managed = service.runtime.process_store.get(pid, session_id='terminal-test')
    deadline = time.monotonic() + 10
    while managed.running and time.monotonic() < deadline:
        time.sleep(.05)
    final = call('terminal/session/read', processId=pid, cursor=0)
    assert not final['running'] and final['returnCode'] == 0
    assert '终端运行成功' in final['output']
    assert '终端运行成功' in managed.snapshot(drain_delta=True).stdout_delta
    assert call('terminal/session/read', processId=pid, cursor=final['cursor'])['output'] == ''
    with pytest.raises(ValueError, match='cursor'):
        call('terminal/session/read', processId=pid, cursor=-1)


def test_output_rollover_has_absolute_cursor(terminal):
    call, service, _ = terminal
    created = call('terminal/session/create', language='python', command="print('ready', flush=True)")
    managed = service.runtime.process_store.get(created['processId'], session_id='terminal-test')
    managed.wait(wait_timeout_seconds=10)
    initial = managed.read_terminal()
    managed._append('stdout', 'A' * 120000)
    tail = managed.read_terminal(initial['cursor'])
    assert tail['truncated'] and len(tail['output']) == 100000
    assert tail['cursor'] == initial['cursor'] + 120000
    assert managed.read_terminal(tail['cursor'])['output'] == ''
    assert managed.read_terminal(tail['cursor'] + 10)['reset']


def test_shell_language_is_never_silently_reinterpreted(monkeypatch):
    if os.name == 'nt':
        monkeypatch.setattr('app.code_block_terminal.shutil.which', lambda _: None)
        with pytest.raises(RuntimeError, match='bash is not installed'):
            _shell_argv('bash', 'echo hello')
    with pytest.raises(ValueError, match='not executable'):
        _shell_argv('sql', 'drop table example')


def test_terminal_io_never_rescans_the_conversation_library(terminal):
    call, service, _ = terminal
    created = call('terminal/session/create')
    pid = created['processId']
    assert '_sessionId' not in created
    def unexpected_scan():
        raise AssertionError('a keystroke must not load every stored conversation')
    service._list_session_objects = unexpected_scan
    for _ in range(20):
        call('terminal/session/read', processId=pid, cursor=0)
        call('terminal/session/write', processId=pid, text=' ')
    call('terminal/session/resize', processId=pid, rows=25, cols=100)
    call('terminal/session/rename', processId=pid, title='Fast terminal')
    assert call('terminal/session/list')['sessions'][0]['title'] == 'Fast terminal'
    assert call('terminal/session/close', processId=pid)['closed']


def test_terminal_status_does_not_snapshot_the_entire_transcript(terminal):
    call, service, _ = terminal
    created = call('terminal/session/create')
    pid = created['processId']
    managed = service.runtime.process_store.get(pid, session_id='terminal-test')
    managed.snapshot = lambda **_: pytest.fail('terminal I/O must not construct process transcript snapshots')
    assert call('terminal/session/read', processId=pid, cursor=0)['processId'] == pid
    assert call('terminal/session/write', processId=pid, text=' ')['running']
