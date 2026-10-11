"""H&R 标签策略：本地估算达标，仅解除自有保护标签。"""
import math
import re
from typing import Literal
from pydantic import BaseModel, Field, model_validator, field_validator


def tags(value):
    values = value if isinstance(value, (list, set, tuple)) else [value or '']
    return list(dict.fromkeys(t.strip() for v in values for t in re.split('[,，\n]', str(v)) if t.strip()))


class SiteRule(BaseModel):
    site_id: int
    detection: Literal['marked', 'all', 'none'] = 'marked'
    tracker_hosts: list[str] = Field(default_factory=list)
    seed_hours: float = Field(default=168, ge=0, allow_inf_nan=False)
    ratio: float = Field(default=0, ge=0, allow_inf_nan=False)
    ratio_basis: Literal['client', 'full_size'] = 'client'

    @model_validator(mode='after')
    def target(self):
        if self.seed_hours <= 0 and self.ratio <= 0:
            raise ValueError('时长和分享指标至少启用一项')
        return self


class ScanTask(BaseModel):
    id: str
    name: str
    enabled: bool = True
    downloader: str
    cron: str = '7,22,37,52 * * * *'
    extra_tags: list[str] = Field(default_factory=list)
    fallback_hours: float = Field(default=168, gt=0, allow_inf_nan=False)
    remove_extra_tags: bool = False
    # None 跟随全部站点，空列表明确不处理任何站点。
    site_ids: list[int] | None = None

    def matches_sites(self, site_ids):
        return self.site_ids is None or bool(set(self.site_ids) & set(site_ids))

    @field_validator('extra_tags', mode='before')
    @classmethod
    def normalize(cls, v): return tags(v)


class Config(BaseModel):
    enabled: bool = False
    confirmed_tag: str = 'H&R'
    pending_tag: str = 'H&R待核验'
    tag_aliases: list[str] = Field(default_factory=list)
    sites: list[SiteRule] = Field(default_factory=list)
    tasks: list[ScanTask] = Field(default_factory=list)
    started_at: float = 0
    @model_validator(mode='after')
    def validate_all(self):
        # 兼容旧 enabled 字段，实际启停只由下载器任务决定。
        self.enabled = any(t.enabled for t in self.tasks)
        for label in (self.confirmed_tag, self.pending_tag):
            if len(tags(label)) != 1 or tags(label)[0] != label or len(label) > 80:
                raise ValueError('保护标签须为单个名称，不含逗号或换行')
        if self.confirmed_tag == self.pending_tag:
            raise ValueError('确认标签与待确认标签须不同')
        names = [t.downloader for t in self.tasks]
        if len(names) != len(set(names)):
            raise ValueError('每个下载器只配置一个扫描任务，避免重复维护标签')
        if len({t.id for t in self.tasks}) != len(self.tasks):
            raise ValueError('扫描任务标识不可重复')
        if len({s.site_id for s in self.sites}) != len(self.sites):
            raise ValueError('站点规则不可重复')
        from apscheduler.triggers.cron import CronTrigger
        for task in self.tasks: CronTrigger.from_crontab(task.cron)
        return self


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError): return None


def decision(row, rules, record, task, config):
    """站内时长/指标或，多个站点且；未知同样按目标解除。"""
    source = set(row['tags']) - set(record.get('owned', []))
    forced = bool(source & (set(task.extra_tags) | {config.confirmed_tag, config.pending_tag} | set(config.tag_aliases)))
    signals = record.get('signals', {})
    ids = set(row['site_ids']) | {int(s) for s in signals}
    targets, unknown, positive = [], False, forced
    for sid in ids:
        rule = rules.get(sid)
        signal = signals.get(str(sid))
        if rule is None:
            unknown = True
            continue
        is_hr = forced or rule.detection == 'all' or signal is True
        uncertain = rule.detection == 'marked' and signal is None
        positive |= is_hr
        unknown |= uncertain
        if is_hr or uncertain: targets.append(rule)
    if not row['sites_complete'] or not ids:
        unknown = True
    if unknown and (not row['sites_complete'] or not ids or any(s not in rules for s in ids)):
        targets.append(SiteRule(site_id=0, seed_hours=task.fallback_hours))
    if not positive and not unknown:
        return 'clear', '明确非 H&R', set()
    if not targets:
        targets.append(SiteRule(site_id=0, seed_hours=task.fallback_hours))
    def satisfied(rule):
        seed = finite(row.get('seed_seconds'))
        metric = finite(row.get('ratio'))
        if rule.ratio_basis == 'full_size':
            uploaded, size = finite(row.get('uploaded')), finite(row.get('total_size'))
            metric = uploaded / size if uploaded is not None and size else None
        return ((rule.seed_hours > 0 and seed is not None and seed >= rule.seed_hours * 3600)
                or (rule.ratio > 0 and metric is not None and metric >= rule.ratio))
    if row['completed'] and all(satisfied(r) for r in targets):
        return 'released', '所有关联站点的本地时长或分享指标已达标', set()
    label = config.confirmed_tag if positive else config.pending_tag
    return ('protected' if positive else 'unknown', '下载完成且每个站点分别满足（做种时间或分享指标）后解除', {label})
