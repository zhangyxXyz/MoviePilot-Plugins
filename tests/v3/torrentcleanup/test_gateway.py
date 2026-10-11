"""下载器适配器使用 SDK 替身，校验文件引用及不确定证据。"""
from types import SimpleNamespace

import pytest
from app.plugins.torrentcleanup import gateway
from app.plugins.torrentcleanup.models import SitePolicy
from app.plugins.torrentcleanup.policy import Torrent


@pytest.fixture
def gateway_module():
    """加载真实 SDK；具体外部调用由每个用例在网关边界替换。"""
    return gateway


def test_manifests_detect_hardlinks_and_reject_escape(gateway_module, tmp_path):
    """路径和 inode 都参与去重，越界或缺失文件不能获得已验证状态。"""
    root = tmp_path / 'data'
    root.mkdir()
    (root / 'a').write_bytes(b'abc')
    (root / 'b').hardlink_to(root / 'a')
    mapping = [(str(root), '/remote')]
    a = gateway_module.map_files([{'name': 'a', 'size': 3}], '/remote', mapping)
    b = gateway_module.map_files([{'name': 'b', 'size': 3}], '/remote', mapping)
    assert a[2] and b[2] and a[0].intersection(b[0])
    for name in ['../a', '/a', 'missing']:
        assert not gateway_module.map_files([{'name': name, 'size': 3}], '/remote', mapping)[2]
    assert not gateway_module.map_files([{'name': 'a', 'size': 9}], '/remote', mapping)[2]
    assert not gateway_module.map_files([{'name': 'a', 'size': 3}], '/remote', [('relative', '/remote')])[2]


def test_network_exception_becomes_unknown_evidence(gateway_module):
    """失败请求也产出未知证据，不能沿用之前的失效确认。"""
    gateway = object.__new__(gateway_module.Gateway)
    def fail(*args):
        """模拟站点连接失败。"""
        raise TimeoutError('do not expose cookie')
    gateway.fetch_detail = fail
    torrent = Torrent('QB', 'a'*40, '测试', frozenset({3}), True, True, True, 999999, 1, 10, hr=False)
    snapshot = gateway_module.Snapshot(torrent, '/media', {3: [{'message': 'torrent not registered'}]}, [])
    evidence = gateway.verify(snapshot, 3, {'hash': torrent.hash, 'site_id': 3, 'url': 'https://site.test/details.php?id=1', 'verified_at': 1})
    assert evidence.site_result == 'unknown' and not evidence.confirmed_missing
    assert 'cookie' not in evidence.reason


def test_tracker_mapping_is_exact_or_site_subdomain(gateway_module):
    """相似后缀域名不能误归到目标站点。"""
    gateway = object.__new__(gateway_module.Gateway)
    gateway.sites = {3: {'id': 3, 'url': 'https://site.test/'}}
    gateway.policies = {3: SitePolicy(site_id=3, tracker_hosts=['other.test'])}
    assert gateway.resolve_site('t.site.test') == 3
    assert gateway.resolve_site('other.test') == 3
    assert gateway.resolve_site('evilsite.test') is None


def test_mteam_uses_host_api_key_without_cookie(gateway_module):
    """馒头读取宿主 API Key，不复制 Cookie 或拿登录 Token 替代。"""
    gateway = object.__new__(gateway_module.Gateway)
    gateway.sites = {2: {'url': 'https://kp.m-team.cc/', 'domain': 'm-team.cc', 'apikey': 'test-key', 'token': 'test-token', 'cookie': 'test-cookie'}}
    gateway.policies = {2: SitePolicy(site_id=2, verification='mteam')}
    headers, cookies = gateway.authentication(2)
    assert headers['x-api-key'] == 'test-key' and cookies is None
    assert headers['User-Agent']
    assert 'Authorization' not in headers and 'Cookie' not in headers
    assert gateway.api_base(2) == 'https://api.m-team.cc/api'
    gateway.sites[2]['apikey'] = ''
    with pytest.raises(ValueError, match='API Key'):
        gateway.authentication(2)


