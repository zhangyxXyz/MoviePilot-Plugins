"""旧 H&R 字段仅兼容配置，新入口统一由助手标签保护。"""
from app.plugins.torrentcleanup.models import CleanupTask
from app.plugins.torrentcleanup.engine import Engine
from app.plugins.torrentcleanup.gateway import Snapshot
from app.plugins.torrentcleanup.policy import Torrent
from types import SimpleNamespace
import sys

def test_legacy_local_hr_cannot_bypass_helper_tag(monkeypatch):
    monkeypatch.setitem(sys.modules,'app.sdk.plugins',SimpleNamespace(PluginManager=lambda:SimpleNamespace(get_plugin_config=lambda _:{'confirmed_tag':'我的HR'})))
    t=Torrent('QB','a'*40,'seed',frozenset({1}),True,True,True,999*3600,999,100,frozenset({'我的HR'}),False,frozenset(),False)
    row=Snapshot(t,'/test',{},[],client_state='stalledUP',progress=1)
    task=CleanupTask(name='test',downloader='QB',site_ids=[1],mode='conditions',verify_invalid=False,filter_seed=True,min_seed_hours=1,hr_local_conditions=True,hr_seed_hours=1,protect_tags=['我的HR'])
    engine=Engine(SimpleNamespace(policies={}),lambda _:None,lambda *args:None,lambda:False)
    decision,_=engine.assess(task,row,{}, {})
    assert not decision.eligible
    assert any('保护标签' in r for r in decision.reasons)


def test_dead_task_does_not_implicitly_protect_hr_labels(monkeypatch):
    monkeypatch.setitem(sys.modules,'app.sdk.plugins',SimpleNamespace(PluginManager=lambda:SimpleNamespace(get_plugin_config=lambda _:{'confirmed_tag':'H&R'})))
    t=Torrent('QB','a'*40,'seed',frozenset({1}),True,True,True,999*3600,999,100,frozenset({'H&R','H&R待核验','辅种'}),True,frozenset(),False)
    row=Snapshot(t,'/test',{},[],client_state='stalledUP',progress=1)
    task=CleanupTask(name='test',downloader='QB',site_ids=[1],mode='conditions',verify_invalid=False,filter_seed=True,min_seed_hours=1,protect_tags=['保留','勿删'])
    engine=Engine(SimpleNamespace(policies={}),lambda _:None,lambda *args:None,lambda:False)
    decision,_=engine.assess(task,row,{}, {})
    assert decision.eligible
