"""自动订阅的任务合同和稳定排序。"""
import copy
import uuid
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from apscheduler.triggers.cron import CronTrigger

SOURCES = {'popular': '热门媒体', 'douban': '豆瓣榜单', 'actor': '演员作品', 'release': '将映关注', 'wish': '豆瓣想看'}
RANKS = {'movie-weekly': '电影口碑榜', 'movie-ustop': '北美票房榜', 'movie-real-time': '实时热门电影',
         'show-domestic': '国产剧榜', 'movie-hot-gaia': '热门电影', 'tv-hot': '热门剧集',
         'movie-top250': '电影 Top250', 'movie-top250-full': '完整电影 Top250'}

class Task(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    name: str = Field(min_length=1, max_length=100)
    source: Literal['popular', 'douban', 'actor', 'release', 'wish']
    enabled: bool = False
    cron: str = '0 9 * * *'
    media_type: Literal['all', 'movie', 'tv', 'anime'] = 'all'
    scan_limit: int = Field(default=30, ge=1, le=300)
    max_add: int = Field(default=3, ge=1, le=30)
    min_year: int = Field(default=0, ge=0, le=2100)
    min_rating: float = Field(default=0, ge=0, le=10)
    min_votes: int = Field(default=0, ge=0)
    min_popularity: int = Field(default=0, ge=0)
    ranks: list[str] = Field(default_factory=list)
    rss_urls: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    douban_user: str = Field(default='', pattern=r'^[a-zA-Z0-9_.-]*$')
    wish_days: int = Field(default=7, ge=1, le=365)
    titles: list[str] = Field(default_factory=list)
    release_mode: Literal['recognized', 'near_release'] = 'recognized'
    release_days: int = Field(default=30, ge=0, le=365)
    season_mode: Literal['first', 'all'] = 'first'
    save_path: str = ''
    legacy_plugin: str = ''
    migration_note: str = ''

    @field_validator('cron')
    @classmethod
    def valid_cron(cls, value):
        CronTrigger.from_crontab(value)
        return value

    @field_validator('rss_urls')
    @classmethod
    def valid_urls(cls, values):
        from urllib.parse import urlparse
        for value in values:
            p = urlparse(value)
            if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
                raise ValueError('RSS 地址须为不含账号密码的 HTTP 或 HTTPS 地址')
        return list(dict.fromkeys(values))

class Settings(BaseModel):
    enabled: bool = False
    show_sidebar: bool = True

class TaskOrder(BaseModel):
    ids: list[str]

class HistoryAction(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=300)


def save_task(tasks, task):
    """编辑原位替换，新任务追加末尾，历史按固定 ID 关联。"""
    if any(t['id'] == task['id'] for t in tasks):
        return [copy.deepcopy(task if t['id'] == task['id'] else t) for t in tasks]
    if len(tasks) >= 100:
        raise ValueError('最多支持 100 个任务')
    return copy.deepcopy([*tasks, task])


def reorder(tasks, ids):
    current = {t['id']: t for t in tasks}
    if len(ids) != len(current) or len(set(ids)) != len(ids) or set(ids) != set(current):
        raise ValueError('任务列表已变化，请刷新后重新排序')
    return [copy.deepcopy(current[i]) for i in ids]
