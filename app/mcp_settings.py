"""Desktop managed MCP servers; secret values stay in the server environment."""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from app.agent_runtime.mcp_runtime import MCPClientManager, MCPServerConfig, canonical_mcp_tool_name
from app import mcp_credentials

FIELDS = {'name', 'transport', 'command', 'args', 'cwd', 'url', 'env_from',
          'bearer_token_env', 'enabled', 'timeout_seconds'}
ENV_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def parse_server(raw):
    if not isinstance(raw, dict) or set(raw) - FIELDS:
        raise ValueError('Unsupported configuration; use environment references for credentials')
    name = raw.get('name', '')
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', name):
        raise ValueError('Name must contain 1–64 letters, digits, dots, underscores or hyphens')
    args, env = raw.get('args', []), raw.get('env_from', {})
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError('Arguments must be an array of strings')
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) and ENV_NAME.fullmatch(k) and ENV_NAME.fullmatch(v) for k, v in env.items()):
        raise ValueError('Environment mappings must contain variable names, not values')
    token = raw.get('bearer_token_env', '')
    if not isinstance(token, str) or (token and not ENV_NAME.fullmatch(token)):
        raise ValueError('Bearer token must reference an environment variable')
    if not isinstance(raw.get('enabled', True), bool):
        raise ValueError('Enabled must be a boolean')
    timeout = float(raw.get('timeout_seconds', 30))
    if not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise ValueError('Timeout must be between 1 and 120 seconds')
    for field in ('transport', 'command', 'cwd', 'url'):
        if field in raw and not isinstance(raw[field], str):
            raise ValueError(f'{field} must be text')
    config = MCPServerConfig(**{**raw, 'args': tuple(args), 'env_from': tuple(env.items()), 'timeout_seconds': timeout})
    if config.transport == 'http':
        url = urlsplit(config.url)
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Use a server URL without credentials, query or fragment')
        if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError('HTTP requires HTTPS or a loopback host')
    return config


def project(config):
    return {k: dict(v) if k == 'env_from' else list(v) if k == 'args' else v
            for k, v in asdict(config).items() if k in FIELDS}


def load_managed(path):
    path = Path(path)
    if not path.exists():
        return ()
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list):
        raise ValueError('Managed MCP configuration must be an array')
    configs = tuple(parse_server(item) for item in data)
    if len({c.name for c in configs}) != len(configs):
        raise ValueError('Duplicate MCP server names')
    return configs


class MCPSettings:
    def __init__(self, runtime):
        self.runtime = runtime
        self.path = Path(runtime.mcp_managed_path)

    def snapshot(self):
        managed = load_managed(self.path)
        state = self.runtime.mcp_clients.status()
        statuses = {item['name']: item for item in state['servers']}
        return {'servers': [{**project(c), **statuses.get(c.name, {}), 'editable': editable}
                            for c, editable in [*((c, False) for c in self.runtime.mcp_external_configs), *((c, True) for c in managed)]],
                'connected_servers': state['connected_servers'], 'remote_tool_count': state['tool_count'],
                'sdk_available': state['sdk_available'], 'config_path': str(self.path),
                'tools': [{'name': t.remote_name, 'server': t.server_name, 'description': t.description}
                          for t in self.runtime.mcp_clients.capture_binding().tools()]}

    def manage(self, params):
        action = params.get('action')
        configs = list(load_managed(self.path))
        name = str(params.get('name') or '')
        index = next((i for i, c in enumerate(configs) if c.name == name), None)
        credential_change = None
        previous = configs[index] if index is not None else None
        if action == 'save':
            config = parse_server(params.get('server'))
            token = params.get('token')
            if token is not None:
                if not isinstance(token, str) or not token.strip() or len(token) > 16384 or config.transport != 'http':
                    raise ValueError('Token must be nonempty text for an HTTP server')
                raw = project(config)
                raw['bearer_token_env'] = mcp_credentials.reference(config.name)
                config = parse_server(raw)
                credential_change = (config.bearer_token_env, token.strip())
            if any(c.name == config.name for c in self.runtime.mcp_external_configs):
                raise ValueError('This name belongs to an external configuration')
            if index is not None and config.name != name:
                raise ValueError('Server names cannot be changed; add a new server instead')
            if index is None:
                if name:
                    raise ValueError('Managed server no longer exists; refresh before editing')
                if any(c.name == config.name for c in configs):
                    raise ValueError('Server name already exists')
                configs.append(config)
            else:
                configs[index] = config
        elif action in {'delete', 'toggle', 'reconnect'}:
            if index is None:
                raise ValueError('Managed server not found')
            if action == 'delete':
                configs.pop(index)
            elif action == 'toggle':
                raw = project(configs[index])
                raw['enabled'] = not raw['enabled']
                configs[index] = parse_server(raw)
        else:
            raise ValueError('Unknown MCP management action')
        identities = [canonical_mcp_tool_name(c.name, 'probe') for c in (*self.runtime.mcp_external_configs, *configs)]
        if len(set(identities)) != len(identities):
            raise ValueError('Server names produce conflicting tool names; choose a distinct name')
        old_secret = None
        if credential_change:
            old_secret = mcp_credentials.resolve(credential_change[0])
            mcp_credentials.write(*credential_change)
        old = self.runtime.mcp_clients
        manager = MCPClientManager((*self.runtime.mcp_external_configs, *configs),
                                   max_result_chars=old.max_result_chars, target_factory=old._target_factory)
        try:
            manager.connect()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps([project(c) for c in configs], ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temporary, self.path)
        except Exception:
            manager.close()
            if credential_change:
                if old_secret is None:
                    mcp_credentials.delete(credential_change[0])
                else:
                    mcp_credentials.write(credential_change[0], old_secret)
            raise
        self.runtime.mcp_clients = manager
        old.close()
        if previous and (action == 'delete' or (action == 'save' and previous.bearer_token_env != config.bearer_token_env)):
            mcp_credentials.delete(previous.bearer_token_env)
        return self.snapshot()
