"""通过宿主 Chain 查询媒体和创建订阅，不依赖旧插件。"""
import re
import hashlib
import time
from xml.etree import ElementTree
from app.chain.media import MediaChain
from app.chain.download import DownloadChain
from app.chain.subscribe import SubscribeChain
from app.chain.tmdb import TmdbChain
from app.sdk.config import settings
from app.sdk.media import MediaInfo, MetaInfo, resolve_media_identity
from app.sdk.network import RequestUtils
from app.schemas.types import MediaSource, MediaType

RANK_PATHS = {'movie-ustop': 'movie/ustop', 'movie-weekly': 'movie/weekly',
              'movie-real-time': 'movie/weekly/movie_real_time_hotest', 'show-domestic': 'movie/weekly/show_domestic',
              'movie-hot-gaia': 'movie/weekly/movie_hot_gaia', 'tv-hot': 'movie/weekly/tv_hot',
              'movie-top250': 'movie/weekly/movie_top250', 'movie-top250-full': 'list/movie_top250'}


def number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_rss(xml):
    if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('不支持含外部实体的 RSS')
    root = ElementTree.fromstring(xml)
    result = []
    for node in root.findall('.//item'):
        link, title = node.findtext('link', ''), node.findtext('title', '')
        match = re.search(r'douban\.com/subject/(\d+)', link)
        if not match:
            continue
        # 只接受明确标注评分的内容，不将任意数字误认为豆瓣评分。
        desc = re.sub('<[^>]+>', ' ', node.findtext('description', ''))
        score = re.search(r'(?:豆瓣评分|评分)\s*[:：]?\s*(\d+(?:\.\d+)?)', desc)
        year = re.search(r'\b((?:19|20)\d{2})\b', node.findtext('year', '') or desc)
        result.append({'title': title, 'douban_id': match[1], 'year': year[1] if year else None,
                       'douban_rating': number(score[1]) if score else None})
    return result


def parse_wish(xml, days, now=None):
    """只接受近期的明确想看动态，看过、在看及其他动态不导入。"""
    from datetime import datetime, timezone
    from email.utils import parsedate_to_datetime
    now = now or datetime.now(timezone.utc)
    if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('不支持含外部实体的 RSS')
    root = ElementTree.fromstring(xml)
    items = []
    for node in root.findall('.//item'):
        title = node.findtext('title', '').strip()
        if not title.startswith('想看'):
            continue
        match = re.search(r'douban\.com/subject/(\d+)', node.findtext('link', ''))
        if not match:
            continue
        pubdate = node.findtext('pubDate', '')
        if pubdate:
            try:
                published = parsedate_to_datetime(pubdate)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if (now - published).total_seconds() > days * 86400:
                    continue
            except (ValueError, TypeError):
                pass
        items.append({'title': title[2:].strip(), 'douban_id': match[1], 'wish_date': pubdate})
    return items


