"""下载器及站点读取适配，所有写操作均由执行器显式调用。"""

import math
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urljoin

from bs4 import BeautifulSoup

from app.sdk.config import settings
from app.sdk.network import RequestUtils, SitesHelper
from app.sdk.services import DownloaderHelper

from .models import SitePolicy
from .policy import Evidence, Torrent
from .verification import authenticated, detail_url, detail_links, search_url, parse_detail, tracker_result


@dataclass
class Snapshot:
    """归一任务及复核所需的内存信息，不保存下载器认证信息。"""

    torrent: Torrent
    path: str
    trackers: dict[int, list[dict]]
    local_paths: list[str]
    comment: str = ""
    client_state: str = ""
    progress: float | None = None
    added_at: float | None = None
    downloaded: float | None = None
    download_speed: float | None = None
    upload_speed: float | None = None
    category: str = ""
    managed: bool = False
    error_message: str = ''
    has_error: bool = False


def numeric(value):
    """无效或负值数字统一为未知，禁止以零冒充真实状态。"""
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def map_files(files: list[dict], save_path: str, mappings: list) -> tuple[frozenset[str], list[str], bool]:
    """根据下载器映射验证文件，并同时保留路径及 inode 引用身份。"""
    identities, paths = set(), []
    if not files or not mappings:
        return frozenset(), [], False
    try:
        for file in files:
            relative = PurePosixPath(str(file["name"]))
            if relative.is_absolute() or ".." in relative.parts:
                return frozenset(), [], False
            remote = PurePosixPath(save_path) / relative
            candidates = [(Path(local), PurePosixPath(client)) for local, client in mappings
                          if Path(local).is_absolute() and PurePosixPath(client).is_absolute()
                          and remote.is_relative_to(PurePosixPath(client))]
            if not candidates:
                return frozenset(), [], False
            local_root, remote_root = max(candidates, key=lambda pair: len(pair[1].parts))
            file_path = local_root / remote.relative_to(remote_root)
            resolved = file_path.resolve(strict=True)
            if not resolved.is_relative_to(local_root.resolve(strict=True)) or file_path.is_symlink():
                return frozenset(), [], False
            stat = file_path.stat()
            if not file_path.is_file() or stat.st_size != file["size"]:
                return frozenset(), [], False
            identities.update({"path:" + str(resolved), f"inode:{stat.st_dev}:{stat.st_ino}"})
            paths.append(str(resolved))
    except (OSError, ValueError, TypeError, KeyError):
        return frozenset(), [], False
    return frozenset(identities), paths, True