def test_partial_files_check_temporary_paths_and_hardlinks(tmp_path):
    from app.plugins.torrentcleanup.policy import file_deletion_guard
    from dataclasses import replace
    root = tmp_path / 'data'
    (root / 'complete').mkdir(parents=True)
    (root / 'incomplete').mkdir()
    (root / 'incomplete/a.!qB').write_bytes(b'ab')
    mapping = [(str(root), '/downloads')]
    prefs = {'temp_path_enabled': True, 'incomplete_files_ext': True}
    refs, paths, verified = gateway.map_incomplete_files([{'name':'a','size':10}], '/downloads/complete', '/downloads/incomplete', mapping, prefs)
    assert verified and len(paths) == 4
    assert any(x.startswith('inode:') for x in refs)
    (root / 'other').hardlink_to(root / 'incomplete/a.!qB')
    other, _, valid = gateway.map_files([{'name':'other','size':2}], '/downloads', mapping)
    t = Torrent('QB','a'*40,'partial',frozenset(),True,False,False,0,0,10,files=refs,files_verified=verified)
    assert file_deletion_guard(t,[t],True) is None
    assert file_deletion_guard(t,[t,replace(t,hash='b'*40,files=other,files_verified=valid)],True) == '文件仍被其他任务使用'
    assert not gateway.map_incomplete_files([{'name':'a','size':10}], '/downloads/complete', '', mapping, prefs)[2]
    assert not gateway.map_incomplete_files([{'name':'a','size':10}], '/downloads/complete', '/unmapped', mapping, prefs)[2]
    assert not gateway.map_incomplete_files([{'name':'a','size':10}], '/downloads/complete', '/downloads/incomplete', mapping, {})[2]


def test_missing_partial_paths_still_reject_symlinks_escape_and_oversize(tmp_path):
    root = tmp_path / 'data';root.mkdir()
    mapping=[(str(root),'/downloads')]
    assert gateway.map_files([{'name':'missing','size':10}], '/downloads', mapping, allow_incomplete=True)[2]
    assert not gateway.map_files([{'name':'missing','size':10}], '/downloads', mapping)[2]
    (root/'link').symlink_to(tmp_path/'outside')
    assert not gateway.map_files([{'name':'link/file','size':10}], '/downloads', mapping, allow_incomplete=True)[2]
    assert not gateway.map_files([{'name':'link','size':10}], '/downloads', mapping, allow_incomplete=True)[2]
    assert not gateway.map_files([{'name':'../outside','size':10}], '/downloads', mapping, allow_incomplete=True)[2]
    (root/'too-large').write_bytes(b'12345')
    assert not gateway.map_files([{'name':'too-large','size':2}], '/downloads', mapping, allow_incomplete=True)[2]


@pytest.mark.parametrize('progress,left,hours,protected',[(.2,80,0,False),(1,0,167,True),(1,0,168,False)])
def test_local_minimum_does_not_turn_unfinished_download_into_hr(progress,left,hours,protected):
    g=object.__new__(gateway.Gateway)
    g.sites={2:{'id':2,'url':'https://site.test/'}}
    g.policies={2:SitePolicy(site_id=2,hr_mode='minimum',min_seed_hours=168)}
    qbc=SimpleNamespace(torrents_info=lambda **kwargs:[{'hash':'a'*40,'name':'example','state':'error' if progress<1 else 'stalledUP','progress':progress,'amount_left':left,'seeding_time':hours*3600,'size':100,'tags':'','save_path':'/downloads','ratio':0}],torrents_trackers=lambda **kwargs:[{'url':'https://site.test/announce','status':2,'msg':''}])
    g.service=lambda name:SimpleNamespace(type='qbittorrent',instance=SimpleNamespace(qbc=qbc))
    assert g.snapshot('QB')[0].torrent.hr is protected
