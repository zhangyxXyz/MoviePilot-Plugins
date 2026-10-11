"""可重复执行的旧插件迁移，原始数据完整保留供回退和核查。"""
import hashlib
import re
import time
from datetime import datetime
from .models import Task
from .engine import media_key

LEGACY = {'PopularSubscribe': 'popular', 'DoubanRankPlus': 'douban', 'ActorSubscribePlus': 'actor', 'MediaRelease': 'release'}


def split(value):
    if isinstance(value, list):
        return value
    return [x.strip() for x in re.split('[,，\n]', str(value or '')) if x.strip()]


def migrate(snapshot):
    tasks, records, ledger, raw_processed = [], {}, {}, {}
    for pid, source in LEGACY.items():
        data = snapshot.get(pid, {})
        c, saved = data.get('config') or {}, data.get('data') or {}
        new = []
        if source == 'popular':
            for kind, label, threshold in [('movie', '电影', 5000), ('tv', '剧集', 8000), ('anime', '动漫', 8000)]:
                new.append(Task(id='popular-'+kind, name='高热度'+label, source=source,
                                enabled=c.get(kind+'_enabled', False), media_type=kind,
                                cron=c.get(kind+'_cron') or '5 1 * * *', min_popularity=c.get(kind+'_popular_cnt', threshold),
                                scan_limit=c.get(kind+'_page_cnt', 30), max_add=3, legacy_plugin=pid).model_dump())
        elif source == 'actor':
            for index, actor in enumerate(split(c.get('actors')) or ['成龙']):
                # 每位演员一张卡片。
                new.append(Task(id='actor-'+str(index), name=actor+'作品', source=source, actors=[actor],
                                enabled=c.get('enabled', False), cron=c.get('cron') or '35 8 * * *',
                                media_type='movie' if c.get('mtype') == ['电影'] else 'all',
                                min_year=int(c.get('year') or datetime.now().year), min_rating=float(c.get('vate') or 0),
                                release_mode='near_release', release_days=int(c.get('last') or 90), max_add=2,
                                scan_limit=100, legacy_plugin=pid).model_dump())
        elif source == 'douban':
            urls, unsupported = [], False
            for row in split(c.get('rss_addrs')):
                # 旧版支持每条 RSS 的路径和类型覆盖，复杂规则保留原始备份并暂停待核对。
                if ';' in row:
                    unsupported = True
                urls.append(row.split(';', 1)[0])
            new.append(Task(id='douban-quality', name='豆瓣高分电影', source=source,
                            enabled=c.get('enabled', False) and not unsupported,
                            ranks=c.get('ranks') or ['movie-weekly'], rss_urls=urls,
                            cron=c.get('cron') or '20 9 * * *', min_year=int(c.get('release_year') or 2024),
                            min_rating=float(c.get('vote') or 8), min_votes=1000 if not c else 0,
                            media_type='movie' if not c or c.get('is_only_movies') else 'all',
                            season_mode='all' if c.get('is_seasons_all') else 'first',
                            scan_limit=20, max_add=2, legacy_plugin=pid,
                            migration_note='旧 RSS 含独立目录或类型规则，请核对后启用' if unsupported else '').model_dump())
        else:
            for kind, field, label in [('movie', 'movies', '电影'), ('tv', 'tvs', '剧集')]:
                titles = split(c.get(field))
                if titles or kind == 'movie':
                    new.append(Task(id='release-'+kind, name='将映'+label+'关注', source=source,
                                    enabled=bool(titles) and c.get('enabled', False), titles=titles, media_type=kind,
                                    cron=c.get('cron') or '45 9 * * *', max_add=2, legacy_plugin=pid).model_dump())
        tasks.extend(new)
        history = saved.get('history') or []
        if not isinstance(history, list):
            history = []
        raw_processed[pid] = saved.get('already_handle') or saved.get('unique_flags') or []
        for index, old in enumerate(history):
            if not isinstance(old, dict):
                continue
            kind = 'movie' if old.get('type') in ('电影', 'movie') else 'tv'
            season = old.get('season')
            if isinstance(season, str):
                import cn2an
                found = re.search(r'第(.+?)季', season)
                try:
                    season = int(cn2an.cn2an(found[1], 'smart')) if found else int(season)
                except (ValueError, TypeError):
                    season = None
            mid = str(old.get('media_id') or old.get('tmdbid') or '')
            source_id = old.get('media_source') or ('themoviedb' if old.get('tmdbid') else '')
            matching = [t for t in new if t['media_type'] == kind or t['media_type'] == 'all']
            # 热门剧集和动漫旧记录没有足够信息区分，留在来源迁移历史。
            if source == 'popular' and kind == 'tv':
                matching = []
            task_id = matching[0]['id'] if len(matching) == 1 else 'legacy-'+source
            identity = hashlib.sha256(f'{pid}:{index}:{old.get("unique", "")}'.encode()).hexdigest()[:24]
            item = {'id': 'm-'+identity, 'task_id': task_id, 'task_name': next((t['name'] for t in new if t['id'] == task_id), '迁移历史'),
                    'source': source, 'legacy_plugin': pid, 'status': 'migrated', 'reason': '从旧插件迁入的订阅历史',
                    'title': old.get('title'), 'year': old.get('year'), 'media_type': kind,
                    'media_source': source_id, 'media_id': mid, 'season': season or (1 if kind == 'tv' else None),
                    'poster': old.get('poster') or old.get('poster_path'), 'detail_link': old.get('detail_link'),
                    'overview': old.get('overview'), 'legacy_time': old.get('time'), 'time': time.time(),
                    'legacy_vote': old.get('vote'), 'legacy_rating_source': '旧版媒体识别源，非确认豆瓣评分'}
            # 豆瓣历史里可能混有识别失败或条件不符，不能标成成功订阅。
            if source == 'douban' and (not mid or mid == '0' or old.get('status') not in (None, '已订阅', '订阅成功')):
                item.update(status='legacy', reason='旧版处理记录，保留原始状态供核对')
            records[item['id']] = item
            key = media_key(item)
            if key and mid != '0' and item['status'] == 'migrated':
                ledger[key] = {'task_ids': [task_id], 'status': 'migrated', 'time': time.time()}
    return {'tasks': tasks, 'records': records, 'ledger': ledger, 'legacy_processed': raw_processed,
            'report': {pid: {'history': len((value.get('data') or {}).get('history') or []),
                             'processed': len(raw_processed.get(pid) or [])} for pid, value in snapshot.items()}}