class Gateway:
    """使用宿主服务实例、HTTP 工具及站点 Cookie 提供窄操作接口。"""

    def __init__(self, site_policies: list[SitePolicy]):
        """建立本轮独立适配器，站点凭据只保存在内存中。"""
        self.helper = DownloaderHelper()
        self.policies = {p.site_id: p for p in site_policies}
        self.sites = {s["id"]: s for s in self.read_sites()}
        self.verifier = None
        self._history_rows = None

    def configure_verification(self, read, save, stopped):
        """让手动核验与任务运行共享站点缓存和限流状态。"""
        from .automatic import SiteVerifier
        self.verifier = SiteVerifier(self, read, save, stopped)

    def verification_priority(self, row):
        """先处理从未核验的任务，防止请求限额使后排任务长期饥饿。"""
        caches = self.verifier.read("site_checks") or {} if self.verifier else {}
        return min((caches.get(f"{sid}:{row.torrent.hash}", {}).get("checked_at", 0)
                    for sid in row.torrent.site_ids), default=0)

    def canonical_detail(self, site_id, url):
        """按站点配置校验并规范化详情地址。"""
        profile = self.policies[site_id]
        if profile.verification == 'mteam':
            from .mteam import canonical_url
            return canonical_url(self.sites[site_id]['url'], url)
        return detail_url(self.sites[site_id]["url"], url, profile.detail_path, profile.detail_id_param)

    def authentication(self, site_id):
        """直接复用宿主凭据，按适配器选择 Cookie 或 API 请求头，不另存副本。"""
        site, profile = self.sites[site_id], self.policies[site_id]
        headers = {'User-Agent': site.get('ua') or getattr(settings, 'USER_AGENT', 'MoviePilot'), 'Referer': site['url']}
        if profile.verification == 'mteam':
            if not site.get('apikey'):
                raise ValueError('MoviePilot 本站未配置 API Key，馒头第三方接口要求 x-api-key，不使用 Cookie 替代')
            headers.update({'x-api-key': site['apikey'], 'Accept': 'application/json'})
            return headers, None
        if not site.get('cookie'):
            raise ValueError('MoviePilot 本站未配置 Cookie')
        return headers, site['cookie']

    def api_base(self, site_id):
        """优先显式地址，其次复用宿主索引适配的 API 域名，最后根据站点域名推导。"""
        profile, site = self.policies[site_id], self.sites[site_id]
        if profile.api_url:
            return profile.api_url.rstrip('/') if profile.api_url.rstrip('/').endswith('/api') else profile.api_url.rstrip('/') + '/api'
        if not hasattr(self, '_api_bases'):
            self._api_bases = {}
        if site_id in self._api_bases:
            return self._api_bases[site_id]
        try:
            indexer = SitesHelper().get_indexer(site.get('domain') or site['url']) or {}
            configured = indexer.get('api_domain') or indexer.get('api_host')
            if configured:
                target = urlsplit(configured if '://' in configured else 'https://' + configured)
                if target.scheme == 'https' and target.hostname and not target.username and not target.password:
                    self._api_bases[site_id] = 'https://' + target.netloc + '/api'
                    return self._api_bases[site_id]
        except Exception:
            pass
        domain = site.get('domain') or urlsplit(site['url']).hostname
        if '://' in domain:
            domain = urlsplit(domain).hostname
        result = f'https://{domain if domain.startswith("api.") else "api." + domain}/api'
        self._api_bases[site_id] = result
        return result

    def history_candidates(self, site_id, torrent_hash):
        """分页读取有限下载历史，只采纳精确 Hash 记录中的同源详情链接。"""
        if self._history_rows is None:
            self._history_rows = []
            try:
                for page in range(1, 6):
                    response = RequestUtils(headers={"X-API-KEY": settings.API_TOKEN}, timeout=15).get_res(
                        f"http://127.0.0.1:{settings.PORT}{settings.API_V1_STR}/history/download",
                        params={"page": page, "count": 100}, allow_redirects=False)
                    if response is None or response.status_code != 200:
                        break
                    rows = response.json()
                    rows = rows.get("data") if isinstance(rows, dict) else rows
                    if not isinstance(rows, list):
                        break
                    self._history_rows.extend(rows)
                    if len(rows) < 100:
                        break
            except Exception:
                pass
        profile = self.policies[site_id]
        results = []
        for row in self._history_rows:
            if str(row.get("download_hash", "")).lower() != torrent_hash.lower():
                continue
            if profile.verification == 'mteam':
                from .mteam import candidates_from_text
                results += candidates_from_text(self.sites[site_id]['url'], json.dumps(row.get('note') or {}, ensure_ascii=False))
            else:
                results += detail_links(self.sites[site_id]["url"], json.dumps(row.get("note") or {}, ensure_ascii=False),
                                        profile.detail_path, profile.detail_id_param)
        return list(dict.fromkeys(results))[:10]

    def comment_candidates(self, site_id, row):
        """使用下载器备注中的详情线索，备注不匹配 Hash 时绝不建立关联。"""
        comment = row.comment
        if not comment:
            try:
                service = self.service(row.torrent.downloader)
                if service.type == "qbittorrent":
                    comment = service.instance.qbc.torrents_properties(torrent_hash=row.torrent.hash).get("comment", "")
            except Exception:
                comment = ""
        profile = self.policies[site_id]
        if profile.verification == 'mteam':
            from .mteam import candidates_from_text
            return candidates_from_text(self.sites[site_id]['url'], comment or '', numeric_comment=True)
        return detail_links(self.sites[site_id]["url"], comment or "", profile.detail_path, profile.detail_id_param)

    def search_address(self, site_id, row):
        """使用显式配置的 Hash 或标题搜索模板，不猜测站点参数。"""
        return search_url(self.sites[site_id]["url"], self.policies[site_id].search_url_template,
                          row.torrent.hash, row.torrent.name)

    def search_candidates(self, site_id, row, html):
        """优先核验体积接近的搜索结果，体积只是排序线索而非身份凭据。"""
        profile = self.policies[site_id]
        site_url = self.sites[site_id]["url"]
        candidates = detail_links(site_url, html, profile.detail_path, profile.detail_id_param)
        scores = {}
        for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
            try:
                url = self.canonical_detail(site_id, urljoin(site_url, anchor["href"]))
            except ValueError:
                continue
            parent = anchor.find_parent("tr")
            # 内层标题表格可能不含体积，最多向上检查三层行。
            for _ in range(3):
                if parent is None:
                    break
                sizes = re.findall(r"(\d+(?:\.\d+)?)\s*([KMGT]i?B)", parent.get_text(" ", strip=True), re.I)
                if sizes:
                    differences = [abs(float(value) * 1024 ** {"K": 1, "M": 2, "G": 3, "T": 4}[unit[0].upper()] - row.torrent.size)
                                   / max(1, row.torrent.size) for value, unit in sizes]
                    scores[url] = min(differences)
                    break
                parent = parent.find_parent("tr")
        return sorted(candidates, key=lambda url: scores.get(url, float("inf")))

    @staticmethod
    def read_sites() -> list[dict]:
        """通过宿主认证 API 分页读取站点，避免依赖内部数据库模型。"""
        result = []
        for page in range(1, 101):
            response = RequestUtils(headers={"X-API-KEY": settings.API_TOKEN}, timeout=15).get_res(
                f"http://127.0.0.1:{settings.PORT}{settings.API_V1_STR}/site/",
                params={"page": page, "count": 100}, allow_redirects=False)
            if response is None or response.status_code != 200:
                raise RuntimeError("无法通过宿主接口读取站点配置")
            payload = response.json()
            rows = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise RuntimeError("站点接口返回了未知格式")
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise RuntimeError("站点分页超限，无法确认完整站点范围")

    def options(self) -> dict:
        """只向界面返回站点身份和下载器名称，不返回 Cookie 或密码。"""
        return {"sites": [{"title": s["name"], "value": s["id"], "domain": urlsplit(s["url"]).hostname,
                           "api_url": self.api_base(s['id']) if self.policies.get(s['id']) and self.policies[s['id']].verification == 'mteam' else '',
                           "auth_types": [label for key, label in [("cookie", "Cookie"), ("apikey", "API Key"), ("token", "Token")] if s.get(key)]}
                           for s in self.sites.values()],
                "downloaders": [{"title": c.name, "value": c.name, "type": c.type}
                                for c in self.helper.get_configs().values()]}

    def service(self, name: str):
        """下载器不可用或类型未知时明确失败。"""
        service = self.helper.get_service(name)
        if not service or not service.instance or service.instance.is_inactive():
            raise RuntimeError(f"下载器 {name} 当前不可用")
        if service.type not in {"qbittorrent", "transmission"}:
            raise RuntimeError("仅支持 qBittorrent 和 Transmission")
        return service

    def resolve_site(self, host: str) -> int | None:
        """精确 Tracker 映射优先，其次使用已配置站点域名的子域匹配。"""
        explicit = [p.site_id for p in self.policies.values() if p.site_id in self.sites and host in p.tracker_hosts]
        if len(explicit) == 1:
            return explicit[0]
        matches = []
        for site in self.sites.values():
            domain = urlsplit(site["url"]).hostname or ""
            domain = domain.removeprefix("www.")
            if domain and (host == domain or host.endswith("." + domain)):
                matches.append(site["id"])
        return matches[0] if len(matches) == 1 else None

    def snapshot(self, name: str, manifests: bool = False, error_only: bool = False) -> list[Snapshot]:
        """获取累计做种时间和所有 Tracker，删文件预览额外读取完整文件清单。"""
        service = self.service(name)
        instance = service.instance
        if service.type == "qbittorrent":
            rows = instance.qbc.torrents_info()
        else:
            fields = ["hashString", "name", "status", "labels", "totalSize", "percentDone",
                      "leftUntilDone", "secondsSeeding", "uploadRatio", "downloadDir",
                      "error", "errorString", "trackerStats", "comment", "addedDate", "downloadedEver", "rateDownload", "rateUpload"]
            if manifests:
                fields.append("files")
            rows = instance.trc.get_torrents(arguments=fields)
        result = []
        for item in rows:
            qb = service.type == "qbittorrent"
            h = item.get("hash" if qb else "hashString")
            if not h:
                raise RuntimeError("下载器返回了缺少身份的任务")
            raw_trackers = ([] if error_only and qb and item.get('state') not in {'error', 'missingFiles', 'stalledDL', 'metaDL', 'forcedMetaDL'}
                            else instance.qbc.torrents_trackers(torrent_hash=h) if qb else item.get("trackerStats", []))
            trackers, complete = {}, True
            errors = [str(item.get('errorString') or '')] if not qb else []
            for tracker in raw_trackers:
                url = tracker.get("url" if qb else "announce", "")
                if qb and tracker.get("tier") == -1:
                    continue
                host = (urlsplit(url).hostname or "").lower()
                success = (tracker.get("status") == 2) if qb else tracker.get("lastAnnounceSucceeded")
                if not success:
                    errors.append(str(tracker.get('msg' if qb else 'lastAnnounceResult') or ''))
                site_id = self.resolve_site(host)
                if site_id is None:
                    complete = False
                    continue
                trackers.setdefault(site_id, []).append({"success": success,
                    "message": tracker.get("msg" if qb else "lastAnnounceResult", "")})
            seed = numeric(item.get("seeding_time" if qb else "secondsSeeding"))
            ratio = numeric(item.get("ratio" if qb else "uploadRatio"))
            size = numeric(item.get("size" if qb else "totalSize"))
            path = str(item.get("save_path" if qb else "downloadDir") or "")
            tags = item.get("tags" if qb else "labels") or []
            if isinstance(tags, str):
                tags = [v.strip() for v in tags.split(",") if v.strip()]
            progress = numeric(item.get("progress" if qb else "percentDone"))
            left = numeric(item.get("amount_left" if qb else "leftUntilDone"))
            healthy = (str(item.get("state", "")).lower() in {"uploading", "stalledup", "pausedup", "stoppedup", "forcedup", "queuedup"}
                       if qb else item.get("status") in {0, 5, 6} and item.get("error") != 3)
            hr = None
            profiles = [self.policies.get(s) for s in trackers]
            if profiles and all(p and p.hr_mode != "unknown" for p in profiles):
                hr = any(p.hr_mode == "minimum" and (seed is None or seed < p.min_seed_hours * 3600)
                         for p in profiles)
            identities, paths, verified = frozenset(), [], False
            if manifests:
                raw_files = instance.qbc.torrents_files(torrent_hash=h) if qb else item.get("files", [])
                files = [{"name": f.get("name"), "size": f.get("size" if qb else "length")} for f in raw_files]
                identities, paths, verified = map_files(files, path, service.config.path_mapping)
            torrent = Torrent(name, h, str(item.get("name") or h), frozenset(trackers), complete,
                              progress == 1 and left == 0, healthy, seed, ratio, int(size or 0),
                              frozenset(tags), hr, identities, verified)
            error_message = '，'.join(dict.fromkeys(v for v in errors if v))
            error_message = re.sub(r'https?://[^\s<>]+', '[Tracker 地址已隐藏]', error_message)
            error_message = re.sub(r'(?i)(passkey|token|api[-_]?key|authorization|cookie)\s*[:=]\s*[^\s,，]+', r'\1=[已隐藏]', error_message)[:2000]
            result.append(Snapshot(torrent, path, trackers, paths, str(item.get("comment") or ""),
                client_state=str(item.get('state') or '') if qb else str(item.get('status', '')),
                progress=progress, added_at=numeric(item.get('added_on') if qb else item.get('addedDate')),
                downloaded=numeric(item.get('downloaded') if qb else item.get('downloadedEver')),
                download_speed=numeric(item.get('dlspeed') if qb else item.get('rateDownload')),
                upload_speed=numeric(item.get('upspeed') if qb else item.get('rateUpload')),
                category=str(item.get('category') or ''), managed=bool(getattr(settings, 'TORRENT_TAG', '') and settings.TORRENT_TAG in tags),
                error_message=error_message, has_error=(str(item.get('state', '')).lower() in {'error', 'missingfiles'} if qb else item.get('error', 0) != 0)))
        return result

    def inventory(self) -> tuple[list[Snapshot], bool]:
        """读取所有已配置下载器的文件引用，任一不可读取即判定清单不完整。"""
        rows, complete = [], True
        for config in self.helper.get_configs(include_disabled=True).values():
            try:
                rows.extend(self.snapshot(config.name, manifests=True))
            except Exception:
                complete = False
        return rows, complete

    def fetch_detail(self, site_id: int, url: str) -> str | None:
        """仅携带目标站点 Cookie 请求同源页面，禁止跟随认证重定向。"""
        return self.fetch_site_page(site_id, self.canonical_detail(site_id, url))

    def fetch_site_page(self, site_id: int, url: str, method='GET', data=None, json_body=None) -> str | None:
        """仅允许在配置站点同源读取搜索或详情页，所有正式调用先经过限流器。"""
        site = self.sites.get(site_id)
        profile = self.policies.get(site_id)
        if not site or not profile or profile.verification not in {'nexusphp', 'mteam'}:
            return None
        headers, cookies = self.authentication(site_id)
        root, target = urlsplit(self.api_base(site_id) if profile.verification == 'mteam' else site['url']), urlsplit(url)
        if (root.scheme, root.hostname, root.port) != (target.scheme, target.hostname, target.port) or target.username or target.password:
            raise ValueError("站点请求不能跨域或携带 URL 认证信息")
        request = RequestUtils(cookies=cookies, headers=headers, timeout=15,
                                proxies=settings.PROXY if site.get("proxy") else None,
                                verify=True)
        if method == 'POST' and profile.verification == 'mteam' and target.path in {'/api/torrent/search', '/api/torrent/detail', '/api/torrent/genDlToken'}:
            response = request.post_res(url, data=data, json=json_body, allow_redirects=False)
        elif method == 'GET' and profile.verification == 'nexusphp':
            response = request.get_res(url, allow_redirects=False)
        else:
            raise ValueError('不支持的站点只读请求')
        if profile.verification == 'mteam' and response is not None and response.status_code != 200:
            raise ValueError(f'馒头 API 返回 HTTP {response.status_code}，未跟随重定向，请检查宿主站点凭据和网络')
        if response is None or response.status_code != 200 or len(response.content) > 3 * 1024**2:
            return None
        return response.text

    def verify(self, snapshot: Snapshot, site_id: int, binding: dict | None, force=False) -> Evidence:
        """同时复核 Tracker 与预先验证的详情页，不从搜索无结果推断删除。"""
        if getattr(self, "verifier", None):
            return self.verifier.check(snapshot, site_id, force=force, only_suspected=True)
        status = tracker_result(snapshot.trackers.get(site_id, []))
        result, auth, bound = "unknown", False, False
        if binding and binding.get("hash") == snapshot.torrent.hash and binding.get("site_id") == site_id:
            try:
                html = self.fetch_detail(site_id, binding["url"])
            except Exception:
                # 网络、认证或页面读取失败也需要生成未知证据并重置连续观察。
                html = None
            bound = bool(binding.get("verified_at"))
            if html:
                auth = authenticated(html)
                result = parse_detail(html, snapshot.torrent.hash, bound)
        reason = {"active": "Tracker 汇报正常", "missing": "Tracker 明确返回种子不存在",
                  "unknown": "Tracker 状态不确定"}[status]
        reason += {"active": "，详情页 Hash 仍存在", "missing": "，已验证详情页明确提示删除",
                   "unknown": "，缺少已验证详情或站点复核不确定"}[result]
        return Evidence(snapshot.torrent.downloader, snapshot.torrent.hash, site_id,
                        time.time(), status, result, auth, bound, reason)

    def apply(self, torrent: Torrent, action: str) -> bool:
        """发出单项操作，调用方必须先持久化执行意图并重新检查。"""
        service = self.service(torrent.downloader)
        instance = service.instance
        if action == "mark":
            tags = sorted(set(torrent.tags) | {"清理候选"})
            if service.type == "qbittorrent":
                instance.qbc.torrents_add_tags(tags="清理候选", torrent_hashes=torrent.hash)
            else:
                instance.trc.change_torrent(ids=[torrent.hash], labels=tags)
            return True
        if action == "pause":
            return instance.stop_torrents(ids=[torrent.hash]) is True
        if action in {"delete", "deletefile"}:
            return instance.delete_torrents(delete_file=action == "deletefile", ids=[torrent.hash]) is True
        raise ValueError("不支持的动作")

    def confirm(self, torrent: Torrent, action: str, paths: list[str]) -> bool:
        """从下载器重新读取执行结果，删除文件还必须核实原路径消失。"""
        service = self.service(torrent.downloader)
        instance = service.instance
        if service.type == "qbittorrent":
            rows = instance.qbc.torrents_info(torrent_hashes=torrent.hash)
        else:
            rows = instance.trc.get_torrents(ids=[torrent.hash], arguments=["hashString", "status", "labels"])
        if action in {"delete", "deletefile"}:
            return not rows and (action == "delete" or bool(paths) and not any(os.path.exists(p) for p in paths))
        if not rows:
            return False
        item = rows[0]
        if action == "mark":
            tags = item.get("tags" if service.type == "qbittorrent" else "labels") or []
            return "清理候选" in ([v.strip() for v in tags.split(",")] if isinstance(tags, str) else tags)
        return (str(item.get("state", "")).lower().startswith(("paused", "stopped"))
                if service.type == "qbittorrent" else item.get("status") == 0)
