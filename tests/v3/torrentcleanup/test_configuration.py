"""改名保留预览，安全规则修改仍必须使对应计划和观察失效。"""

import copy
import pytest
from app.plugins.torrentcleanup.configuration import reconcile_config


@pytest.fixture
def existing():
    """两项独立策略都有已有预览及累计观察。"""
    config = {'revision': 'r1', 'enabled': False, 'show_sidebar': True, 'sites': [],
              'tasks': [{'id': k, 'name': k, 'cron': '0 */6 * * *', 'notify': False,
                         'enabled': False, 'action': 'preview', 'min_seed_hours': 168} for k in ['a', 'b']]}
    plans = {k: {'id': 'plan-' + k, 'task_name': k, 'revision': 'r1', 'created_at': 123} for k in ['a', 'b']}
    observations = {k + ':TR:hash:3': {'count': 3, 'first_at': 100} for k in ['a', 'b']}
    return config, plans, observations


@pytest.mark.parametrize('field,value', [('name', '新名字'), ('cron', '0 */12 * * *'), ('notify', True)])
def test_display_changes_keep_plan_identity_age_and_observations(existing, field, value):
    """展示和调度修改不能重置观察期，也不能延长执行有效期。"""
    old, plans, observations = existing
    new = copy.deepcopy(old); new['tasks'][0][field] = value
    config, kept, records = reconcile_config(old, new, plans, observations)
    assert config['revision'] == 'r1' and records == observations
    assert kept['a']['id'] == 'plan-a' and kept['a']['created_at'] == 123
    assert kept['a']['task_name'] == new['tasks'][0]['name']
    assert plans['a']['task_name'] == 'a'


@pytest.mark.parametrize('field,value', [('min_seed_hours', 12), ('enabled', True), ('action', 'delete')])
def test_rule_change_invalidates_only_affected_task(existing, field, value):
    """动作、开关或阈值变化必须重新预览，但不波及另一个任务。"""
    old, plans, observations = existing
    new = copy.deepcopy(old); new['tasks'][0][field] = value
    config, kept, records = reconcile_config(old, new, plans, observations)
    assert config['revision'] != 'r1'
    assert set(kept) == {'b'} and kept['b']['revision'] == config['revision']
    assert set(records) == {'b:TR:hash:3'}


def test_site_or_global_enable_change_invalidates_all(existing):
    """站点映射可能改变原本未知的任务归属，因此影响全部计划。"""
    old, plans, observations = existing
    for change in [{'sites': [{'site_id': 3, 'tracker_hosts': ['tracker.test']}]}, {'enabled': True}]:
        _, kept, records = reconcile_config(old, {**old, **change}, plans, observations)
        assert kept == {} and records == {}


def test_sidebar_noop_and_task_deletion(existing):
    """侧栏设置不清理计划，删除任务只移除它自身的数据。"""
    old, plans, observations = existing
    _, kept, records = reconcile_config(old, {**old, 'show_sidebar': False}, plans, observations)
    assert kept == plans and records == observations
    _, kept, records = reconcile_config(old, {**old, 'show_preview': False}, plans, observations)
    assert kept == plans and records == observations
    _, kept, records = reconcile_config(old, {**old, 'tasks': old['tasks'][1:]}, plans, observations)
    assert set(kept) == {'b'} and set(records) == {'b:TR:hash:3'}


def test_request_limits_do_not_erase_observations(existing):
    """站点请求节奏不改变清理依据，调整额度仍保留已有观察。"""
    old, plans, observations = existing
    old['sites'] = [{'site_id': 3, 'verification': 'nexusphp', 'max_requests_per_hour': 20}]
    new = copy.deepcopy(old); new['sites'][0]['max_requests_per_hour'] = 120
    _, kept, records = reconcile_config(old, new, plans, observations)
    assert kept == plans and records == observations
