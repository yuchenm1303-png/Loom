"""Browse a public plugin directory; inspect the package before installation."""
import json
import math
import time
from pathlib import Path
from urllib.parse import urlsplit

from app.mcp_marketplace import fetch_json
from app.plugin_packages import valid_name

CATALOG_URL = 'https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/.claude-plugin/marketplace.json'
REPOSITORY = 'https://github.com/anthropics/claude-plugins-official.git'


class PluginCatalog:
    def __init__(self, runtime_home, *, fetcher=fetch_json):
        self.path = Path(runtime_home) / 'plugins' / '.catalog-cache.json'
        self.fetcher = fetcher

    def _data(self, refresh=False):
        cached = None
        try:
            if self.path.stat().st_size < 3_000_000:
                cached = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(cached.get('data', {}).get('plugins'), list) or not isinstance(cached.get('time'), (int, float)) or not math.isfinite(cached['time']):
                    cached = None
        except (ValueError, OSError, AttributeError):
            pass
        if cached and not refresh and 0 <= time.time() - cached['time'] < 900:
            return cached['data'], {'cached': True, 'stale': False, 'fetchedAt': cached['time']}
        try:
            data = self.fetcher(CATALOG_URL)
            if not isinstance(data, dict) or not isinstance(data.get('plugins'), list):
                raise ValueError('Invalid plugin directory')
            now = time.time()
            try:
                from app.plugin_manager import _atomic
                _atomic(self.path, {'time': now, 'data': data})
            except OSError:
                pass
            return data, {'cached': False, 'stale': False, 'fetchedAt': now}
        except Exception:
            if cached:
                return cached['data'], {'cached': True, 'stale': True, 'fetchedAt': cached['time']}
            raise RuntimeError('Plugin directory is unavailable; retry or install from a local/HTTPS source') from None

    def _entry(self, row):
        name = valid_name(row.get('name'))
        raw = row.get('source')
        source = None
        if isinstance(raw, str) and raw.startswith('./'):
            source = {'url': REPOSITORY, 'ref': 'main', 'path': raw[2:]}
        elif isinstance(raw, dict):
            if raw.get('source') in {'url', 'git-subdir'}:
                source = {'url': raw.get('url', ''), 'path': raw.get('path', ''), 'ref': raw.get('sha') or raw.get('ref') or ''}
            elif raw.get('source') == 'github':
                source = {'url': 'https://github.com/' + str(raw.get('repo', '')) + '.git', 'path': raw.get('path', ''), 'ref': raw.get('sha') or raw.get('ref') or ''}
        if source:
            url = urlsplit(source['url'])
            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
                source = None
        # Entries that define their own component layout need an adapter rather
        # than silently installing every skill found in the repository.
        if row.get('strict') is False and any(row.get(key) for key in ('skills', 'commands', 'agents', 'hooks')):
            source = None
        homepage = row.get('homepage', '')
        url = urlsplit(homepage) if isinstance(homepage, str) else None
        homepage = homepage if url and url.scheme == 'https' and url.hostname and not url.username and not url.password else ''
        return {'name': name, 'description': str(row.get('description', ''))[:2000], 'category': str(row.get('category', 'other'))[:80],
                'homepage': homepage, 'source': source, 'reviewable': source is not None}

    def list(self, params):
        query = params.get('search', '')
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError('Invalid plugin search')
        try:
            offset = int(params.get('offset', 0))
        except (ValueError, TypeError):
            raise ValueError('Invalid plugin page') from None
        if offset < 0 or offset > 5000:
            raise ValueError('Invalid plugin page')
        data, freshness = self._data(params.get('refresh') is True)
        entries = []
        for row in data['plugins'][:5000]:
            try:
                entry = self._entry(row)
            except (ValueError, AttributeError, TypeError):
                continue
            if query.casefold() in (entry['name'] + ' ' + entry['description'] + ' ' + entry['category']).casefold():
                entries.append(entry)
        return {'plugins': entries[offset:offset + 24], 'total': len(entries),
                'nextOffset': offset + 24 if offset + 24 < len(entries) else None,
                'source': 'Anthropic plugin directory', 'sourceUrl': 'https://github.com/anthropics/claude-plugins-official', **freshness}

    def source(self, name, *, refresh=False):
        valid_name(name)
        data, _ = self._data(refresh)
        for row in data['plugins'][:5000]:
            if isinstance(row, dict) and row.get('name') == name:
                entry = self._entry(row)
                if not entry['source']:
                    raise ValueError('This plugin source requires manual setup')
                return entry['source']
        raise ValueError('Plugin was not found in the directory')
