"""馒头只读 API 适配，复用宿主 API Key，未验证的错误码不作为删种证据。"""

import json
import re
import time
from dataclasses import asdict
from urllib.parse import urlsplit, urlunsplit, parse_qs

from .policy import Evidence
from .verification import search_url


def canonical_url(site_url, supplied):
    """规范化本站 /detail/数字 链接，拒绝跨域和嵌入凭据。"""
    root, target = urlsplit(site_url), urlsplit(supplied)
    if (root.scheme, root.hostname, root.port) != (target.scheme, target.hostname, target.port) or target.username or target.password:
        raise ValueError('详情链接必须与 MoviePilot 已配置站点同源')
    if not re.fullmatch(r'/detail/\d+/?', target.path):
        raise ValueError('馒头详情路径应为 /detail/数字')
    return urlunsplit((root.scheme, root.netloc, target.path.rstrip('/'), '', ''))


def candidates_from_text(site_url, text, numeric_comment=False):
    """下载器备注可以是纯种子 ID，历史记录仅采纳本站完整详情链接。"""
    values = re.findall(r'https?://[^\s<>"\x27]+', text or '')
    if numeric_comment and (text or '').strip().isdigit():
        values.insert(0, site_url.rstrip('/') + '/detail/' + text.strip())
    result = []
    for value in values:
        try:
            candidate = canonical_url(site_url, value)
            if candidate not in result:
                result.append(candidate)
        except ValueError:
            continue
    return result[:10]


def parse_identity(payload, torrent_id, torrent_hash):
    """仅接受成功响应中相同 ID 与精确 Hash，缺少 Hash 或任何错误均保持未知。"""
    if not isinstance(payload, dict):
        return False
    data = payload.get('data')
    if str(payload.get('code')) != '0' or not isinstance(data, dict) or str(data.get('id')) != str(torrent_id):
        return False
    hashes = [str(data.get(key, '')).lower() for key in ['hash', 'infoHash', 'info_hash', 'torrentHash']]
    return torrent_hash.lower() in hashes and bool(re.fullmatch(r'[0-9a-f]{40}', torrent_hash.lower()))


