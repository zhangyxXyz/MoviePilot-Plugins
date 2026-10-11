import importlib.util
from pathlib import Path
import pytest
p=Path(__file__).resolve().parents[3]/'plugins.v3/hitandrun/policy.py'
s=importlib.util.spec_from_file_location('hrpolicy',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

def decide(**updates):
    row=dict(tags=[],site_ids=[1],sites_complete=True,completed=True,seed_seconds=0,ratio=0,uploaded=0,total_size=100)
    row.update(updates)
    return m.decision(row,{1:m.SiteRule(site_id=1,ratio=2)}, {},m.ScanTask(id='a',name='scan',downloader='qb'),m.Config())

def test_unknown_protected_then_time_release():
    assert decide()[2]=={'H&R待核验'}
    assert decide(seed_seconds=168*3600)[0]=='released'

def test_ratio_release(): assert decide(ratio=2)[0]=='released'
def test_incomplete_never_releases(): assert decide(completed=False,ratio=100,seed_seconds=9999999)[0]=='unknown'
def test_invalid_metrics_do_not_release(): assert decide(ratio=float('nan'),seed_seconds=None)[0]=='unknown'
def test_custom_tag():
    cfg=m.Config(confirmed_tag='HR保护',pending_tag='HR待确认')
    task=m.ScanTask(id='x',name='x',downloader='qb',extra_tags=['辅种'])
    row=dict(tags=['辅种'],site_ids=[1],sites_complete=True,completed=False)
    assert m.decision(row,{1:m.SiteRule(site_id=1,detection='none')},{},task,cfg)[2]=={'HR保护'}
def test_sites_all_required_and_full_size_basis():
    row=dict(tags=['H&R'],site_ids=[1,3],sites_complete=True,completed=True,seed_seconds=0,ratio=10,uploaded=1900,total_size=100)
    rules={1:m.SiteRule(site_id=1,ratio=2),3:m.SiteRule(site_id=3,ratio=20,ratio_basis='full_size')}
    args=[row,rules,{},m.ScanTask(id='x',name='x',downloader='qb'),m.Config()]
    assert m.decision(*args)[0]=='protected'
    row['uploaded']=2000
    assert m.decision(*args)[0]=='released'
def test_unknown_site_fallback_cannot_use_other_site_ratio():
    assert decide(sites_complete=False,ratio=999)[0]=='unknown'
def test_rule_validation():
    with pytest.raises(ValueError):m.SiteRule(site_id=1,seed_hours=0,ratio=0)
    with pytest.raises(ValueError):m.Config(confirmed_tag='a,b')
    with pytest.raises(ValueError):m.Config(confirmed_tag='same',pending_tag='same')
def test_false_signal_clear():
    row=dict(tags=[],site_ids=[1],sites_complete=True,completed=False)
    assert m.decision(row,{1:m.SiteRule(site_id=1)},{'signals':{'1':False}},m.ScanTask(id='x',name='x',downloader='qb'),m.Config())[0]=='clear'

def test_task_switch_is_the_only_enable_control():
    task=dict(id='a',name='a',downloader='qb',enabled=True)
    assert m.Config(enabled=False,tasks=[task]).enabled
    task['enabled']=False
    assert not m.Config(enabled=True,tasks=[task]).enabled
