"""配置变化按实际判断依据失效预览，展示字段不影响观察记录。"""

import copy
import uuid
from .quota import TIMING_FIELDS


def save_task_in_place(tasks, task):
    """编辑保持原位置，新建追加末尾。"""
    if any(t['id'] == task['id'] for t in tasks):
        return [copy.deepcopy(task if t['id'] == task['id'] else t) for t in tasks]
    if len(tasks) >= 100:
        raise ValueError('清理任务数量超过上限')
    return copy.deepcopy([*tasks, task])


def reorder_tasks(tasks, ids):
    """拒绝重复、遗漏和过期清单，避免覆盖并发新增或删除的任务。"""
    current = {t['id']: t for t in tasks}
    if len(ids) != len(current) or len(set(ids)) != len(ids) or set(ids) != set(current):
        raise ValueError('任务列表已变化，请刷新后重新排序')
    return [copy.deepcopy(current[key]) for key in ids]


def reconcile_config(previous, updated, plans, observations):
    """保留未受影响任务的计划和采样，改名只同步显示名称而不延长有效期。"""
    updated, plans, observations = copy.deepcopy((updated, plans or {}, observations or {}))
    old_tasks = {t['id']: t for t in previous.get('tasks', [])}
    new_tasks = {t['id']: t for t in updated.get('tasks', [])}
    task_ids = set(old_tasks) | set(new_tasks)
    display_fields = {'name', 'cron', 'notify'}
    affected = {key for key in task_ids
                if {k: v for k, v in old_tasks.get(key, {}).items() if k not in display_fields}
                != {k: v for k, v in new_tasks.get(key, {}).items() if k not in display_fields}}
    timing_fields = TIMING_FIELDS
    old_sites = {s['site_id']: {k: v for k, v in s.items() if k not in timing_fields} for s in previous.get('sites', [])}
    new_sites = {s['site_id']: {k: v for k, v in s.items() if k not in timing_fields} for s in updated.get('sites', [])}
    if (previous.get('enabled', False) != updated.get('enabled', False) or old_sites != new_sites
            or any(previous.get(k) != updated.get(k) for k in ('hr_enabled', 'hr_downloaders', 'hr_auto_release'))):
        affected = task_ids
    revision = uuid.uuid4().hex if affected else previous.get('revision') or uuid.uuid4().hex
    updated['revision'] = revision
    kept = {}
    for key, plan in plans.items():
        if key in new_tasks and key not in affected:
            # 仅提升原本有效的配置版本；不会复活本来就已过期的配置计划。
            if plan.get('revision') == previous.get('revision'):
                plan['revision'] = revision
            plan['task_name'] = new_tasks[key]['name']
            kept[key] = plan
    observations = {key: value for key, value in observations.items()
                    if key.split(':', 1)[0] in new_tasks and key.split(':', 1)[0] not in affected}
    return updated, kept, observations
