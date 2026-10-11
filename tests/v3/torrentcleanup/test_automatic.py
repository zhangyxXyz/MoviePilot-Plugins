"""自动发现和缓存必须保持身份核验、观察期及站点请求限额。"""

import copy
from types import SimpleNamespace

import pytest

from app.plugins.torrentcleanup import automatic
from app.plugins.torrentcleanup.automatic import SiteVerifier
from app.plugins.torrentcleanup.models import SitePolicy, BindPayload
from app.plugins.torrentcleanup.policy import Torrent, observe, Observation
from app.plugins.torrentcleanup.verification import detail_url

HASH = 'a' * 40
AUTH = '<a href="logout.php">退出</a><a href="userdetails.php?id=1">账号</a>'
URL = 'https://site.test/details.php?id=1'


class Clock:
    """使用可推进的时间，避免测试实际等待。"""
    def __init__(self):
        """设置远离零的初始时间戳。"""
        self.now = 100000

    def time(self):
        """返回模拟时间。"""
        return self.now

    def monotonic(self):
        """返回用于请求间隔的单调时间。"""
        return self.now

    def sleep(self, seconds):
        """直接推进时钟。"""
        self.now += seconds


class Gateway:
    """返回离线历史线索、搜索结果和详情页。"""
    def __init__(self):
        """准备一个站点及真实匹配的测试页面。"""
        self.policies = {3: SitePolicy(site_id=3, verification='nexusphp')}
        self.sites = {3: {'url': 'https://site.test/', 'cookie': 'fake-cookie'}}
        self.history, self.comments, self.search = [URL], [], []
        self.pages = {URL: AUTH + 'Hash码：' + HASH, 'https://site.test/search': AUTH}
        self.requests = []

    def canonical_detail(self, site_id, url):
        """约束候选为本站详情。"""
        return detail_url(self.sites[site_id]['url'], url)

    def history_candidates(self, *args):
        """返回历史候选。"""
        return self.history

    def comment_candidates(self, *args):
        """返回下载器备注候选。"""
        return self.comments

    def search_address(self, *args):
        """返回固定搜索页面。"""
        return 'https://site.test/search'

    def search_candidates(self, *args):
        """返回虚构搜索结果。"""
        return self.search

    def fetch_site_page(self, site_id, url):
        """记录访问次数，返回离线页面。"""
        self.requests.append(url)
        return self.pages.get(url)


@pytest.fixture
def setup_check(monkeypatch):
    """创建离线网关、复制语义存储与可控制时间。"""
    clock = Clock()
    monkeypatch.setattr(automatic, 'time', clock)
    data = {}
    def read(key):
        """模拟持久化读取。"""
        return copy.deepcopy(data.get(key))
    def save(key, value):
        """模拟持久化写入。"""
        data[key] = copy.deepcopy(value)
    gateway = Gateway()
    verifier = SiteVerifier(gateway, read, save, lambda: False)
    torrent = Torrent('TR', HASH, '示例', frozenset({3}), True, True, True, 999999, 1, 100, hr=False)
    row = SimpleNamespace(torrent=torrent, trackers={3: [{'success': True}]})
    return verifier, gateway, row, data, clock


def test_empty_link_uses_history_and_caches_verified_binding(setup_check):
    """空链接自动核验下载历史，二次点击不再次访问站点。"""
    verifier, gateway, row, data, clock = setup_check
    result = verifier.check(row, 3)
    assert result.site_result == 'active' and result.source == '下载历史'
    assert data['bindings']['3:' + HASH]['url'] == URL
    clock.now += 100
    cached = verifier.check(row, 3)
    assert cached.cached and cached.checked_at == result.checked_at
    assert gateway.requests == [URL]
    assert BindPayload(downloader='TR', hash=HASH, site_id=3, url='').url == ''


@pytest.mark.parametrize('trackers', [[{'success': True}], [{'success': False, 'message': 'timeout'}], [{'success': False, 'message': 'invalid passkey'}], []])
def test_normal_and_uncertain_tracker_skip_site_requests(setup_check, trackers):
    """自动扫描仅明确疑似失效访问站点，正常和网络认证异常均不请求。"""
    verifier, gateway, row, data, clock = setup_check
    row.trackers[3] = trackers
    result = verifier.check(row, 3, only_suspected=True)
    assert result.site_skipped and not result.confirmed_missing
    assert gateway.requests == []


