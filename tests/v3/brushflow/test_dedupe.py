import ast
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
ROOT = Path('/vol1/1000/Dev/MoviePilot-Plugins')
spec = importlib.util.spec_from_file_location('brush_dedupe', ROOT/'plugins.v3/brushflow/dedupe.py')
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)

def test_hash_original_info_and_magnet():
    info=b'd6:lengthi1e4:name1:x12:piece lengthi1e6:pieces20:abcdefghijklmnopqrste'
    raw=b'd4:info'+info+b'e'
    assert d.content_hashes(raw)=={hashlib.sha1(info).hexdigest()}
    assert d.content_hashes('magnet:?xt=urn:btih:'+'A'*40)=={'a'*40}

@pytest.mark.parametrize('raw',[b'',b'<!DOCTYPE html>',b'd4:infodee',b'd4:infod1:ai1eeex', 'https://example.org/download.php?id=1'])
def test_bad_content_fail_closed(raw):
    with pytest.raises(d.IdentityUnavailable):d.content_hashes(raw)

def test_site_ids_require_exact_host_and_detail_url():
    assert d.detail_keys('https://a.test/details.php?id=001&passkey=SECRET')=={('a.test','1')}
    assert d.detail_keys('https://b.test/detail/1')=={('b.test','1')}
    assert not d.detail_keys('https://a.test/download.php?id=1')
    assert not d.detail_keys('same title')

def test_inventory_includes_unmanaged_and_does_not_mutate():
    client=Mock();client.torrents_info.return_value=[{'hash':'A'*40,'tags':'辅种'}]
    client.torrents_properties.return_value={'comment':'https://a.test/details.php?id=3'}
    svc=SimpleNamespace(type='qbittorrent',instance=SimpleNamespace(qbc=client))
    assert d.inventory(svc,True)==({'a'*40},{('a.test','3')})
    assert [x[0] for x in client.mock_calls]==['torrents_info','torrents_properties']
    client.torrents_info.side_effect=RuntimeError('secret-url')
    with pytest.raises(d.IdentityUnavailable,match='本轮暂缓新增'):d.inventory(svc)

def test_transmission_inventory():
    client=Mock();client.get_torrents.return_value=[SimpleNamespace(hashString='b'*40,comment='https://a.test/details.php?id=5')]
    assert d.inventory(SimpleNamespace(type='transmission',instance=SimpleNamespace(trc=client)),True)==({'b'*40},{('a.test','5')})

def test_preadd_duplicate_never_calls_add_or_changes_tags():
    source=ast.parse((ROOT/'plugins.v3/brushflow/__init__.py').read_text())
    cls=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=='BrushFlow')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__download')
    method.returns=None
    for a in method.args.args:a.annotation=None
    ns={'DuplicateTorrent':d.DuplicateTorrent,'IdentityUnavailable':d.IdentityUnavailable,'content_hashes':d.content_hashes,'inventory':d.inventory,'settings':SimpleNamespace(PROXY=None)}
    exec(compile(ast.Module(body=[method],type_ignores=[]),'<download>','exec'),ns)
    client=Mock();client.torrents_info.return_value=[{'hash':'a'*40}]
    downloader=Mock(qbc=client)
    task=SimpleNamespace(up_speed=None,dl_speed=None,site_skip_tips=False,dedupe_hash=True)
    plugin=SimpleNamespace(_get_task_config=lambda:task,downloader=downloader,service_info=SimpleNamespace(type='qbittorrent',instance=downloader))
    torrent=SimpleNamespace(enclosure='magnet:?xt=urn:btih:'+'a'*40,site_proxy=False,site_cookie=None,title='x')
    with pytest.raises(d.DuplicateTorrent):ns['__download'](plugin,torrent)
    downloader.add_torrent.assert_not_called()
