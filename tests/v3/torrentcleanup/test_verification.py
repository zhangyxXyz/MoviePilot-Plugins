"""失效判断必须依赖已登录且已绑定的明确证据。"""
import pytest
from app.plugins.torrentcleanup.verification import detail_url, parse_detail, tracker_result, search_url, detail_links


def test_search_query_cannot_inject_parameters():
    """名字中的与号会被编码，关键词去除季号和格式后缀。"""
    from urllib.parse import parse_qs, urlsplit
    url = search_url('https://site.test', '/torrents.php?search={name}', HASH, 'name&passkey=oops')
    assert parse_qs(urlsplit(url).query) == {'search': ['name&passkey=oops']}
    url = search_url('https://site.test', '/torrents.php?search={keyword}', HASH,
                     '奔跑吧.Keep.Running.The.Heavenly.Road.S01.2025.2160p')
    assert parse_qs(urlsplit(url).query)['search'] == ['Keep Running The Heavenly Road']


def test_detail_candidates_ignore_cross_origin_and_strip_extra_parameters():
    """从链接提取线索时不保留 passkey，不接受外站页面。"""
    html = '<a href="/details.php?id=7&passkey=secret">标题</a><a href="https://evil.test/details.php?id=8">标题</a>'
    assert detail_links('https://site.test', html) == ['https://site.test/details.php?id=7']

AUTH = '<a href="logout.php">退出</a><a href="userdetails.php?id=1">账号</a>'
HASH = 'a' * 40


@pytest.mark.parametrize('body,bound,expected', [
    ('种子不存在', False, 'unknown'), ('种子不存在', True, 'missing'),
    ('Cloudflare 验证', True, 'unknown'), ('404 Not Found', True, 'unknown'),
    ('Hash码：' + HASH, False, 'active'), ('Hash码：' + 'b'*40 + ' 种子不存在', True, 'unknown'),
    ('<input type="password">种子不存在', True, 'unknown'),
    ('<script>种子不存在</script>', True, 'unknown'),
])
def test_detail_evidence(body, bound, expected):
    """认证、页面身份、错误类型分别满足才可确认。"""
    assert parse_detail(AUTH + body, HASH, bound) == expected
    assert parse_detail(body, HASH, bound) == 'unknown'


def test_urls_never_forward_cookie_cross_origin():
    """禁止跨域、跨协议和不同端口详情请求。"""
    for url in ['https://evil.test/details.php?id=1', 'http://site.test/details.php?id=1',
                'https://site.test:444/details.php?id=1', 'https://site.test/download.php?id=1']:
        with pytest.raises(ValueError): detail_url('https://site.test/', url)
    assert detail_url('https://site.test/', 'https://site.test/details.php?id=1&passkey=secret') == 'https://site.test/details.php?id=1'


def test_tracker_requires_all_endpoints_missing():
    """任一成功或不确定的 Tracker 都不能判定为失效。"""
    missing = {'success': False, 'message': 'torrent not registered'}
    assert tracker_result([missing]) == 'missing'
    assert tracker_result([missing, {'success': True}]) == 'active'
    assert tracker_result([missing, {'message': 'connection timeout'}]) == 'unknown'
    assert tracker_result([{'message': 'torrent not found: passkey disabled'}]) == 'unknown'


def test_audiences_menu_and_hidden_hash():
    """观众新版用户菜单和隐藏 Hash 必须同时满足登录与精确身份核验。"""
    auth = '''<span class="site-userbar__name" data-uploader-actions='[{"href":"logout.php"}]'></span><a href="userdetails.php?id=1">账号</a>'''
    hidden = f'<input type="hidden" id="torrent_hash" value="hash={HASH}">'
    assert parse_detail(auth + hidden, HASH, False) == 'active'
    assert parse_detail(hidden, HASH, False) == 'unknown'
    assert parse_detail(auth + hidden, 'b' * 40, True) == 'unknown'
    assert parse_detail(auth + f'<div data-hash="{HASH}">用户评论</div>', HASH, False) == 'unknown'
    assert parse_detail(auth + f'<a id="copyTorrentHashLink" data-hash="{HASH}">复制</a>', HASH, False) == 'active'
