import pytest

from app.plugin_catalog import PluginCatalog, REPOSITORY


def test_directory_search_paging_and_source_mapping(tmp_path):
    rows = [{'name': f'plugin-{i}', 'description': 'Code review', 'category': 'development', 'source': './plugins/demo'} for i in range(30)]
    client = PluginCatalog(tmp_path, fetcher=lambda url: {'plugins': rows})
    first = client.list({'search': 'review'})
    assert first['total'] == 30 and len(first['plugins']) == 24
    assert first['nextOffset'] == 24
    assert len(client.list({'search': 'review', 'offset': 24})['plugins']) == 6
    assert client.source('plugin-0') == {'url': REPOSITORY, 'ref': 'main', 'path': 'plugins/demo'}


def test_directory_pin_and_unsafe_links(tmp_path):
    rows = [{'name': 'pinned', 'source': {'source': 'git-subdir', 'url': 'https://github.com/example/repo.git', 'path': 'plugins/test', 'ref': 'main', 'sha': 'a' * 40}, 'homepage': 'javascript:alert(1)'},
            {'name': 'bad', 'source': {'source': 'url', 'url': 'https://user:password@example.com/repo.git'}}]
    client = PluginCatalog(tmp_path, fetcher=lambda url: {'plugins': rows})
    result = client.list({})
    assert client.source('pinned')['ref'] == 'a' * 40
    assert not result['plugins'][0]['homepage']
    assert not result['plugins'][1]['reviewable']
    with pytest.raises(ValueError):
        client.source('bad')


def test_directory_offline_cache_and_refresh(tmp_path):
    client = PluginCatalog(tmp_path, fetcher=lambda url: {'plugins': [{'name': 'demo', 'source': './plugins/demo'}]})
    client.list({})
    client.fetcher = lambda url: (_ for _ in ()).throw(OSError('offline'))
    result = client.list({'refresh': True})
    assert result['stale'] and result['cached']
    assert result['plugins'][0]['name'] == 'demo'
    empty = PluginCatalog(tmp_path / 'other', fetcher=client.fetcher)
    with pytest.raises(RuntimeError, match='unavailable'):
        empty.list({})