def check_mteam(verifier, row, site_id, supplied_url, force, base, tracker_note):
    """优先已有关联和下载器备注，自动请求限流、凭据变更与缓存仍由统一逻辑管理。"""
    from .automatic import RequestDeferred
    gateway = verifier.gateway
    profile, site = gateway.policies[site_id], gateway.sites[site_id]
    key = f'{site_id}:{row.torrent.hash}'
    bindings, caches = verifier.read('bindings') or {}, verifier.read('site_checks') or {}
    binding, cache = bindings.get(key) or {}, caches.get(key) or {}
    fingerprint = verifier.fingerprint(site_id)
    if supplied_url:
        supplied_url = canonical_url(site['url'], supplied_url)
    if (not force and cache.get('fingerprint') == fingerprint
            and 0 <= time.time() - cache.get('checked_at', 0) < verifier.cache_seconds(profile, cache)
            and (not supplied_url or supplied_url == cache.get('detail_url'))):
        fields = {k: cache[k] for k in ['checked_at', 'site_result', 'authenticated', 'binding_verified', 'detail_url', 'source']}
        return Evidence(**{**base, **fields}, reason=tracker_note + '，' + cache['note'] + '（缓存）', cached=True)
    found, source, note, active, can_cache = '', '', '未找到可以核实身份的馒头详情', False, True
    identity_hash, hash_verified_at = '', 0
    try:
        gateway.authentication(site_id)
        known = supplied_url or binding.get('url')
        candidates = [(canonical_url(site['url'], known), binding.get('source', '手动链接'))] if known else []
        if not candidates and profile.auto_discover:
            candidates = [(u, '下载历史') for u in gateway.history_candidates(site_id, row.torrent.hash)]
            candidates += [(u, '下载器备注') for u in gateway.comment_candidates(site_id, row)]
            if not candidates:
                keyword = parse_qs(urlsplit(search_url(site['url'], '/?q={keyword}', row.torrent.hash, row.torrent.name)).query)['q'][0]
                body = verifier.fetch(site_id, gateway.api_base(site_id) + '/torrent/search', method='POST',
                                      json_body={'keyword': keyword, 'categories': [], 'pageNumber': 1, 'pageSize': 20, 'visible': 1})
                payload = json.loads(body or '{}')
                data = payload.get('data') or {}
                results = data.get('data', []) if isinstance(data, dict) and str(payload.get('code')) == '0' else []
                results = sorted((r for r in results if isinstance(r, dict) and str(r.get('id', '')).isdigit()),
                                 key=lambda r: abs(float(r.get('size') or 0) - row.torrent.size))
                candidates = [(site['url'].rstrip('/') + '/detail/' + str(r['id']), '站点 API 搜索') for r in results]
        for candidate, origin in list(dict.fromkeys(candidates))[:profile.max_detail_candidates]:
            torrent_id = urlsplit(candidate).path.rsplit('/', 1)[-1]
            body = verifier.fetch(site_id, gateway.api_base(site_id) + '/torrent/detail', method='POST', data={'id': torrent_id})
            payload = json.loads(body or '{}')
            if parse_identity(payload, torrent_id, row.torrent.hash):
                active, found, source = True, candidate, origin
                identity_hash,hash_verified_at=row.torrent.hash,time.time()
                note = '馒头 API 详情 ID 与 Hash 一致，种子存在'
                break
            detail = payload.get('data') if isinstance(payload, dict) else None
            valid_detail = isinstance(detail,dict) and str(payload.get('code'))=='0' and str(detail.get('id'))==torrent_id
            no_hash = isinstance(detail,dict) and not any(detail.get(k) for k in ['hash','infoHash','info_hash','torrentHash'])
            if valid_detail and no_hash:
                if (binding.get('url')==candidate and binding.get('hash')==row.torrent.hash
                        and binding.get('identity_hash')==row.torrent.hash and binding.get('hash_verified_at')):
                    active,found,source=True,candidate,'已验证身份关联'
                    identity_hash,hash_verified_at=row.torrent.hash,binding['hash_verified_at']
                    note='详情确认种子存在，复用此前已验证的 Hash 与站点 ID 关联'
                    break
                if profile.allow_torrent_verification:
                    from .metadata import verify_metadata
                    if verify_metadata(verifier,site_id,candidate,row.torrent.hash):
                        active,found,source=True,candidate,'种子文件 Hash 核验'
                        identity_hash,hash_verified_at=row.torrent.hash,time.time()
                        note='种子文件 Hash 与下载器一致，详情确认种子存在'
                        break
                note = '馒头 API 未提供 Hash（infoHash 为空），无法与下载器核实身份，保留'
            else:
                if valid_detail and not no_hash and binding.get('url')==candidate:
                    bindings.pop(key,None);verifier.save('bindings',bindings);binding={}
                note = '馒头 API 未返回可核实的同一 ID 与 Hash，保留，不据此判定失效'
    except RequestDeferred as error:
        note, can_cache = str(error), False
    except ValueError as error:
        note = str(error) if str(error).startswith(('馒头 API 返回 HTTP', 'MoviePilot 本站未配置', '种子下载重定向域名')) else '馒头 API 响应或种子文件身份无法核实'
    except Exception:
        note = '馒头 API 连接失败，保留，请检查宿主站点凭据和网络'
    now = time.time()
    if active:
        bindings[key] = {'site_id': site_id, 'hash': row.torrent.hash, 'url': found, 'source': source, 'verified_at': now,
                         'identity_hash':identity_hash,'hash_verified_at':hash_verified_at}
        verifier.save('bindings', bindings)
    evidence = Evidence(**{**base, 'checked_at': now, 'site_result': 'active' if active else 'unknown',
                           'authenticated': active, 'binding_verified': active},
                        reason=tracker_note + '，' + note, detail_url=found, source=source)
    if can_cache:
        caches[key] = {**asdict(evidence), 'fingerprint': fingerprint, 'note': note}
        verifier.save('site_checks', caches)
    return evidence
