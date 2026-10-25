"""Versioned plugin snapshots and reviewed installation lifecycle."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.plugin_packages import PluginPackageError, acquire, copy_tree, inside, inspect_package, linked, mcp_definition, tree_hash, valid_name

PluginManagerError = PluginPackageError


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class PluginManager:
    def __init__(self, runtime_home):
        self.runtime_home = Path(runtime_home).expanduser().resolve()
        self.root = self.runtime_home / 'plugins'
        self.registry_path = self.root / 'plugins.json'

    def _registry(self):
        if not self.registry_path.exists():
            return {}
        value = json.loads(self.registry_path.read_text(encoding='utf-8'))
        records = value.get('plugins', {})
        if isinstance(records, list):
            records = {row['name']: row for row in records if isinstance(row, dict) and 'name' in row}
        if not isinstance(records, dict):
            raise PluginManagerError('Invalid plugin registry')
        if any(not isinstance(record, dict) or not isinstance(name, str) for name, record in records.items()):
            raise PluginManagerError('Invalid plugin registry entries')
        return records

    def restore(self, records):
        _atomic(self.registry_path, {'schemaVersion': 2, 'updatedAt': _now(), 'plugins': records})

    def _owned(self, relative, *, exists=False):
        if linked(self.root):
            raise PluginManagerError('Plugin storage cannot be a filesystem link')
        return inside(self.root, relative, exists=exists)

    def _remove(self, path):
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root.resolve()) or resolved == self.root.resolve() or linked(path):
            raise PluginManagerError('Refusing to remove a path outside plugin storage')
        if resolved.exists():
            shutil.rmtree(resolved)

    def _package(self, record):
        revision = record.get('revision', '')
        if not re.fullmatch(r'[a-f0-9]{64}', revision):
            raise PluginManagerError('This legacy plugin must be reinstalled before activation')
        return self._owned('packages/' + revision, exists=True)

    def preview(self, source, *, name_hint='', catalog_name=''):
        staging = self._owned('.staging')
        staging.mkdir(parents=True, exist_ok=True)
        for directory in list(staging.iterdir()):
            if directory.is_dir() and not linked(directory) and time.time() - directory.stat().st_mtime > 1800:
                self._remove(directory)
        if len(list(staging.iterdir())) >= 20:
            raise PluginManagerError('Too many pending previews; finish or cancel an earlier preview')
        token = uuid.uuid4().hex
        directory = staging / token
        directory.mkdir()
        try:
            with tempfile.TemporaryDirectory(prefix='loom-plugin-source-') as temp:
                temporary = Path(temp)
                if source == 'builtin:review-assistant' or (isinstance(source, dict) and source.get('url') == 'builtin:review-assistant'):
                    root = temporary / 'review-assistant'
                    skill = root / 'skills' / 'review-change'
                    skill.mkdir(parents=True)
                    (root / 'loom-plugin.json').write_text(json.dumps({'schemaVersion': 1, 'name': 'review-assistant', 'version': '1.0.0', 'description': 'A starter plugin for reviewing changes with an Agent Skill.'}), encoding='utf-8')
                    (skill / 'SKILL.md').write_text('---\nname: review-change\ndescription: Review code changes for correctness and regressions when asked to review a diff.\n---\nRead the relevant diff and surrounding code. Identify reproducible correctness issues. Explain impact with file references, and state what was verified. Use normal tool permissions.\n', encoding='utf-8')
                    origin, git_revision = {'url': source}, ''
                else:
                    root, origin, git_revision = acquire(source, temporary)
                copy_tree(root, directory / 'package')
            package = directory / 'package'
            hint = name_hint or root.name
            if hint in {'repository', 'extracted'}:
                from urllib.parse import urlsplit
                hint = Path(urlsplit(origin['url']).path).stem
            plan = inspect_package(package, name_hint=hint)
            fingerprint = tree_hash(package)
            preview = {'token': token, 'createdAt': time.time(), 'source': origin, 'gitRevision': git_revision,
                       'integrity': fingerprint, 'plan': plan, 'catalogName': catalog_name}
            _atomic(directory / 'preview.json', preview)
            return self._preview_record(preview)
        except Exception:
            self._remove(directory)
            raise

    def _preview_record(self, preview):
        plan = preview['plan']
        root = self._owned('.staging/' + preview['token'] + '/package', exists=True)
        return {'token': preview['token'], 'name': plan['name'], 'version': plan['version'], 'description': plan['description'],
                'source': preview['source'], 'gitRevision': preview['gitRevision'], 'integrity': preview['integrity'],
                'format': plan['format'], 'skills': [{k: v for k, v in row.items() if k != 'raw'} for row in plan['skills']],
                'servers': [{**mcp_definition(plan['name'], row['local_name'], row['raw'], root)[0], 'local_name': row['local_name']} for row in plan['mcp']],
                'credentials': plan['credentials'], 'unsupported': plan['unsupported'],
                'alreadyInstalled': plan['name'].casefold() in {k.casefold() for k, v in self._registry().items() if not v.get('removed')}}

    def _preview(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
            raise PluginManagerError('Invalid plugin preview token')
        path = self._owned('.staging/' + token, exists=True)
        preview = json.loads((path / 'preview.json').read_text(encoding='utf-8'))
        if time.time() - preview['createdAt'] > 1800:
            raise PluginManagerError('This preview expired; inspect the source again')
        if tree_hash(path / 'package') != preview['integrity']:
            raise PluginManagerError('Plugin files changed after preview; inspect the source again')
        if inspect_package(path / 'package', name_hint=preview['plan']['name']) != preview['plan']:
            raise PluginManagerError('Plugin component plan changed after preview')
        return path, preview

    def discard(self, token):
        directory, _ = self._preview(token)
        self._remove(directory)
        return {'discarded': True}

    def commit_preview(self, token, *, upgrade=False, enabled=True, allow_partial=False):
        directory, preview = self._preview(token)
        plan = preview['plan']
        if plan['unsupported'] and not allow_partial:
            raise PluginManagerError('This plugin contains unsupported components; explicitly choose supported components only')
        registry = self._registry()
        name = plan['name']
        existing = next((key for key in registry if key.casefold() == name.casefold()), name)
        previous = registry.get(existing, {})
        if previous and not previous.get('removed') and not upgrade:
            raise PluginManagerError('Plugin already installed; review it as an update')
        if existing != name and previous:
            raise PluginManagerError('Plugin names cannot change letter case during updates')
        target = self._owned('packages/' + preview['integrity'])
        if target.exists():
            if tree_hash(target) != preview['integrity']:
                raise PluginManagerError('Existing plugin snapshot failed its integrity check')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_tree(directory / 'package', target)
        registry[name] = {'name': name, 'version': plan['version'], 'description': plan['description'],
                          'enabled': bool(enabled), 'revision': preview['integrity'], 'source': preview['source'],
                          'gitRevision': preview['gitRevision'], 'installedAt': previous.get('installedAt') or _now(),
                          'catalogName': preview.get('catalogName') or previous.get('catalogName', ''),
                          'updatedAt': _now(), 'plan': plan,
                          'credentialRefs': {k: v for k, v in previous.get('credentialRefs', {}).items() if k in plan['credentials']}}
        self.restore(registry)
        return {'plugin': self.get(name), 'installed': True, 'activation': 'live', 'token': token}

    def install(self, source, *, upgrade=False, enabled=True, allow_partial=False):
        preview = self.preview(source)
        return self.commit_preview(preview['token'], upgrade=upgrade, enabled=enabled, allow_partial=allow_partial)

    def get(self, name):
        valid_name(name)
        record = self._registry().get(name)
        if not record or record.get('removed'):
            raise PluginManagerError('Plugin is not installed')
        result = {k: v for k, v in record.items() if k not in {'plan', 'credentialRefs'}}
        plan = record.get('plan', {})
        result.update(skills=plan.get('skills', []), servers=[{'name': s['name'], 'local_name': s['local_name'], 'enabled': s['raw'].get('enabled', True)} for s in plan.get('mcp', [])],
                      credentials=[{'name': name, 'stored': name in record.get('credentialRefs', {})} for name in plan.get('credentials', [])],
                      unsupported=plan.get('unsupported', []), format=plan.get('format', 'legacy'),
                      status='Enabled' if record.get('enabled') else 'Disabled', error='')
        try:
            root = self._package(record)
            result['path'] = str(root)
        except (ValueError, OSError) as exc:
            result.update(status='Needs reinstall', error=str(exc))
        return result

    def list(self):
        registry = self._registry()
        rows = [self.get(name) for name, record in registry.items() if not record.get('removed')]
        if self.root.exists():
            for child in self.root.iterdir():
                if child.name in {'packages', 'plugins.json'} or child.name.startswith('.') or not child.is_dir() or linked(child):
                    continue
                try:
                    plan = inspect_package(child)
                except (ValueError, OSError):
                    continue
                if plan['name'] not in registry:
                    rows.append({'name': plan['name'], 'version': plan['version'], 'description': plan['description'],
                                 'source': {'url': str(child)}, 'enabled': False, 'status': 'Discovered',
                                 'skills': plan['skills'], 'servers': [], 'credentials': [], 'error': '', 'unsupported': plan['unsupported']})
        return sorted(rows, key=lambda r: r['name'].casefold())

    def change(self, name, action, *, release=''):
        valid_name(name)
        registry = self._registry()
        if name not in registry or registry[name].get('removed'):
            raise PluginManagerError('Plugin is not installed')
        if action in {'remove', 'uninstall', 'delete'}:
            registry[name] = {'name': name, 'removed': True}
        elif action in {'enable', 'disable'}:
            if action == 'enable':
                self._package(registry[name])
            registry[name]['enabled'] = action == 'enable'
            registry[name]['updatedAt'] = _now()
        else:
            raise PluginManagerError('To update a plugin, preview its source and install the reviewed snapshot')
        self.restore(registry)
        return {'removed': True, 'activation': 'live'} if registry[name].get('removed') else {'plugin': self.get(name), 'activation': 'live'}

    def components(self, *, verify=False):
        from app.agent_runtime.skills import SkillDefinition, SkillScope
        from app.mcp_settings import parse_server
        servers, skills, owners, errors = [], [], {}, {}
        try:
            registry = self._registry()
        except (ValueError, OSError, TypeError) as exc:
            return {'mcp': (), 'skills': (), 'owners': {}, 'errors': {'registry': str(exc)}}
        for name, record in registry.items():
            if record.get('removed') or record.get('enabled') is False:
                continue
            try:
                root = self._package(record)
                if verify and tree_hash(root) != record['revision']:
                    raise PluginManagerError('Installed files changed; reinstall the plugin')
                plan = record.get('plan')
                if not isinstance(plan, dict):
                    raise PluginManagerError('Plugin needs a reviewed reinstall')
                plugin_servers, plugin_skills = [], []
                for server in plan['mcp']:
                    config, _ = mcp_definition(name, server['local_name'], server['raw'], root, record.get('credentialRefs'))
                    plugin_servers.append(parse_server(config))
                for skill in plan['skills']:
                    path = inside(root, skill['path'])
                    plugin_skills.append(SkillDefinition(name=skill['name'], description=skill['description'], short_description='',
                                                        path=path, root=root, scope=SkillScope.USER,
                                                        plugin_name=name, source_name=skill['local_name'], source_revision=record['revision']))
                servers.extend(plugin_servers)
                skills.extend(plugin_skills)
                owners.update({server.name: name for server in plugin_servers})
            except (ValueError, OSError, KeyError, TypeError) as exc:
                errors[name] = str(exc)
        return {'mcp': tuple(servers), 'skills': tuple(skills), 'owners': owners, 'errors': errors}

    def collect(self):
        retained = {r.get('revision') for r in self._registry().values() if not r.get('removed')}
        packages = self._owned('packages')
        if packages.exists():
            for directory in packages.iterdir():
                if re.fullmatch(r'[a-f0-9]{64}', directory.name) and directory.name not in retained:
                    self._remove(directory)

    def purge(self):
        raise PluginManagerError('Remove individual plugins to preserve source folders and credential ownership')


__all__ = ['PluginManager', 'PluginManagerError']
