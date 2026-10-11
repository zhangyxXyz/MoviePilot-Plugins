"""保种清理回归测试：使用下载器替身，不连接真实服务或执行真实删除。
"""
import copy
import importlib.util
import sys
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('brushflow_guard_test_subject', ROOT / 'plugins.v3/brushflow/guarded_cleanup.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
GIB = guard.GIB

def torrent(h, hours=200, tags='刷流', size=100 * GIB):
    return dict(hash=h, name=h, seeding_time=hours * 3600, progress=1, amount_left=0, completion_on=1, state='stalledUP', tags=tags, save_path=str(Path('/downloads').absolute()), content_path=str(Path('/downloads').absolute() / h), size=size)

def file(path, size=100 * GIB, valid=True):
    return dict(path=path, size=size, valid=valid)

def _plan(ts, files, **kwargs):
    return guard.build_plan(ts, files, 168, **kwargs)




def test_manual_keep_tag_cannot_be_ignored_by_hr_bridge():
    rows = [torrent('a', tags='刷流,H&R,勿删')]
    decisions = {'a': {'managed': True, 'allow': True, 'owned_tags': ['H&R', '勿删']}}
    assert not _plan(rows, {'a': [file('/a')]}, protected_tags={'H&R', '勿删'}, hr_decisions=decisions)['groups']

def test_each_shared_seed_must_meet_its_own_target():
    ts = [torrent('a', 250), torrent('b', 300, '辅种')]
    fs = {'a': [file('/shared')], 'b': [file('/shared')]}
    targets = {'a': 240, 'b': 336}
    assert not _plan(ts, fs, min_hours_by_hash=targets)['groups']
    ts[1]['seeding_time'] = 336 * 3600
    group = _plan(ts, fs, min_hours_by_hash=targets)['groups'][0]
    assert group['required_hours'] == targets

def test_task_target_overrides_global_even_when_lower():
    assert _plan([torrent('a', 100)], {'a': [file('/a')]}, min_hours_by_hash={'a': 72})['groups']

def test_standalone_task_target_and_unassigned_affiliate():
    rows = [torrent('a', 100)]
    files = {'a': [file('/a')]}
    assert guard.build_plan(rows, files, 0, min_hours_by_hash={'a': 72})['groups']
    rows.append(torrent('b', 1000, '辅种'))
    files['b'] = [file('/a')]
    assert not guard.build_plan(rows, files, 0, min_hours_by_hash={'a': 72})['groups']

def test_multiple_task_owners_take_strictest_requirement():
    tasks = {'a': SimpleNamespace(id='a', downloader='qb', min_seed_hours=240), 'b': SimpleNamespace(id='b', downloader='qb', min_seed_hours=336), 'c': SimpleNamespace(id='c', downloader='other', min_seed_hours=999)}
    plugin = SimpleNamespace(_task_configs=tasks, _get_task_data=lambda *args: {'hash': {}})
    assert guard.minimum_hours_by_hash(plugin, 'qb') == {'hash': 336}

def test_effective_policy_inherits_and_overrides_every_field():
    plugin = SimpleNamespace(_global_min_seed_hours=168, _global_inactive_hours=48, _global_seed_ratio=2, _global_seed_size=10, _global_download_time=24, _global_seed_avgspeed=5, _global_hr_seed_time=240, _global_del_no_free=True, _global_delete_except_tags='MOVIEPILOT,H&R')
    inherited = guard.resolve_task_policy(plugin, SimpleNamespace())
    assert inherited['min_seed_hours'] == 168
    assert inherited['inactive_hours'] == 48
    assert inherited['del_no_free']
    task = SimpleNamespace(min_seed_hours=72, seed_inactivetime=60, seed_ratio=3, seed_size=20, download_time=12, seed_avgspeed=8, hr_seed_time=120, del_no_free=False, delete_except_tags='KEEP')
    actual = guard.resolve_task_policy(plugin, task)
    for key, value in dict(min_seed_hours=72, inactive_hours=1, seed_ratio=3, seed_size=20, download_time=12, seed_avgspeed=8, hr_seed_time=120, del_no_free=False, protected_tags={'KEEP'}).items():
        assert actual[key] == value
    assert guard.resolve_task_policy(plugin, SimpleNamespace(seed_time=36))['min_seed_hours'] == 36

def test_task_ratio_replaces_global_and_other_fields_still_inherit():
    plugin = SimpleNamespace(_global_min_seed_hours=168, _global_seed_ratio=2)
    policy = guard.resolve_task_policy(plugin, SimpleNamespace(seed_ratio=4))
    row = torrent('a', 1)
    row['ratio'] = 3
    args = dict(task_policies={'a': [policy]}, policy={'seed_ratio': 2})
    assert not _plan([row], {'a': [file('/a')]}, **args)['groups']
    row['ratio'] = 4
    assert _plan([row], {'a': [file('/a')]}, **args)['groups']
    row.update(ratio=0, seeding_time=168 * 3600)
    assert _plan([row], {'a': [file('/a')]}, **args)['groups']

def test_overlapping_owners_must_each_match_one_of_their_own_conditions():
    plugin = SimpleNamespace(_global_min_seed_hours=168)
    policies = [guard.resolve_task_policy(plugin, SimpleNamespace(min_seed_hours=72)), guard.resolve_task_policy(plugin, SimpleNamespace(seed_ratio=4))]
    row = torrent('a', 100)
    row['ratio'] = 3
    args = dict(task_policies={'a': policies})
    assert not _plan([row], {'a': [file('/a')]}, **args)['groups']
    row['ratio'] = 4
    assert _plan([row], {'a': [file('/a')]}, **args)['groups']

def test_task_tags_override_global_without_leaking_into_other_tasks():
    plugin = SimpleNamespace(_global_min_seed_hours=168, _global_delete_except_tags='KEEP')
    own = guard.resolve_task_policy(plugin, SimpleNamespace(delete_except_tags='PRIVATE'))
    default = guard.resolve_task_policy(plugin, SimpleNamespace())
    rows = [torrent('a', tags='刷流,KEEP'), torrent('b', tags='刷流,PRIVATE')]
    fs = {'a': [file('/a')], 'b': [file('/b')]}
    result = _plan(rows, fs, protected_tags={'KEEP'}, task_policies={'a': [own], 'b': [default]})
    assert len(result['groups']) == 2
    rows[0]['tags'] = '刷流,PRIVATE'
    assert len(_plan(rows, fs, task_policies={'a': [own], 'b': [default]})['groups']) == 1

def test_idle_48_hours_allows_seed_younger_than_seven_days():
    t = torrent('a', 2)
    t.update(last_activity=2000000000 - 48 * 3600, upspeed=0, dlspeed=0)
    p = _plan([t], {'a': [file('/a')]}, inactive_hours=48, now=2000000000)
    assert p['groups']
    assert p['groups'][0]['matched_rules']['a'] == ['连续未活动达到 48 小时']

def test_active_old_seed_satisfies_cumulative_branch():
    t = torrent('a', 168)
    t.update(last_activity=2000000000, upspeed=500, dlspeed=0)
    assert _plan([t], {'a': [file('/a')]}, inactive_hours=48, now=2000000000)['groups']

def test_shared_group_can_match_different_branches():
    a, b = (torrent('a', 168), torrent('b', 2, '辅种'))
    b.update(last_activity=2000000000 - 49 * 3600, upspeed=0, dlspeed=0)
    files = {'a': [file('/shared')], 'b': [file('/shared')]}
    assert _plan([a, b], files, inactive_hours=48, now=2000000000)['groups']
    b['last_activity'] = 2000000000 - 47 * 3600
    assert not _plan([a, b], files, inactive_hours=48, now=2000000000)['groups']

def test_unknown_or_future_activity_does_not_match_idle():
    for value in [None, 0, -1, float('nan'), 2000000001]:
        t = torrent('a', 2)
        t.update(last_activity=value, upspeed=0, dlspeed=0)
        assert not _plan([t], {'a': [file('/a')]}, inactive_hours=48, now=2000000000)['groups']

def test_current_transfer_prevents_idle_match():
    for speed in [None, 1]:
        t = torrent('a', 2)
        t.update(last_activity=1, upspeed=speed, dlspeed=0)
        assert not _plan([t], {'a': [file('/a')]}, inactive_hours=48, now=2000000000)['groups']

def test_recent_completion_bounds_idle_window():
    t = torrent('a', 2)
    t.update(last_activity=1, upspeed=0, dlspeed=0, completion_on=2000000000 - 3600)
    assert not _plan([t], {'a': [file('/a')]}, inactive_hours=48, now=2000000000)['groups']

def test_idle_rule_still_respects_protected_affiliate():
    t = torrent('a', 2, '刷流,H&R')
    t.update(last_activity=1, upspeed=0, dlspeed=0)
    assert not _plan([t], {'a': [file('/a')]}, inactive_hours=48, now=2000000000, protected_tags=['H&R'])['groups']

def test_idle_only_policy_does_not_require_cumulative_metadata():
    t = torrent('a', 2)
    t.update(seeding_time=None, last_activity=1, upspeed=0, dlspeed=0)
    assert guard.build_plan([t], {'a': [file('/a')]}, 0, inactive_hours=48, now=2000000000)['groups']

def test_ratio_and_upload_amount_are_independent_alternatives():
    for policy, metadata, reason in [({'seed_ratio': 2}, {'ratio': 2}, '分享率'), ({'seed_size': 10}, {'uploaded': 10 * GIB}, '上传量')]:
        t = torrent('a', 1)
        t.update(metadata)
        plan = guard.build_plan([t], {'a': [file('/a')]}, 0, policy=policy)
        assert reason in plan['groups'][0]['matched_rules']['a'][0]

def test_average_speed_requires_valid_age_and_thirty_minutes_seeding():
    t = torrent('a', 1)
    t.update(added_on=1000, uploaded=1024)
    fs = {'a': [file('/a')]}
    assert guard.build_plan([t], fs, 0, policy={'seed_avgspeed': 1}, now=10000)['groups']
    for field, value in [('seeding_time', 1799), ('added_on', None), ('uploaded', None)]:
        changed = dict(t)
        changed[field] = value
        assert not guard.build_plan([changed], fs, 0, policy={'seed_avgspeed': 1}, now=10000)['groups']

def test_download_timeout_is_only_for_incomplete_downloads():
    t = torrent('a', 0)
    t.update(progress=0.5, amount_left=100, completion_on=0, state='downloading', added_on=1000)
    f = file('/a', valid=False)
    f['path_safe'] = True
    assert guard.build_plan([t], {'a': [f]}, 0, policy={'download_time': 2}, now=8200)['groups']
    assert not guard.build_plan([torrent('a')], {'a': [file('/a')]}, 0, policy={'download_time': 2}, now=8200)['groups']

def test_old_incomplete_download_does_not_match_seed_or_idle():
    t = torrent('a', 500)
    t.update(progress=0.5, amount_left=100, completion_on=0, state='stalledDL', added_on=1, last_activity=1, upspeed=0, dlspeed=0)
    f = file('/a', valid=False)
    f['path_safe'] = True
    assert not _plan([t], {'a': [f]}, inactive_hours=48, now=2000000000)['groups']

def test_expired_promotion_needs_enabled_rule_known_expiry_and_incomplete_download():
    t = torrent('a', 0)
    t.update(progress=0.5, amount_left=100, completion_on=0, state='pausedDL')
    f = file('/a', valid=False)
    f['path_safe'] = True
    for expiry, expected in [(None, False), (0, False), (10001, False), (9000, True)]:
        plan = guard.build_plan([t], {'a': [f]}, 0, policy={'del_no_free': True}, promotion_expiries={'a': expiry}, now=10000)
        assert bool(plan['groups']) == expected
    assert not _plan([t], {'a': [f]}, promotion_expiries={'a': 9000}, now=10000)['groups']


def test_new_conditions_do_not_bypass_shared_group_eligibility():
    a, b = (torrent('a', 1), torrent('b', 1, '辅种'))
    a['ratio'] = 3
    b['ratio'] = 0
    fs = {'a': [file('/x')], 'b': [file('/x')]}
    assert not _plan([a, b], fs, policy={'seed_ratio': 2})['groups']
    b['ratio'] = 2
    assert _plan([a, b], fs, policy={'seed_ratio': 2})['groups']

def test_promotion_metadata_uses_latest_owner_and_hr_flags():
    task = lambda id: SimpleNamespace(id=id, downloader='qb', timezone_offset=0, site_hr_active=False)
    records = {'one': {'hash': {'freedate': 100, 'hit_and_run': True}}, 'two': {'hash': {'freedate': 200}}}
    plugin = SimpleNamespace(_task_configs={'one': task('one'), 'two': task('two')}, _global_del_no_free=True, _get_task_data=lambda id, key: records[id], _promotion_expiry_at=lambda value, offset: SimpleNamespace(timestamp=lambda: value) if value else None)
    hr, expiry = guard.policy_metadata(plugin, 'qb')
    assert hr == {'hash'}
    assert expiry == {'hash': 200}
    records['two']['hash']['freedate'] = None
    assert guard.policy_metadata(plugin, 'qb')[1] == {}

def test_under_minimum_never_eligible():
    p = _plan([torrent('a', 167.99)], {'a': [file('/a')]})
    assert p['groups'] == []

def test_exact_minimum_is_eligible():
    assert len(_plan([torrent('a', 168)], {'a': [file('/a')]})['groups']) == 1

def test_elapsed_age_does_not_replace_cumulative_time():
    t = torrent('a', 2)
    t['completion_on'] = 1
    assert not _plan([t], {'a': [file('/a')]})['groups']

def test_young_untagged_reseed_protects_old_brush():
    ts = [torrent('a', 400), torrent('b', 2, '辅种')]
    p = _plan(ts, {'a': [file('/shared')], 'b': [file('/shared')]})
    assert not p['groups']
    assert p['total_bytes'] == 100 * GIB

def test_shared_files_deleted_as_one_group():
    ts = [torrent('a', 400), torrent('b', 200, '辅种')]
    p = _plan(ts, {'a': [file('/shared')], 'b': [file('/shared')]})
    assert p['groups'][0]['hashes'] == ['a', 'b']
    assert p['groups'][0]['seed_seconds'] == 200 * 3600

def test_transitive_partial_overlap_is_one_group():
    ts = [torrent('a'), torrent('b', tags='辅种'), torrent('c', 1, '辅种')]
    fs = {'a': [file('/x')], 'b': [file('/x'), file('/y')], 'c': [file('/y')]}
    assert not _plan(ts, fs)['groups']

def test_same_name_different_path_is_not_grouped():
    ts = [torrent('a'), torrent('b', 1, '辅种')]
    ts[1]['name'] = 'a'
    p = _plan(ts, {'a': [file('/one/a')], 'b': [file('/two/a')]})
    assert p['groups'][0]['hashes'] == ['a']

def test_protected_tag_on_affiliate_protects_group():
    ts = [torrent('a'), torrent('b', tags='H&R')]
    assert not _plan(ts, {'a': [file('/x')], 'b': [file('/x')]}, protected_tags=['H&R'])['groups']

def test_persisted_hr_flag_protects_group():
    assert not _plan([torrent('a')], {'a': [file('/a')]}, protected_hashes=['a'])['groups']

def test_incomplete_reseed_protects_group():
    ts = [torrent('a'), torrent('b', tags='辅种')]
    ts[1]['progress'] = 0.9
    assert not _plan(ts, {'a': [file('/x')], 'b': [file('/x')]})['groups']

def test_missing_unknown_invalid_values_fail_closed():
    for field, value in [('seeding_time', None), ('seeding_time', float('nan')), ('seeding_time', -1), ('progress', None), ('completion_on', 0), ('amount_left', None), ('state', 'missingFiles'), ('state', None), ('state', ''), ('state', 'futureUnknownState')]:
        t = torrent('a')
        t[field] = value
        assert not _plan([t], {'a': [file('/a')]})['groups']

def test_invalid_file_blocks_group():
    assert not _plan([torrent('a')], {'a': [file('/a', valid=False)]})['groups']

def test_manifest_failure_aborts_even_for_nonbrush():
    with pytest.raises(ValueError):
        _plan([torrent('a'), torrent('b', tags='other')], {'a': [file('/a')]})

def test_conflicting_shared_sizes_abort():
    with pytest.raises(ValueError):
        _plan([torrent('a'), torrent('b')], {'a': [file('/x', 1)], 'b': [file('/x', 2)]})

def test_shared_capacity_counted_once():
    p = _plan([torrent('a'), torrent('b')], {'a': [file('/x')], 'b': [file('/x')]})
    assert p['total_bytes'] == 100 * GIB

def test_unmanaged_unrelated_tasks_not_selected():
    p = _plan([torrent('a', tags='辅种')], {'a': [file('/a')]})
    assert p['total_bytes'] == 0
    assert not p['groups']

class FakeQB:

    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.files = {t['hash']: [dict(name=t['name'], size=t['size'], progress=1)] for t in rows}
        self.deletions = []
        self.on_reannounce = None

    def torrents_info(self):
        return copy.deepcopy(self.rows)

    def torrents_files(self, torrent_hash):
        return copy.deepcopy(self.files[torrent_hash])

    def torrents_reannounce(self, torrent_hashes):
        if self.on_reannounce:
            self.on_reannounce()

    def torrents_delete(self, delete_files, torrent_hashes):
        assert delete_files
        self.deletions.append(list(torrent_hashes))
        self.rows = [t for t in self.rows if t['hash'] not in torrent_hashes]

@pytest.fixture
def run_cleanup():

    def run_cleanup(qb, task_min=None, inactive_hours=None):
        task = SimpleNamespace(id='task', enabled=True, proxy_delete=True, downloader='qb', site_hr_active=False, delete_except_tags='MOVIEPILOT,H&R', min_seed_hours=task_min)
        records = {t['hash']: dict(hit_and_run=False) for t in qb.rows if '刷流' in t['tags']}
        plugin = SimpleNamespace(_global_inactive_hours=inactive_hours, _global_min_seed_hours=168, _global_delete_size_range='450-550', _enabled=True, _task_configs={'task': task}, _get_task_data=lambda *args: records, _save_task_data=MagicMock(), _recalculate_statistics=MagicMock(), save_data=MagicMock())
        run_cleanup.plugin = plugin
        service = SimpleNamespace(instance=SimpleNamespace(qbc=qb))
        helper = SimpleNamespace(get_service=lambda **kwargs: service)
        modules = {'app.sdk.logging': SimpleNamespace(logger=MagicMock()), 'app.sdk.services': SimpleNamespace(DownloaderHelper=lambda: helper)}
        with patch.dict(sys.modules, modules), patch.object(guard.time, 'sleep'), patch.object(guard, 'configured_hr_tags', return_value={'H&R','H&R待核验'}):
            return guard.execute(plugin)
    return run_cleanup

def test_executor_obeys_persisted_task_target(run_cleanup):
    qb = FakeQB([torrent('a', hours=250, size=600 * GIB)])
    assert run_cleanup(qb, task_min=336) == 0
    assert not qb.deletions
    qb.rows[0]['seeding_time'] = 336 * 3600
    assert run_cleanup(qb, task_min=336) == 1
    assert qb.deletions == [['a']]

def test_executor_uses_idle_condition_and_records_actual_reason(run_cleanup):
    t = torrent('a', hours=2, size=600 * GIB)
    t.update(last_activity=1, upspeed=0, dlspeed=0)
    qb = FakeQB([t])
    assert run_cleanup(qb, inactive_hours=48) == 1
    record = run_cleanup.plugin._save_task_data.call_args.args[2]['a']
    assert '连续未活动达到 48 小时' in record['delete_reason']
    assert '累计做种达标' not in record['delete_reason']

def test_below_high_watermark_never_deletes(run_cleanup):
    qb = FakeQB([torrent('a', size=549 * GIB)])
    assert run_cleanup(qb) == 0
    assert not qb.deletions

def test_capacity_cycle_stops_at_low_watermark(run_cleanup):
    qb = FakeQB([torrent(str(i), hours=200 + i) for i in range(6)])
    assert run_cleanup(qb) == 2
    assert len(qb.rows) == 4
    assert run_cleanup.plugin.save_data.call_args.args[1]['status'] == 'target_reached'

def test_high_capacity_does_not_override_minimum(run_cleanup):
    qb = FakeQB([torrent('a', hours=1, size=600 * GIB)])
    assert run_cleanup(qb) == 0
    assert not qb.deletions
    assert run_cleanup.plugin.save_data.call_args.args[1]['status'] == 'no_eligible_group'

def test_unknown_brush_record_blocks_deletion(run_cleanup):
    qb = FakeQB([torrent('a', size=600 * GIB), torrent('b', 1, '辅种')])
    qb.files['b'] = copy.deepcopy(qb.files['a'])
    assert run_cleanup(qb) == 0
    assert not qb.deletions

def test_new_young_reseed_before_delete_aborts(run_cleanup):
    qb = FakeQB([torrent('a', size=600 * GIB)])

    def add_reseed():
        qb.rows.append(torrent('b', 1, '辅种', size=600 * GIB))
        qb.files['b'] = copy.deepcopy(qb.files['a'])
    qb.on_reannounce = add_reseed
    with pytest.raises(RuntimeError):
        run_cleanup(qb)
    assert not qb.deletions

def test_failed_delete_stops_further_groups(run_cleanup):
    qb = FakeQB([torrent(str(i), hours=200 + i) for i in range(6)])
    qb.torrents_delete = MagicMock()
    with pytest.raises(RuntimeError):
        run_cleanup(qb)
    assert qb.torrents_delete.call_count == 1

def test_shared_old_group_deleted_together(run_cleanup):
    qb = FakeQB([torrent('a', size=600 * GIB), torrent('b', 300, '辅种', size=600 * GIB)])
    qb.files['b'] = copy.deepcopy(qb.files['a'])
    assert run_cleanup(qb) == 2
    assert qb.deletions == [['a', 'b']]

def test_snapshot_rejects_path_traversal():
    qb = FakeQB([torrent('a')])
    qb.files['a'][0]['name'] = '../a'
    with pytest.raises(ValueError):
        guard.snapshot(qb)






def test_configured_affiliate_tag_protects_entire_shared_group():
    rows = [torrent('a'), torrent('b', tags='我的辅种')]
    files = {'a': [file('/shared')], 'b': [file('/shared')]}
    assert not _plan(rows, files, protected_tags={'我的辅种'})['groups']
    assert _plan(rows, files, protected_tags=set())['groups']





def test_hr_affiliate_uses_global_and_each_shared_owner_passes_separately():
    rows=[torrent('a',hours=200),torrent('b',hours=100,tags='辅种')]
    fs={'a':[file('/shared')], 'b':[file('/shared')]}
    kwargs=dict(hr_tags={'辅种'},policy={'hr_seed_time':168},task_policies={'a':[{'min_seed_hours':168,'inactive_hours':0,'protected_tags':set()}]})
    assert not _plan(rows,fs,**kwargs)['groups']
    rows[1]['seeding_time']=168*3600
    assert _plan(rows,fs,**kwargs)['groups']
    rows[1]['tags']='辅种,勿删'
    assert not _plan(rows,fs,protected_tags={'勿删'},**kwargs)['groups']





@pytest.mark.parametrize('label',['H&R','H&R待核验','我的HR'])
def test_helper_labels_block_even_with_task_override(label):
    row=torrent('a',hours=999,tags='刷流,'+label)
    kwargs=dict(always_protected_tags={label},task_policies={'a':[{'min_seed_hours':1,'inactive_hours':0,'protected_tags':set()}]})
    assert not _plan([row],{'a':[file('/a')]},**kwargs)['groups']
    row['tags']='刷流,辅种'
    assert _plan([row],{'a':[file('/a')]},**kwargs)['groups']

def test_legacy_hr_decision_cannot_strip_present_protection():
    row=torrent('a',hours=999,tags='刷流,H&R')
    assert not _plan([row],{'a':[file('/a')]},always_protected_tags={'H&R'},hr_decisions={'a':{'allow':True,'managed':True,'owned_tags':['H&R']}})['groups']
