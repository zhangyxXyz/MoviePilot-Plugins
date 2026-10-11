"""独立读取下载器与站点身份，只允许标签写入。"""
from urllib.parse import urlsplit
from app.sdk.config import settings
from app.sdk.network import RequestUtils
from app.sdk.services import DownloaderHelper
from .policy import finite, tags

class Gateway:
    def __init__(self, rules):
        self.helper = DownloaderHelper()
        self.policies = {p.site_id: p for p in rules}
        self.sites = {s['id']: s for s in self.read_sites()}
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

    def hr_tags(self, downloader, torrent_hash, add=frozenset(), remove=frozenset()):
        """读取最新标签后合并，读回确认，保留下载器的其他所有标签。"""
        service = self.service(downloader)
        client = service.instance.qbc if service.type == 'qbittorrent' else service.instance.trc
        def read():
            rows = (client.torrents_info(torrent_hashes=torrent_hash) if service.type == 'qbittorrent'
                    else client.get_torrents(ids=[torrent_hash], arguments=['hashString', 'labels']))
            if len(rows) != 1:
                raise RuntimeError('种子尚未出现或已被移除')
            tags = rows[0].get('tags' if service.type == 'qbittorrent' else 'labels') or []
            return {v.strip() for v in tags.split(',') if v.strip()} if isinstance(tags, str) else set(tags)
        current = read()
        if service.type == 'qbittorrent':
            if add:
                client.torrents_add_tags(torrent_hashes=torrent_hash, tags=','.join(sorted(add)))
            if remove:
                client.torrents_remove_tags(torrent_hashes=torrent_hash, tags=','.join(sorted(remove)))
        else:
            client.change_torrent(ids=[torrent_hash], labels=sorted((current | set(add)) - set(remove)))
        actual = read()
        if not set(add).issubset(actual) or set(remove) & actual:
            raise RuntimeError('标签更新未确认')

    def snapshot(self, name):
        service = self.service(name)
        qb = service.type == 'qbittorrent'
        client = service.instance.qbc if qb else service.instance.trc
        rows = client.torrents_info() if qb else client.get_torrents(arguments=[
            'hashString','name','labels','percentDone','leftUntilDone','secondsSeeding',
            'uploadRatio','uploadedEver','totalSize','addedDate','trackerStats'])
        result = []
        for item in rows:
            h = str(item.get('hash' if qb else 'hashString') or '').lower()
            if not h: raise RuntimeError('下载器返回缺少 Hash 的任务')
            trackers = client.torrents_trackers(torrent_hash=h) if qb else item.get('trackerStats', [])
            ids, complete = set(), bool(trackers)
            for tracker in trackers:
                if qb and tracker.get('tier') == -1: continue
                sid = self.resolve_site((urlsplit(tracker.get('url' if qb else 'announce', '')).hostname or '').lower())
                if sid is None: complete = False
                else: ids.add(sid)
            result.append(dict(hash=h, name=str(item.get('name') or h), site_ids=sorted(ids),
                sites_complete=complete and bool(ids), tags=tags(item.get('tags' if qb else 'labels')),
                completed=finite(item.get('progress' if qb else 'percentDone')) == 1 and finite(item.get('amount_left' if qb else 'leftUntilDone')) == 0,
                seed_seconds=finite(item.get('seeding_time' if qb else 'secondsSeeding')),
                ratio=finite(item.get('ratio' if qb else 'uploadRatio')),
                uploaded=finite(item.get('uploaded' if qb else 'uploadedEver')),
                total_size=finite(item.get('total_size' if qb else 'totalSize')),
                added_at=finite(item.get('added_on' if qb else 'addedDate'))))
        return result
