"""统一去重、筛选和执行。预览不会写入订阅或去重账本。"""
import hashlib
import time
from datetime import date, timedelta


def media_key(item):
    if not item.get('media_id') or not item.get('media_source'):
        return ''
    # TMDB 的电影和剧集 ID 空间不同，电视剧按季去重。
    return f"{item['media_source']}:{item['media_type']}:{item['media_id']}:s{item.get('season') or (1 if item['media_type'] != 'movie' else 0)}"


def record_id(task_id, item):
    identity = media_key(item) or f"{item.get('douban_id')}:{item.get('title')}:{item.get('year')}"
    return hashlib.sha256(f'{task_id}:{identity}'.encode()).hexdigest()[:24]


def filter_reason(task, item):
    if item.get('error'):
        return 'retry', item['error']
    if not media_key(item):
        return 'retry', '尚未识别到唯一媒体，稍后重试'
    kind = task['media_type']
    if kind != 'all':
        if kind == 'movie' and item['media_type'] != 'movie':
            return 'filtered', '不属于电影'
        if kind in ('tv', 'anime'):
            if item['media_type'] != 'tv':
                return 'filtered', '不属于剧集'
            if item.get('anime') is None:
                return 'retry', '暂未获取到剧集分类'
            if item['anime'] != (kind == 'anime'):
                return 'filtered', '不符合电视剧或动漫分类'
    if task['min_year'] and not item.get('year'):
        return 'retry', '暂无年份，稍后重试'
    if task['min_year'] and int(item['year']) < task['min_year']:
        return 'filtered', '早于最低年份'
    if task['source'] == 'popular' and int(item.get('popularity') or 0) < task['min_popularity']:
        return 'filtered', '订阅人次尚未达到门槛'
    # 豆瓣任务绝不回退到 TMDB 评分。
    field = 'douban_rating' if task['source'] in ('douban', 'wish') else 'tmdb_rating'
    votes = 'douban_votes' if task['source'] in ('douban', 'wish') else 'tmdb_votes'
    if task['min_rating']:
        if not item.get(field):
            return 'retry', '暂无豆瓣评分' if field == 'douban_rating' else '暂无 TMDB 评分'
        if float(item[field]) < task['min_rating']:
            return 'filtered', '评分尚未达到门槛'
    if task['min_votes']:
        if item.get(votes) is None:
            return 'retry', '暂无评分人数'
        if item[votes] < task['min_votes']:
            return 'filtered', '评分人数尚未达到门槛'
    if task['release_mode'] == 'near_release':
        try:
            released = date.fromisoformat(item.get('release_date', '')[:10])
        except (ValueError, TypeError):
            return 'retry', '暂无上映日期'
        if released > date.today() + timedelta(days=task['release_days']):
            return 'waiting', '尚未到关注的上映时间范围'
    return 'eligible', '符合条件'


class Engine:
    def __init__(self, gateway, load, save, stopped=lambda: False):
        self.gateway, self.load, self.save, self.stopped = gateway, load, save, stopped

    def run(self, task, preview=False):
        ledger = self.load('ledger') or {}
        records = self.load('records') or {}
        output, added, selected = [], 0, 0
        # 限额延后的条目与失败条目持久化，下轮即使移出榜单也能重试。
        pending = [r['candidate'] for r in records.values() if r.get('task_id') == task['id']
                   and r.get('status') in ('retry', 'waiting', 'limited') and r.get('candidate')]
        candidates = self.gateway.candidates(task, pending)
        seen = set()
        for item in candidates:
            if self.stopped():
                break
            key = media_key(item)
            rid = record_id(task['id'], item)
            if rid in seen:
                continue
            seen.add(rid)
            status, reason = filter_reason(task, item)
            if status == 'eligible':
                if key in ledger:
                    status, reason = 'duplicate', '已由自动订阅处理'
                else:
                    try:
                        existing = self.gateway.exists(item)
                        if existing:
                            status, reason = existing, '媒体库已存在' if existing == 'library' else '订阅已存在'
                        elif (selected if preview else added) >= task['max_add']:
                            status, reason = 'limited', '达到本次新增上限，下轮继续'
                        elif preview:
                            selected += 1
                        else:
                            sid = self.gateway.subscribe(task, item)
                            if not sid:
                                raise RuntimeError('未返回订阅 ID')
                            item['subscription_id'] = sid
                            status, reason = 'subscribed', '已创建订阅'
                            added += 1
                    except Exception as err:
                        status, reason = 'retry', f'识别或订阅检查失败（{type(err).__name__}），下轮重试'
            row = {**item, 'id': rid, 'task_id': task['id'], 'task_name': task['name'],
                   'source': task['source'], 'status': status, 'reason': reason, 'time': time.time()}
            row.pop('_media', None)
            output.append(row)
            if preview:
                continue
            if status in ('subscribed', 'existing', 'library'):
                ledger.setdefault(key, {'task_ids': [], 'status': status, 'time': time.time()})
            if key in ledger:
                if task['id'] not in ledger[key]['task_ids']:
                    ledger[key]['task_ids'].append(task['id'])
                self.save('ledger', ledger)
            previous = records.get(rid, {})
            if previous.get('status') in ('subscribed', 'migrated') and status == 'duplicate':
                # 再扫描不把真实订阅历史覆盖成跳过记录。
                continue
            row['candidate'] = {k: v for k, v in item.items() if k != '_media'}
            records[rid] = row
            self.save('records', records)
        return {'items': output, 'added': added, 'scanned': len(output), 'preview': preview, 'would_add': selected,
                'time': time.time(), 'task_id': task['id']}
