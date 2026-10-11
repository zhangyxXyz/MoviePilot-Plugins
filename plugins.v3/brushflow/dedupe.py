"""Read-only downloader identity checks; titles never establish torrent identity."""
import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit


class DuplicateTorrent(Exception):
    pass


class IdentityUnavailable(Exception):
    pass


def detail_keys(text):
    """Only explicit detail URLs establish a site + torrent ID pair."""
    result = set()
    for url in re.findall(r'https?://[^\s<>"\']+', str(text or '')):
        parsed = urlsplit(url.rstrip(').,;'))
        host = (parsed.hostname or '').lower()
        path = parsed.path.rstrip('/')
        tid = None
        if path.endswith('/details.php'):
            tid = parse_qs(parsed.query).get('id', [None])[0]
        else:
            match = re.fullmatch(r'/(?:browse/)?(?:detail|details|torrent)/(\d+)', path)
            if match:
                tid = match.group(1)
        if host and tid and str(tid).isdigit():
            result.add((host, str(int(tid))))
    return result


def content_hashes(content):
    """Hash the original info bytes, including BEP 52 v2 identity when present."""
    if isinstance(content, str):
        if not content.startswith('magnet:?'):
            raise IdentityUnavailable('无法读取种子内容，暂缓新增')
        hashes = set()
        for xt in parse_qs(urlsplit(content).query).get('xt', []):
            if xt.lower().startswith('urn:btih:'):
                value = xt[9:]
                if len(value) == 32:
                    try:
                        value = base64.b32decode(value.upper()).hex()
                    except ValueError:
                        continue
                if re.fullmatch(r'[a-fA-F0-9]{40}', value):
                    hashes.add(value.lower())
            elif re.fullmatch(r'urn:btmh:1220[a-fA-F0-9]{64}', xt):
                hashes.add(xt[13:].lower())
        if not hashes:
            raise IdentityUnavailable('磁力链接缺少有效 Hash，暂缓新增')
        return hashes
    if not isinstance(content, bytes) or len(content) > 32 * 1024 * 1024:
        raise IdentityUnavailable('种子内容无法核实，暂缓新增')
    def read(pos, depth=0):
        if depth > 100:
            raise ValueError('nesting')
        start = pos
        ch = content[pos:pos+1]
        if ch == b'i':
            end = content.index(b'e', pos)
            return int(content[pos+1:end]), end + 1
        if ch in (b'd', b'l'):
            pos += 1
            values = []
            while content[pos:pos+1] != b'e':
                value, pos = read(pos, depth+1)
                values.append(value)
            if ch == b'd':
                if len(values) % 2:
                    raise ValueError('dict')
                return dict(zip(values[::2], values[1::2])), pos+1
            return values, pos+1
        colon = content.index(b':', pos)
        size = int(content[pos:colon])
        if size < 0 or colon + 1 + size > len(content):
            raise ValueError('length')
        return content[colon+1:colon+1+size], colon+1+size
    try:
        if content[:1] != b'd':
            raise ValueError('root')
        pos, info_bytes, info = 1, None, None
        while content[pos:pos+1] != b'e':
            key, pos = read(pos)
            start = pos
            value, pos = read(pos)
            if key == b'info':
                if info_bytes is not None:
                    raise ValueError('duplicate info')
                info_bytes, info = content[start:pos], value
        if pos+1 != len(content) or not isinstance(info, dict) or not info:
            raise ValueError('info')
        hashes = set()
        if b'pieces' in info:
            hashes.add(hashlib.sha1(info_bytes).hexdigest())
        if info.get(b'meta version') == 2:
            digest = hashlib.sha256(info_bytes).hexdigest()
            hashes.update((digest, digest[:40]))
        if not hashes:
            raise ValueError('unsupported')
        return hashes
    except (ValueError, IndexError, TypeError, RecursionError):
        raise IdentityUnavailable('种子 Hash 解析失败，暂缓新增') from None


def inventory(service, include_details=False):
    """Read every task, including IYUU/unmanaged tasks, without changing tags."""
    hashes, details = set(), set()
    try:
        if service.type == 'qbittorrent':
            client = service.instance.qbc
            rows = client.torrents_info()
            if rows is None:
                raise ValueError('missing inventory')
            for row in rows:
                value = row.get('hash')
                if not value:
                    raise ValueError('missing hash')
                hashes.add(value.lower())
                if include_details:
                    props = client.torrents_properties(torrent_hash=value)
                    if props is None or 'comment' not in props:
                        raise ValueError('missing properties')
                    details.update(detail_keys(props['comment']))
        elif service.type == 'transmission':
            rows = service.instance.trc.get_torrents(arguments=['hashString', 'comment'])
            if rows is None:
                raise ValueError('missing inventory')
            for row in rows:
                value = row.get('hashString') if isinstance(row, dict) else row.hashString
                if not value:
                    raise ValueError('missing hash')
                hashes.add(value.lower())
                if include_details:
                    comment = row.get('comment') if isinstance(row, dict) else row.comment
                    details.update(detail_keys(comment))
        else:
            raise ValueError('unsupported downloader')
        return hashes, details
    except Exception:
        raise IdentityUnavailable('下载器判重查询失败，本轮暂缓新增') from None
