"""User-owned interactive shells sharing the managed PTY and sandbox runtime."""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from .agent_runtime.permissions import permission_snapshot
from .agent_runtime.process_runtime import validate_terminal_size

_LOCK = threading.RLock()


def _profiles() -> list[dict[str, Any]]:
    candidates = ([('pwsh', 'PowerShell', ('-NoLogo',)),
                   ('powershell', 'Windows PowerShell', ('-NoLogo',)),
                   ('cmd', 'Command Prompt', ('/d',)), ('bash', 'Bash', ('-i',)),
                   ('wsl', 'WSL', ())]
                  if os.name == 'nt' else
                  [('bash', 'Bash', ('-i',)), ('zsh', 'Zsh', ('-i',)),
                   ('fish', 'Fish', ('-i',)), ('sh', 'Shell', ('-i',)),
                   ('pwsh', 'PowerShell', ('-NoLogo',))])
    result = []
    for key, label, flags in candidates:
        executable = shutil.which(key)
        if executable:
            result.append({'id': key, 'label': label, 'argv': (executable, *flags)})
    return result


def _available_profiles(service) -> list[dict[str, Any]]:
    profiles = _profiles()
    settings = getattr(service, 'settings', None)
    preferences = settings.snapshot().get('terminal', {}) if settings is not None else {}
    preferred = str(preferences.get('shell') or '')
    if preferred == 'powershell':
        preferred = 'pwsh' if any(p['id'] == 'pwsh' for p in profiles) else 'powershell'
    elif preferred == 'git-bash':
        preferred = 'bash'
    return sorted(profiles, key=lambda profile: profile['id'] != preferred)


def _registry(service) -> dict[str, dict[str, Any]]:
    with _LOCK:
        if not hasattr(service, '_loom_interactive_terminals'):
            service._loom_interactive_terminals = {}
        return service._loom_interactive_terminals


def _record(managed, metadata) -> dict[str, Any]:
    snapshot = managed.snapshot()
    return {**metadata, 'processId': managed.process_id, 'running': snapshot.running,
            'state': snapshot.state.value, 'returnCode': snapshot.returncode,
            'failure': snapshot.failure, 'pty': snapshot.pty,
            'rows': snapshot.rows, 'cols': snapshot.cols}


def dispatch(service, method: str, params: dict[str, Any]) -> dict[str, Any]:
    from .code_block_terminal import _workspace, _execution_context, _process, _shell_argv

    if method == 'terminal/profiles':
        return {'profiles': [{k: v for k, v in p.items() if k != 'argv'} for p in _available_profiles(service)]}
    workspace = _workspace(params.get('workspace'))
    registry = _registry(service)
    if method == 'terminal/session/list':
        records = []
        with _LOCK:
            entries = list(registry.items())
        for process_id, meta in entries:
            if meta['workspace'] != str(workspace):
                continue
            try:
                managed = _process(service, {'workspace': str(workspace), 'processId': process_id})
                records.append(_record(managed, meta))
            except (KeyError, ValueError):
                with _LOCK:
                    registry.pop(process_id, None)
        return {'sessions': records}
    if method == 'terminal/session/create':
        profiles = _available_profiles(service)
        shell = str(params.get('shell') or (profiles[0]['id'] if profiles else ''))
        profile = next((p for p in profiles if p['id'] == shell), None)
        if not profile:
            raise ValueError(f'Shell is not installed: {shell or "default"}')
        cwd = (workspace / str(params.get('cwd') or '.')).resolve()
        if not cwd.is_dir() or not cwd.is_relative_to(workspace):
            raise ValueError('Terminal directory must be inside the project workspace')
        rows, cols = validate_terminal_size(params.get('rows', 24), params.get('cols', 100))
        try:
            session_id, mode = _execution_context(service, workspace)
        except ValueError as exc:
            if str(exc) != 'workspace is not associated with a Loom thread':
                raise
            # Opening a terminal in a fresh project is also an explicit user
            # action. Establish its normal thread ownership before spawning.
            service.thread_start({'workspace': str(workspace)})
            session_id, mode = _execution_context(service, workspace)
        command = str(params.get('command') or '')
        argv = _shell_argv(str(params.get('language') or 'shell'), command) if command else profile['argv']
        managed = service.runtime.process_store.start(
            session_id=session_id, argv=argv, cwd=cwd, workspace=workspace,
            permission_mode=mode.value, permissions=permission_snapshot(mode),
            timeout_seconds=3600 if command else None, pty=True, rows=rows, cols=cols,
            env={'TERM': 'xterm-256color', 'COLORTERM': 'truecolor'},
        )
        meta = {'workspace': str(workspace), 'cwd': str(cwd), 'shell': shell,
                'title': str(params.get('title') or profile['label'])[:80],
                'command': command, 'language': str(params.get('language') or 'shell'), 'createdAt': time.time()}
        with _LOCK:
            registry[managed.process_id] = meta
        return _record(managed, meta)

    if method == 'terminal/session/close':
        process_id = str(params.get('processId') or '').strip()
        if not process_id:
            raise ValueError('processId is required')
        with _LOCK:
            meta = registry.get(process_id)
        if not meta:
            return {'closed': True}
        if meta['workspace'] != str(workspace):
            raise ValueError('terminal process does not belong to this workspace')
        try:
            _process(service, params).terminate_tree()
        except ValueError:
            # Finished processes can already have been pruned by the shared
            # process store. Their stale tab must still be removable.
            pass
        with _LOCK:
            registry.pop(process_id, None)
        return {'closed': True}
    managed = _process(service, params)
    with _LOCK:
        metadata = registry.get(managed.process_id)
    if not metadata:
        raise ValueError('Not an interactive terminal session')
    if method == 'terminal/session/read':
        cursor = params.get('cursor', 0)
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise ValueError('cursor must be a non-negative integer')
        # Read output before status: the client performs one final poll after
        # exit so the reader thread can flush the final bytes.
        return {**managed.read_terminal(cursor), **_record(managed, metadata)}
    if method == 'terminal/session/write':
        managed.write_stdin(str(params.get('text') or ''))
    elif method == 'terminal/session/resize':
        rows, cols = validate_terminal_size(params.get('rows'), params.get('cols'))
        managed.resize(rows=rows, cols=cols, drain_delta=False)
    elif method == 'terminal/session/rename':
        title = str(params.get('title') or '').strip()[:80]
        if not title:
            raise ValueError('title must not be empty')
        with _LOCK:
            metadata['title'] = title
    else:
        raise ValueError(f'Unknown terminal session method: {method}')
    return _record(managed, metadata)
