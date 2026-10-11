"""元信息身份核验、凭据边界、长期身份关联与分类配额测试。"""
import copy
import hashlib
import json
from types import SimpleNamespace
import pytest
from app.plugins.torrentcleanup.metadata import info_hash, validate_download_url
from app.plugins.torrentcleanup.automatic import SiteVerifier, RequestDeferred
from app.plugins.torrentcleanup.models import SitePolicy
from app.plugins.torrentcleanup.quota import inspect_quota
from app.plugins.torrentcleanup.policy import Torrent

META=b'd4:infod4:name4:demo6:lengthi1eee'
HASH=hashlib.sha1(b'd4:name4:demo6:lengthi1ee').hexdigest()


def test_raw_info_bytes_and_invalid_payloads():
    """直接计算原始 info，拒绝 HTML、截断、重复字段、额外尾部及非字典。"""
    assert info_hash(META)==HASH
    for data in [b'<html>',META[:-1],META+b'extra',b'd4:info1:ae',b'd4:infode4:infodee']:
        with pytest.raises((ValueError,IndexError)): info_hash(data)


def test_download_redirect_allowlist():
    """认证地址与分发域名必须显式允许，拒绝相似域名、HTTP 和嵌入认证。"""
    roots=['https://api.site.test/api']
    assert validate_download_url('https://cdn.test/file',roots,['cdn.test'])
    for url in ['https://evilcdn.test/file','http://cdn.test/file','https://user:secret@cdn.test/file','https://cdn.test:444/file']:
        with pytest.raises(ValueError):validate_download_url(url,roots,['cdn.test'])


def test_dual_download_windows_and_zero():
    """小时与滚动 24 小时同时生效，零配额阻止请求，边界时刻释放。"""
    p=SitePolicy(site_id=1,torrent_downloads_per_hour=2,torrent_downloads_per_day=3)
    limits=inspect_quota(p,[1000,5000,5010],'download',5020)
    assert all(v['blocked'] for v in limits)
    assert limits[0]['next_at']==8600 and limits[1]['next_at']==87400
    assert not inspect_quota(p,[1000,5000,5010],'download',87400)[1]['blocked']
    p.torrent_downloads_per_hour=0
    assert inspect_quota(p,[],'download',5020)[0]['blocked']


@pytest.fixture
def mteam_case(monkeypatch):
    """站点响应不提供 Hash，元信息文件可以核实身份。"""
    from app.plugins.torrentcleanup import metadata,automatic,mteam
    now=SimpleNamespace(value=200000)
    clock=SimpleNamespace(time=lambda:now.value,monotonic=lambda:now.value,sleep=lambda n:setattr(now,'value',now.value+n))
    monkeypatch.setattr(automatic,'time',clock);monkeypatch.setattr(mteam,'time',clock)
    data={};requests=[];downloads=[]
    p=SitePolicy(site_id=2,verification='mteam',allow_torrent_verification=True,max_requests_per_hour=120)
    def fetch(site,url,**kwargs):
        requests.append(url)
        return json.dumps({'code':'0','data':{'id':'1','infoHash':None}} if url.endswith('/detail') else {'code':'0','data':'https://api.site.test/file'})
    g=SimpleNamespace(policies={2:p},sites={2:{'url':'https://site.test/','apikey':'test'}},authentication=lambda _:({},None),
        api_base=lambda _:'https://api.site.test/api',history_candidates=lambda *_:[],comment_candidates=lambda *_:['https://site.test/detail/1'],fetch_site_page=fetch)
    def fetch_meta(*args):downloads.append(1);return META
    monkeypatch.setattr(metadata,'fetch_metadata',fetch_meta)
    verifier=SiteVerifier(g,lambda key:copy.deepcopy(data.get(key)),lambda k,v:data.update({k:copy.deepcopy(v)}),lambda:False)
    row=SimpleNamespace(torrent=Torrent('TR',HASH,'样本',frozenset({2}),True,True,True,999999,1,100,hr=False),trackers={2:[{'success':True}]})
    return verifier,g,row,data,now,requests,downloads


def test_verified_identity_survives_status_expiry_and_credential_change(mteam_case):
    """存在缓存到期或凭据改变仍需请求详情，但已验证 Hash 关联不重复下载。"""
    v,g,row,data,now,requests,downloads=mteam_case
    result=v.check(row,2)
    assert result.site_result=='active' and len(downloads)==1
    assert v.check(row,2).cached and len(requests)==2
    now.value+=7*3600
    assert v.check(row,2).site_result=='active' and len(requests)==3 and len(downloads)==1
    g.sites[2]['apikey']='changed'
    assert not v.check(row,2).cached and len(downloads)==1


def test_download_quota_blocks_before_token_and_does_not_cache(mteam_case):
    """额度用完不能请求令牌，也不能把限流存成种子失效。"""
    v,g,row,data,now,requests,downloads=mteam_case
    g.policies[2].torrent_downloads_per_day=0
    result=v.check(row,2)
    assert result.site_result=='unknown' and '限额' in result.reason
    assert len(requests)==1 and not downloads and not data.get('site_checks')


def test_normal_automatic_scan_never_downloads(mteam_case):
    """允许元信息备用核验也不改变 Tracker 正常自动跳过的约定。"""
    v,g,row,data,now,requests,downloads=mteam_case
    assert v.check(row,2,only_suspected=True).site_skipped
    assert not requests and not downloads


def test_failed_attempt_persists_category_quota(mteam_case):
    """网络失败仍占用额度，新核验器读取同一份持久化计数。"""
    v,g,row,data,now,requests,downloads=mteam_case
    g.policies[2].detail_requests_per_hour=1
    def fail(*args,**kwargs):raise TimeoutError()
    g.fetch_site_page=fail
    with pytest.raises(TimeoutError):v.fetch(2,g.api_base(2)+'/torrent/detail')
    other=SiteVerifier(g,v.read,v.save,lambda:False)
    with pytest.raises(RequestDeferred):other.fetch(2,g.api_base(2)+'/torrent/detail')
    assert len(data['site_request_rates']['2:detail'])==1
