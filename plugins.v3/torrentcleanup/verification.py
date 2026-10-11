"""站点复核解析：未知页面和临时错误永不作为已删种证据。"""

import re
import json
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit, urljoin, quote

from bs4 import BeautifulSoup


def detail_url(site_url: str, supplied_url: str, path: str = "/details.php", id_param: str = "id") -> str:
    """限定为配置站点同源的 NexusPHP 详情地址，剔除额外参数。"""
    root, parsed = urlsplit(site_url), urlsplit(supplied_url)
    if (root.scheme, root.hostname, root.port) != (parsed.scheme, parsed.hostname, parsed.port):
        raise ValueError("详情链接必须与已配置站点同源")
    if parsed.username or parsed.password:
        raise ValueError("详情链接不能包含认证信息")
    ids = parse_qs(parsed.query).get(id_param, [])
    if parsed.path != path or len(ids) != 1 or not ids[0].isdigit():
        raise ValueError(f"详情链接格式应为 {path}?{id_param}=数字")
    return urlunsplit((root.scheme, root.netloc, parsed.path, urlencode({id_param: ids[0]}), ""))


def detail_links(site_url: str, text: str, path: str = "/details.php", id_param: str = "id") -> list[str]:
    """从备注或搜索页提取有限的同源详情候选，链接本身不作为身份依据。"""
    values = re.findall(r'https?://[^\s<>"\x27]+', text)
    values += [a.get("href", "") for a in BeautifulSoup(text, "html.parser").find_all("a")]
    result = []
    for value in values:
        try:
            url = detail_url(site_url, urljoin(site_url, value), path, id_param)
        except ValueError:
            continue
        if url not in result:
            result.append(url)
        if len(result) >= 100:
            break
    return result


def search_url(site_url: str, template: str, torrent_hash: str, name: str) -> str:
    """将已验证模板的查询占位符逐个编码，禁止名字变成附加参数。"""
    title = re.split(r"[._\s]+(?:S\d{1,2}|(?:19|20)\d{2})", name, maxsplit=1, flags=re.I)[0]
    english = re.search(r"[A-Za-z][A-Za-z .'-]{3,}", title)
    keyword = re.sub(r"[._]+", " ", english.group(0)).strip() if english else re.split(r"[.\s]", name)[0]
    values = {"hash": torrent_hash, "name": name, "keyword": keyword}
    return urljoin(site_url, template.format(**{k: quote(v, safe="") for k, v in values.items()}))


def authenticated(html: str) -> bool:
    """要求同时看到退出入口和用户详情入口，拒绝登录及验证页面。"""
    soup = BeautifulSoup(html, "html.parser")
    if soup.select('input[type="password"]'):
        return False
    links = [a.get("href", "").lower() for a in soup.find_all("a")]
    # 观众新版把自己的退出入口放在用户栏菜单数据中，不在普通链接里。
    for node in soup.select('.site-userbar__name[data-uploader-actions]'):
        try:
            actions = json.loads(node.get('data-uploader-actions', '[]'))
            links.extend(a.get('href', '').lower() for a in actions if isinstance(a, dict))
        except (ValueError, TypeError):
            pass
    return any("logout" in h for h in links) and any("userdetails.php?id=" in h for h in links)


def parse_detail(html: str, torrent_hash: str, was_bound: bool) -> str:
    """先验证页面登录状态，再只接受明确 Hash 或已绑定详情的删除提示。"""
    if not authenticated(html):
        return "unknown"
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    hashes = re.findall(r"(?:Hash\s*(?:码|Code)?|Info\s*Hash)\s*[:：]?\s*([0-9a-f]{40})", text, re.I)
    for node in soup.select('input#torrent_hash, a#copyTorrentHashLink[data-hash]'):
        value = node.get('data-hash') or node.get('value', '')
        match = re.fullmatch(r'(?:hash=)?([0-9a-f]{40})', value, re.I)
        if match:
            hashes.append(match.group(1))
    if torrent_hash.lower() in [h.lower() for h in hashes]:
        return "active"
    if hashes:
        return "unknown"
    missing = re.search(r"该种子已被删除|种子不存在|没有该种子|不存在该种子|torrent (?:does not exist|has been deleted)|invalid torrent id", text, re.I)
    return "missing" if was_bound and missing else "unknown"


def tracker_result(trackers: list[dict]) -> str:
    """同站点任一 Tracker 正常即有效，所有 Tracker 明确不存在才算失效。"""
    if not trackers:
        return "unknown"
    states = []
    for item in trackers:
        if item.get("success") is True:
            return "active"
        message = str(item.get("message") or "").lower()
        transient = re.search(r"timeout|timed out|respond|connect|unreachable|unauthor|passkey|banned|disabled|not authorized|limit|too many|暂停|连接|超时|认证", message)
        missing = re.search(r"torrent (?:not registered|does not exist|not found|has been deleted)|unregistered torrent|种子不存在|种子已被删除", message)
        states.append("missing" if missing and not transient else "unknown")
    return "missing" if all(s == "missing" for s in states) else "unknown"