def test_search_candidate_requires_exact_hash(setup_check):
    """同名但不同 Hash 的第一条结果不能抢先建立关联。"""
    verifier, gateway, row, data, clock = setup_check
    gateway.history = []
    gateway.policies[3].search_url_template = '/search?q={hash}'
    wrong = 'https://site.test/details.php?id=2'
    gateway.pages[wrong] = AUTH + 'Hash码：' + 'b' * 40
    gateway.search = [wrong, URL]
    result = verifier.check(row, 3)
    assert result.site_result == 'active' and result.source == '站点搜索'
    assert data['bindings']['3:' + HASH]['url'] == URL
    assert len(gateway.requests) == 3


def test_search_empty_does_not_prove_missing(setup_check):
    """搜索无结果只能产生未知状态，不能创建失效绑定。"""
    verifier, gateway, row, data, clock = setup_check
    gateway.history = []
    gateway.policies[3].search_url_template = '/search?q={hash}'
    row.trackers = {3: [{'message': 'torrent not registered'}]}
    result = verifier.check(row, 3)
    assert result.site_result == 'unknown' and not result.confirmed_missing
    assert not data.get('bindings')


def test_fresh_execution_check_respects_hourly_budget(setup_check):
    """执行前强制复核也不能突破站点额度；拿不到新证据就跳过。"""
    verifier, gateway, row, data, clock = setup_check
    gateway.policies[3].max_requests_per_hour = 1
    assert verifier.check(row, 3).site_result == 'active'
    result = verifier.check(row, 3, force=True)
    assert result.site_result == 'unknown' and '限额' in result.reason
    assert len(gateway.requests) == 1


def test_cached_missing_never_increments_observation_count(setup_check):
    """缓存携带原始采样时间，重复使用不累计确认次数。"""
    verifier, gateway, row, data, clock = setup_check
    verifier.check(row, 3)
    clock.now += 21600
    gateway.pages[URL] = AUTH + '该种子已被删除'
    row.trackers = {3: [{'message': 'torrent not registered'}]}
    fresh = verifier.check(row, 3)
    record = observe(None, fresh, 6)
    assert fresh.confirmed_missing
    clock.now += 10
    cached = verifier.check(row, 3)
    assert cached.cached and observe(record, cached, 6) == record
    row.trackers = {3: [{'success': True}]}
    recovered = verifier.check(row, 3)
    assert recovered.tracker_result == 'active'
    assert observe(record, recovered, 6) is None


def test_cookie_change_invalidates_cached_evidence(setup_check):
    """更换认证后重新访问，而不是继续沿用旧登录证据。"""
    verifier, gateway, row, data, clock = setup_check
    verifier.check(row, 3)
    gateway.sites[3]['cookie'] = 'new-fake-cookie'
    gateway.pages[URL] = '<input type="password">'
    result = verifier.check(row, 3)
    assert result.site_result == 'unknown' and not result.authenticated
    assert len(gateway.requests) == 2


def test_rate_change_keeps_valid_cache_and_shorter_interval_expires_it(setup_check):
    """调高请求额度不导致旧证据全部重取，但缩短缓存间隔立即生效。"""
    verifier, gateway, row, data, clock = setup_check
    verifier.check(row, 3)
    gateway.policies[3].max_requests_per_hour = 120
    clock.now += 7200
    assert verifier.check(row, 3).cached and len(gateway.requests) == 1
    gateway.policies[3].check_interval_hours = 1
    assert not verifier.check(row, 3).cached and len(gateway.requests) == 2


def test_manual_foreign_link_is_rejected_without_request(setup_check):
    """手动 URL 也不能把站点 Cookie 带到第三方。"""
    verifier, gateway, row, data, clock = setup_check
    with pytest.raises(ValueError):
        verifier.check(row, 3, supplied_url='https://evil.test/details.php?id=1')
    assert not gateway.requests


def test_search_rotates_candidates_after_cache_expiry(setup_check):
    """候选超过单次限额时，下个周期接续检查而非永远重复前几项。"""
    verifier, gateway, row, data, clock = setup_check
    gateway.history = []
    gateway.policies[3].search_url_template = '/search?q={keyword}'
    gateway.policies[3].max_detail_candidates = 1
    wrong = 'https://site.test/details.php?id=2'
    gateway.search = [wrong, URL]
    gateway.pages[wrong] = AUTH + 'Hash码：' + 'b' * 40
    assert verifier.check(row, 3).site_result == 'unknown'
    clock.now += 21600
    assert verifier.check(row, 3).site_result == 'active'
    assert data['bindings']['3:' + HASH]['url'] == URL
