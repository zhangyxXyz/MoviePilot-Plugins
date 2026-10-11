"""只读关联宿主订阅状态，保留原始扫描结果和去重记录。"""
from app.sdk.queries import list_subscriptions, list_subscription_history


def identity(row):
    source = getattr(row.get('media_source'), 'value', row.get('media_source'))
    kind = getattr(row.get('media_type') or row.get('type'), 'value', row.get('media_type') or row.get('type'))
    kind = {'电影': 'movie', '电视剧': 'tv'}.get(kind, kind)
    mid = row.get('media_id')
    if not source or not mid or kind not in ('movie', 'tv'):
        return None
    season = row.get('season')
    # 未知季度不猜测，以免将别的季度误判为完成。
    if kind == 'tv' and season is None:
        return None
    return str(source), kind, str(mid), str(season) if kind == 'tv' else '0', row.get('episode_group') or ''


def read_index(query):
    result = {}
    page = 1
    while True:
        batch = query(page={'page': page, 'count': 200})
        for dto in batch.items:
            row = dto.model_dump()
            key = identity(row)
            if key and key not in result:
                result[key] = row
        if not batch.has_next:
            return result
        page += 1


def snapshot():
    return read_index(list_subscriptions), read_index(list_subscription_history)


def project(records, active, completed):
    rows = []
    for record in records:
        row = {k: v for k, v in record.items() if k != 'candidate'}
        key = identity(row)
        current, done = active.get(key), completed.get(key)
        row['scan_status'] = row.get('status')
        if current:
            row.update(status='subscribed', subscription_id=current['id'], reason='已创建订阅，由 MoviePilot 继续处理')
        elif done:
            row.update(status='completed', completed_at=done.get('date'), reason='MoviePilot 已记录订阅完成')
        elif row.get('status') == 'library':
            row.update(status='completed', reason='扫描时已确认媒体库完整存在')
        elif row.get('status') in ('migrated', 'existing'):
            row.update(status='subscribed')
        rows.append(row)
    return rows
