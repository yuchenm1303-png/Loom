"""Apply a reviewed plugin registry to the existing MCP and Skills runtimes."""
from __future__ import annotations

from app import mcp_credentials
from app.plugin_manager import PluginManager, PluginManagerError


class PluginLifecycle:
    def __init__(self, runtime, runtime_home):
        self.runtime = runtime
        self.manager = PluginManager(runtime_home)

    def snapshot(self):
        components = self.manager.components()
        client = getattr(self.runtime, 'mcp_clients', None)
        states = {s['name']: s for s in client.status()['servers']} if client else {}
        rows = self.manager.list()
        for row in rows:
            name = row['name']
            error = components['errors'].get(name) or getattr(self.runtime, 'plugin_errors', {}).get(name)
            errors = [error] if error else []
            servers = []
            for server in row.get('servers', []):
                state = states.get(server['name'], {})
                servers.append({**server, 'connected': bool(state.get('connected')), 'tool_count': state.get('tool_count', 0), 'error': state.get('error', '')})
            row['servers'] = servers
            row['activeSkills'] = sum(s.plugin_name == name for s in components['skills']) if hasattr(self.runtime, 'skill_manager') else 0
            if row.get('enabled') and row.get('skills') and not hasattr(self.runtime, 'skill_manager'):
                errors.append('This runtime does not support plugin Skills')
            row['error'] = row.get('error') or '; '.join(errors)
            if row['error']:
                row['status'] = 'Needs reinstall' if not row.get('revision') else 'Error'
            elif row.get('enabled'):
                row['status'] = 'Degraded' if any(s.get('enabled', True) and not s['connected'] for s in servers) else 'Active'
        return {'plugins': rows, 'activation': 'live'}

    def apply(self):
        from app.agent_runtime.mcp_runtime import MCPClientManager, canonical_mcp_tool_name
        from app.mcp_settings import load_managed
        runtime = self.runtime
        if not hasattr(runtime, 'mcp_clients') or not hasattr(runtime, 'mcp_operator_configs'):
            raise PluginManagerError('This runtime does not support live plugin activation')
        components = self.manager.components(verify=True)
        if components['skills'] and not hasattr(runtime, 'skill_manager'):
            raise PluginManagerError('This runtime does not support plugin Skills')
        external = (*runtime.mcp_operator_configs, *components['mcp'])
        configs = (*external, *load_managed(runtime.mcp_managed_path))
        identities = [canonical_mcp_tool_name(c.name, 'probe') for c in configs]
        if len(set(identities)) != len(identities):
            raise PluginManagerError('Plugin MCP names conflict with another configured server')
        old = runtime.mcp_clients
        manager = MCPClientManager(configs, max_result_chars=old.max_result_chars, target_factory=old._target_factory)
        try:
            manager.connect()
        except Exception:
            manager.close()
            raise
        runtime.mcp_clients = manager
        runtime.mcp_external_configs = tuple(external)
        runtime.plugin_mcp_owners = components['owners']
        runtime.plugin_errors = components['errors']
        try:
            old.close()
        except Exception:
            # The new manager is already valid. Old-client cleanup cannot turn
            # a successful activation into a registry/runtime split.
            pass

    def manage(self, params):
        action = params.get('action')
        before = self.manager._registry()
        old_secrets = {}
        token = params.get('token')
        name = str(params.get('name') or '')
        try:
            if action in {'install', 'upgrade', 'update'}:
                _, preview = self.manager._preview(token)
                if action != 'install' and name and name != preview['plan']['name']:
                    raise PluginManagerError('An update cannot change the installed plugin name')
                enabled = params.get('enabled', before.get(preview['plan']['name'], {}).get('enabled', True))
                if not isinstance(enabled, bool):
                    raise PluginManagerError('enabled must be a boolean')
                result = self.manager.commit_preview(token, upgrade=action != 'install', enabled=enabled,
                                                     allow_partial=params.get('allowPartial') is True)
                name = result['plugin']['name']
            elif action in {'enable', 'disable', 'remove', 'uninstall', 'delete'}:
                result = self.manager.change(name, action)
            elif action in {'configure', 'reconnect'}:
                self.manager.get(name)
                result = {'activation': 'live'}
            else:
                raise PluginManagerError('Unknown plugin action')
            registry = self.manager._registry()
            record = registry.get(name, {})
            credentials = params.get('credentials', {})
            remove = params.get('removeCredentials', [])
            allowed = set(record.get('plan', {}).get('credentials', []))
            if not isinstance(credentials, dict) or set(credentials) - allowed or not isinstance(remove, list) or any(not isinstance(k, str) or k not in allowed for k in remove):
                raise PluginManagerError('Unknown plugin credential names')
            bindings = dict(record.get('credentialRefs', {}))
            for key, value in credentials.items():
                if not isinstance(value, str) or not value.strip() or len(value) > 16384:
                    raise PluginManagerError('Credentials must be nonempty text shorter than 16385 characters')
                reference = mcp_credentials.reference(f'plugin:{name}:{key}')
                old_secrets[reference] = mcp_credentials.resolve(reference)
                mcp_credentials.write(reference, value.strip())
                bindings[key] = reference
            for key in remove:
                bindings.pop(key, None)
            if not record.get('removed'):
                record['credentialRefs'] = bindings
                registry[name] = record
                self.manager.restore(registry)
            errors = self.manager.components(verify=True)['errors']
            if name in errors and record.get('enabled') is not False and not record.get('removed'):
                raise PluginManagerError(errors[name])
            self.apply()
        except Exception:
            self.manager.restore(before)
            for reference, value in old_secrets.items():
                if value is None:
                    mcp_credentials.delete(reference)
                else:
                    mcp_credentials.write(reference, value)
            self.manager.collect()
            raise
        # Only remove owned credentials no longer referenced by this plugin.
        previous_refs = before.get(name, {}).get('credentialRefs', {})
        current_refs = self.manager._registry().get(name, {}).get('credentialRefs', {})
        for reference in set(previous_refs.values()) - set(current_refs.values()):
            mcp_credentials.delete(reference)
        if token:
            self.manager.discard(token)
        self.manager.collect()
        return {**self.snapshot(), 'changed': name}