class Gateway:
    def __init__(self, stopped=lambda: False, load=lambda key: None, save=lambda key, value: None):
        self.media, self.download, self.subs, self.tmdb = MediaChain(), DownloadChain(), SubscribeChain(), TmdbChain()
        self.stopped = stopped
        self.load, self.save = load, save
        self._douban_at = 0
        self._douban_blocked = False

    def normalize(self, media, **extra):
        source, mid = resolve_media_identity(media=media)
        kind = 'movie' if media.type == MediaType.MOVIE else 'tv'
        genres = getattr(media, 'genre_ids', None)
        anime_ids = {int(x) for x in re.findall(r'\d+', str(settings.ANIME_GENREIDS))}
        item = {'media_source': source.value if source else '', 'media_id': str(mid or ''),
                'media_type': kind, 'title': media.title, 'year': str(media.year or ''),
                'season': getattr(media, 'season', None) or (1 if kind == 'tv' else None),
                'poster': media.get_poster_image(), 'overview': media.overview,
                'tmdb_rating': number(media.vote_average), 'tmdb_votes': number(getattr(media, 'vote_count', None)),
                'anime': bool({int(x) for x in genres if str(x).isdigit()} & anime_ids) if genres else None,
                'release_date': getattr(media, 'release_date', None) or getattr(media, 'first_air_date', None),
                'detail_link': media.detail_link, **extra}
        return item

    def detail(self, item):
        kind = MediaType.MOVIE if item['media_type'] == 'movie' else MediaType.TV
        meta = MetaInfo(item.get('title') or '')
        meta.type, meta.begin_season = kind, item.get('season')
        media = self.media.recognize_media(meta=meta, mtype=kind,
                                          media_source=MediaSource(item['media_source']), media_id=str(item['media_id']))
        if not media:
            raise ValueError('媒体识别失败')
        return media, meta

    def expand(self, task, item):
        if item.get('error') or item.get('media_type') != 'tv' or task['season_mode'] != 'all' or item.get('explicit_season'):
            return [item]
        info = self.media.tmdb_info(tmdbid=int(item['media_id']), mtype=MediaType.TV)
        seasons = (info or {}).get('seasons') or []
        if not seasons:
            return [{**item, 'error': '暂未获取到季度信息'}]
        return [{**item, 'season': s['season_number'], 'release_date': s.get('air_date')}
                for s in seasons if s.get('season_number', 0) > 0]

    def candidates(self, task, pending=None):
        # 单轮数据源失败不会清空已有待处理项。
        unique, count = set(), 0
        source = getattr(self, task['source'])
        for item in source(task):
            if self.stopped():
                return
            for expanded in self.expand(task, item):
                key = (expanded.get('media_source'), expanded.get('media_id'), expanded.get('media_type'),
                       expanded.get('season'), expanded.get('douban_id'), expanded.get('title'))
                if key in unique:
                    continue
                unique.add(key)
                yield expanded
            count += 1
            if count >= task['scan_limit']:
                break
        for old in (pending or [])[:task['scan_limit']]:
            if self.stopped():
                return
            key = (old.get('media_source'), old.get('media_id'), old.get('media_type'), old.get('season'), old.get('douban_id'), old.get('title'))
            if key in unique:
                continue
            try:
                if old.get('douban_id'):
                    yield self.resolve_douban(old)
                elif old.get('media_id'):
                    media, _ = self.detail(old)
                    yield {**old, **self.normalize(media), 'season': old.get('season'), 'error': None}
                else:
                    yield from self.release({**task, 'titles': [old['title']]})
            except Exception:
                yield {**old, 'error': '数据源暂不可用，下轮重试'}

    def popular(self, task):
        if not settings.SUBSCRIBE_STATISTIC_SHARE:
            raise ValueError('请在 MoviePilot 设置中开启订阅数据共享')
        types = ['电影', '电视剧'] if task['media_type'] == 'all' else ['电影' if task['media_type'] == 'movie' else '电视剧']
        for kind in types:
            response = RequestUtils(proxies=settings.PROXY, timeout=25, ua=settings.USER_AGENT).get_res(
                str(settings.MP_SERVER_HOST).rstrip('/') + '/subscribe/statistic',
                params={'stype': kind, 'page': 1, 'count': min((task['scan_limit'] + 1) // 2 if len(types) == 2 else task['scan_limit'] * (5 if task['media_type'] == 'anime' else 1), 500)})
            if response is None or response.status_code != 200:
                raise ValueError('热门订阅统计暂不可用')
            data = response.json()
            data = data.get('data', []) if isinstance(data, dict) else data
            for sub in data:
                source, mid = resolve_media_identity(media_source=sub.get('media_source'), media_id=sub.get('media_id'))
                item = {'media_source': source.value if source else '', 'media_id': str(mid or ''),
                        'media_type': 'movie' if kind == '电影' else 'tv', 'title': sub.get('name'),
                        'season': int(sub.get('season') or 1) if kind != '电影' else None,
                        'popularity': int(sub.get('count') or 0), 'explicit_season': bool(sub.get('season'))}
                if not mid:
                    yield {**item, 'error': '统计数据缺少媒体身份'}
                    continue
                try:
                    media, _ = self.detail(item)
                    resolved = {**self.normalize(media), **item}
                    if task['media_type'] in ('tv', 'anime') and resolved.get('anime') is not None and resolved['anime'] != (task['media_type'] == 'anime'):
                        continue
                    yield resolved
                except Exception:
                    yield {**item, 'error': '媒体详情暂不可用'}

    def actor(self, task):
        for actor in task['actors']:
            if actor.isdigit():
                person_id = int(actor)
            else:
                people = self.media.search_persons(name=actor, media_source=MediaSource.TMDB) or []
                exact = [p for p in people if getattr(p, 'name', '') == actor or actor in (getattr(p, 'also_known_as', None) or [])]
                people = exact or people
                if len(people) != 1:
                    yield {'title': actor, 'error': '演员姓名未唯一匹配，请填写 TMDB 人物 ID'}
                    continue
                person_id = people[0].id
            credits = []
            for page in range(1, 11):
                if self.stopped():
                    return
                items = self.tmdb.person_credits(person_id=int(person_id), page=page) or []
                if not items:
                    break
                credits.extend(items)
            credits.sort(key=lambda m: str(m.year or ''), reverse=True)
            for media in credits:
                yield self.normalize(media, actor=actor)

    def release(self, task):
        for title in task['titles']:
            meta = MetaInfo(title)
            if task['media_type'] != 'all':
                meta.type = MediaType.MOVIE if task['media_type'] == 'movie' else MediaType.TV
            found = self.media.search_medias(meta=meta, media_source=MediaSource.TMDB) or []
            # 防止像旧插件一样将同名搜索结果全部订阅。
            exact = [m for m in found if (not meta.year or str(m.year) == str(meta.year)) and
                     m.title in (title, meta.name, meta.cn_name, meta.en_name)]
            found = exact or found
            if len(found) != 1:
                yield {'title': title, 'error': '未唯一识别，请补充年份或等待媒体上线'}
                continue
            item = self.normalize(found[0])
            if meta.begin_season:
                item.update(season=meta.begin_season, explicit_season=True)
            yield item

    def douban(self, task):
        urls = list(dict.fromkeys(task['rss_urls'] + ['https://rsshub.rssforever.com/douban/' + RANK_PATHS[r] for r in task['ranks'] if r in RANK_PATHS]))
        for url in urls:
            cache_key = 'rss_' + hashlib.sha256(url.encode()).hexdigest()[:16]
            cached = self.load(cache_key) or {}
            xml = cached.get('xml') if time.time() - cached.get('time', 0) < 3600 else None
            if not xml:
                response = RequestUtils(proxies=settings.PROXY, timeout=30, ua=settings.USER_AGENT).get_res(url)
                if response is None or response.status_code != 200:
                    if cached and time.time() - cached.get('time', 0) < 86400:
                        xml = cached['xml']
                    else:
                        raise ValueError('豆瓣 RSS 暂不可用，稍后重试')
                else:
                    xml = response.text
                    parse_rss(xml)
                    self.save(cache_key, {'time': time.time(), 'xml': xml})
            for item in parse_rss(xml):
                try:
                    yield self.resolve_douban(item)
                except Exception as err:
                    yield {**item, 'error': f'该条媒体详情暂不可用（{type(err).__name__}），下轮重试'}

    def wish(self, task):
        user = task.get('douban_user', '').strip()
        if not user:
            raise ValueError('请填写豆瓣账号 ID')
        url = 'https://www.douban.com/feed/people/' + user + '/interests'
        cache_key = 'wish_' + hashlib.sha256(user.encode()).hexdigest()[:16]
        cached = self.load(cache_key) or {}
        xml = cached.get('xml') if time.time() - cached.get('time', 0) < 300 else None
        if not xml:
            response = RequestUtils(timeout=30, ua=settings.USER_AGENT).get_res(url)
            if response is None or response.status_code != 200:
                raise ValueError('豆瓣账号动态暂不可用，请确认账号和公开权限，稍后重试')
            xml = response.text
            parse_wish(xml, task['wish_days'])
            self.save(cache_key, {'time': time.time(), 'xml': xml})
        for item in parse_wish(xml, task['wish_days']):
            try:
                yield {**self.resolve_douban(item), 'wish_user': user, 'wish_date': item.get('wish_date')}
            except Exception as err:
                yield {**item, 'wish_user': user, 'error': f'想看作品暂未识别（{type(err).__name__}），下轮重试'}

    def resolve_douban(self, item):
        if self._douban_blocked:
            return {**item, 'error': '豆瓣限制访问，下轮重试'}
        # 限速且可中断，豆瓣详情获取失败不回退成 TMDB 评分。
        while time.monotonic() - self._douban_at < 3:
            if self.stopped():
                return {**item, 'error': '任务已停止'}
            time.sleep(0.2)
        self._douban_at = time.monotonic()
        try:
            info = self.media.douban_info(doubanid=item['douban_id'], raise_exception=True)
        except Exception:
            self._douban_blocked = True
            return {**item, 'error': '豆瓣详情暂不可用，下轮重试'}
        if not info or info.get('code') or 'rate_limit' in str(info.get('msg', '')):
            self._douban_blocked = True
            return {**item, 'error': '豆瓣详情暂不可用，下轮重试'}
        rating = info.get('rating') or {}
        if isinstance(rating, dict):
            score, votes = number(rating.get('value') or rating.get('average')), number(rating.get('count') or rating.get('numRaters'))
        else:
            score, votes = number(rating), None
        score = score or number(info.get('vote_average')) or item.get('douban_rating')
        kind = info.get('media_type')
        if not isinstance(kind, MediaType):
            kind = MediaType.MOVIE if info.get('type') == 'movie' else MediaType.TV
        title = info.get('title') or item['title']
        meta = MetaInfo(title)
        season = meta.begin_season
        matched = None
        for name in dict.fromkeys([meta.cn_name, meta.en_name, title, info.get('original_title')]):
            if name:
                matched = self.media.match_tmdbinfo(name=name, mtype=kind, year=str(info.get('year') or ''), season=season)
                if matched:
                    break
        if not matched:
            return {**item, 'douban_rating': score, 'error': '豆瓣条目尚未匹配到 TMDB'}
        base = {'media_source': MediaSource.TMDB.value, 'media_id': str(matched['id']),
                'media_type': 'movie' if kind == MediaType.MOVIE else 'tv', 'title': title, 'season': season}
        media, _ = self.detail(base)
        return {**self.normalize(media), 'douban_id': item['douban_id'], 'douban_rating': score,
                'douban_votes': votes, 'season': season or (1 if kind == MediaType.TV else None),
                'explicit_season': bool(season)}

    def exists(self, item):
        media, meta = self.detail(item)
        exists, _ = self.download.get_no_exists_info(meta=meta, mediainfo=media)
        if exists:
            return 'library'
        if self.subs.exists(mediainfo=media, meta=meta):
            return 'existing'
        return None

    def subscribe(self, task, item):
        sid, _ = self.subs.add(title=item['title'], year=item.get('year') or '',
                              mtype=MediaType.MOVIE if item['media_type'] == 'movie' else MediaType.TV,
                              media_source=MediaSource(item['media_source']), media_id=item['media_id'],
                              season=item.get('season') if item['media_type'] == 'tv' else None,
                              exist_ok=True, username='自动订阅', **({'save_path': task['save_path']} if task['save_path'] else {}))
        return sid
