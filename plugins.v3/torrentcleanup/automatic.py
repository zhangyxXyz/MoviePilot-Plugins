"""自动详情发现、持久化缓存和站点请求节流。"""

import hashlib
import json
import time
from dataclasses import asdict

from .policy import Evidence
from .verification import authenticated, parse_detail, tracker_result
from .quota import TIMING_FIELDS, inspect_quota


class RequestDeferred(Exception):
    """本站请求额度耗尽或插件停止，等待后续周期。"""


class SiteVerifier:
    """把候选地址与已核实绑定分开保存，只有页面 Hash 匹配才能绑定。"""

    def __init__(self, gateway, read, save, stopped):
        """注入下载器网关与宿主存储，不创建独立后台线程。"""
        self.gateway, self.read, self.save, self.stopped = gateway, read, save, stopped

    def fingerprint(self, site_id):
        """配置或 Cookie 变化后失效缓存，凭据只参与摘要而不落盘。"""
        site = self.gateway.sites[site_id]
        policy = self.gateway.policies[site_id].model_dump(exclude=TIMING_FIELDS)
        payload = [policy, site.get("url"), site.get("cookie"), site.get("ua"), site.get('apikey'), site.get('token'), 'ua-fallback-v1']
        if self.gateway.policies[site_id].verification == 'mteam':
            payload.extend([self.gateway.api_base(site_id), 'mteam-infohash-v2'])
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def fetch(self, site_id, url, **request):
        """每次站点访问先持久化额度，跨任务及重载共享小时限额和最小间隔。"""
        category = request.pop('category', None)
        if category is None:
            category = 'search' if '/torrent/search' in url else 'detail'
        self.reserve(site_id, category)
        return self.gateway.fetch_site_page(site_id, url, **request)

    def reserve(self, site_id, category=None, global_request=True):
        """所有请求计入总预算，类别额度在网络调用前预占且不因失败退款。"""
        profile = self.gateway.policies[site_id]
        rates = self.read("site_request_rates") or {}
        now = time.time()
        category_key = f'{site_id}:{category}'
        events = rates.get(category_key, [])
        for window in inspect_quota(profile, events, category, now):
            if window['blocked']:
                raise RequestDeferred(window['title'] + '限额已用完，等待额度恢复')
        if not global_request:
            rates[category_key] = [v for v in events if 0 <= now-v < 86400] + [now]
            self.save('site_request_rates', rates)
            return
        previous = rates.get(str(site_id), [])
        times = [v for v in previous if 0 <= now - v < 3600]
        if any(v > now for v in previous) or len(times) >= profile.max_requests_per_hour:
            raise RequestDeferred("本站每小时请求限额已用完，等待后续周期")
        wait = max(0, profile.request_gap_seconds - (now - times[-1])) if times else 0
        end = time.monotonic() + wait
        progress = getattr(self.gateway, "progress", None)
        site_name = self.gateway.sites[site_id].get("name") or f"站点 {site_id}"
        if wait and progress:
            progress(phase="waiting", message=f"{site_name}：等待请求间隔（{wait:.1f} 秒）")
        while time.monotonic() < end:
            if self.stopped():
                raise RequestDeferred("插件已停止")
            time.sleep(min(0.2, max(0, end - time.monotonic())))
        if self.stopped():
            raise RequestDeferred("插件已停止")
        times.append(time.time())
        rates[str(site_id)] = times
        if category:
            rates[category_key] = [v for v in events if 0 <= now-v < 86400] + [time.time()]
        self.save("site_request_rates", rates)
        if progress:
            progress(phase="request", message=f"{site_name}：正在请求站点页面", request_count_delta=1)

    def cache_seconds(self, profile, cache):
        """存在结果短期缓存，未知或失败仅短暂退避，身份关联独立长期保存。"""
        return profile.check_interval_hours*3600 if cache.get('site_result') == 'active' else profile.error_cache_minutes*60

    def check(self, row, site_id, supplied_url="", force=False, only_suspected=False):
        """优先绑定及历史记录，再按配置搜索，缓存不会增加失效观察次数。"""
        torrent = row.torrent
        tracker = tracker_result(row.trackers.get(site_id, []))
        key = f"{site_id}:{torrent.hash}"
        profiles = self.gateway.policies
        profile = profiles.get(site_id)
        now = time.time()
        base = dict(downloader=torrent.downloader, hash=torrent.hash, site_id=site_id,
                    checked_at=now, tracker_result=tracker, site_result="unknown",
                    authenticated=False, binding_verified=False)
        tracker_note = {"active": "Tracker 汇报正常", "missing": "Tracker 明确返回不存在", "unknown": "Tracker 状态不确定"}[tracker]
        if only_suspected and tracker != 'missing':
            binding = (self.read('bindings') or {}).get(key) or {}
            note = '本轮无需访问站点' if tracker == 'active' else '未获得明确失效证据，本轮不访问站点'
            return Evidence(**base, reason=tracker_note + '，' + note, detail_url=binding.get('url', ''),
                            source=binding.get('source', ''), site_skipped=True)
        if not profile or profile.verification not in {"nexusphp", "mteam"}:
            return Evidence(**base, reason=tracker_note + "，本站尚未配置详情核验适配")
        if profile.verification == 'mteam':
            from .mteam import check_mteam
            return check_mteam(self, row, site_id, supplied_url, force, base, tracker_note)
        if not self.gateway.sites.get(site_id, {}).get("cookie"):
            return Evidence(**base, reason=tracker_note + "，MoviePilot 中未配置本站 Cookie")
        if supplied_url:
            supplied_url = self.gateway.canonical_detail(site_id, supplied_url)
        bindings = self.read("bindings") or {}
        binding = bindings.get(key) or {}
        caches = self.read("site_checks") or {}
        cache = caches.get(key) or {}
        fingerprint = self.fingerprint(site_id)
        if (not force and cache.get("fingerprint") == fingerprint
                and 0 <= now - cache.get("checked_at", 0) < self.cache_seconds(profile, cache)
                and (not supplied_url or supplied_url == cache.get("detail_url"))):
            return Evidence(**{**base, **{k: cache[k] for k in ["checked_at", "site_result", "authenticated", "binding_verified", "detail_url", "source"]}},
                            reason=tracker_note + "，" + cache["note"] + "（缓存，未重复访问站点）", cached=True)
        found_url, source, note = "", "", "未找到可以核实的详情页"
        result, auth, bound = "unknown", False, False
        can_cache = True
        metadata_checked = False
        try:
            known = supplied_url or binding.get("url")
            if known:
                found_url = self.gateway.canonical_detail(site_id, known)
                source = "手动链接" if supplied_url and supplied_url != binding.get("url") else binding.get("source", "已有关联")
                bound = bool(binding.get("verified_at") and binding.get("url") == found_url
                             and binding.get("hash") == torrent.hash and binding.get("site_id") == site_id)
                html = self.fetch(site_id, found_url)
                auth = bool(html and authenticated(html))
                result = parse_detail(html, torrent.hash, bound) if html else "unknown"
                if result == 'unknown' and auth and profile.allow_torrent_verification:
                    from .metadata import verify_metadata
                    if verify_metadata(self,site_id,found_url,torrent.hash):
                        result='active';metadata_checked=True;source='种子文件 Hash 核验'
                note = {"active": "详情页 Hash 一致，种子存在", "missing": "已绑定详情页明确提示删除", "unknown": "详情页登录、网络或 Hash 核验未通过"}[result]
            elif profile.auto_discover:
                candidates = [(u, "下载历史") for u in self.gateway.history_candidates(site_id, torrent.hash)]
                candidates += [(u, "下载器备注") for u in self.gateway.comment_candidates(site_id, row)]
                checked = set()
                for phase in range(2):
                    if phase == 1:
                        if not profile.search_url_template:
                            note = "下载历史和备注未提供有效详情，本站未配置搜索模板"
                            break
                        html = self.fetch(site_id, self.gateway.search_address(site_id, row), category='search')
                        if not html or not authenticated(html):
                            note = "站点搜索请求或登录验证失败"
                            break
                        candidates = [(u, "站点搜索") for u in self.gateway.search_candidates(site_id, row, html)]
                    cursors = self.read("discovery_cursors") or {}
                    cursor_key = f"{key}:{phase}"
                    signature = hashlib.sha256(json.dumps(candidates).encode()).hexdigest()
                    progress = cursors.get(cursor_key) or {}
                    start = progress.get("offset", 0) if progress.get("signature") == signature else 0
                    selected = (candidates[start:] + candidates[:start])[:profile.max_detail_candidates]
                    for url, origin in selected:
                        if url in checked:
                            continue
                        checked.add(url)
                        html = self.fetch(site_id, url)
                        cursors[cursor_key] = {"signature": signature,
                                               "offset": (candidates.index((url, origin)) + 1) % len(candidates)}
                        self.save("discovery_cursors", cursors)
                        if html and parse_detail(html, torrent.hash, False) == "active":
                            result, auth, found_url, source = "active", True, url, origin
                            note = f"已从{origin}自动关联，详情页 Hash 一致"
                            break
                        if html and authenticated(html) and profile.allow_torrent_verification:
                            from .metadata import verify_metadata
                            if verify_metadata(self,site_id,url,torrent.hash):
                                result,auth,found_url,source='active',True,url,'种子文件 Hash 核验'
                                metadata_checked=True;note='种子文件 Hash 一致，已验证身份'
                                break
                    if result == "active":
                        break
                    note = "本次候选页均未匹配 Hash，搜索无结果不能证明种子失效"
            else:
                note = "未关联详情，本站自动查找已关闭"
        except RequestDeferred as error:
            note, can_cache = str(error), False
        except Exception:
            note = "站点请求失败，保留任务，请核对连接与站点适配配置"
        now = time.time()
        if result == "active":
            bound = True
            bindings[key] = {"site_id": site_id, "hash": torrent.hash, "url": found_url,
                             "verified_at": now, "source": source}
            if metadata_checked:
                bindings[key].update(identity_hash=torrent.hash,hash_verified_at=now)
            self.save("bindings", bindings)
        evidence = Evidence(**{**base, "checked_at": now, "site_result": result,
                               "authenticated": auth, "binding_verified": bound},
                            reason=tracker_note + "，" + note, detail_url=found_url, source=source)
        if can_cache:
            caches[key] = {**asdict(evidence), "fingerprint": fingerprint, "note": note}
            self.save("site_checks", caches)
        return evidence
