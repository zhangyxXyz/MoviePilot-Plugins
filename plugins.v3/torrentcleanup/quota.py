"""站点共享的滚动配额，按请求类别持久化预占，失败也计数。"""
import time

TIMING_FIELDS = {'check_interval_hours', 'request_gap_seconds', 'max_requests_per_hour',
                'max_detail_candidates', 'detail_requests_per_hour', 'search_requests_per_day',
                'torrent_downloads_per_hour', 'torrent_downloads_per_day', 'error_cache_minutes'}


def windows(profile, category):
    """返回请求类别的计数窗口与配置值，零额度表示禁止该类请求。"""
    return {'detail': [(3600, profile.detail_requests_per_hour, '详情每小时')],
            'search': [(86400, profile.search_requests_per_day, '搜索滚动24小时')],
            'download': [(3600, profile.torrent_downloads_per_hour, '种子文件下载每小时'),
                         (86400, profile.torrent_downloads_per_day, '种子文件下载滚动24小时')]}.get(category, [])


def inspect_quota(profile, events, category, now):
    """不修改存储，返回额度状态及最早释放时间。"""
    result = []
    for seconds, limit, title in windows(profile, category):
        times = sorted(v for v in events if 0 <= now-v < seconds)
        blocked = len(times) >= limit or any(v > now for v in events)
        next_at = (times[len(times)-limit]+seconds if limit and len(times)>=limit else None)
        result.append({'title': title, 'used':len(times), 'limit':limit, 'blocked':blocked,
                       'next_at':next_at, 'window_seconds':seconds})
    return result


def summaries(policies, rates):
    """为配置页生成不含凭据的用量摘要。"""
    now=time.time()
    return {p.site_id: [v for kind in ['detail','search','download']
                        for v in inspect_quota(p,rates.get(f'{p.site_id}:{kind}',[]),kind,now)] for p in policies}
