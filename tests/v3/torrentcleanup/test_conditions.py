"""状态与错误关键词的组合筛选不绕过保护或执行前复核。"""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from app.plugins.torrentcleanup.models import CleanupTask
from app.plugins.torrentcleanup.conditions import assess_conditions
from app.plugins.torrentcleanup.policy import Torrent


def case(**fields):
    """建立停用、仅预览的条件任务与未完成下载样本。"""
    task = CleanupTask(id='c',name='条件',downloader='TR',mode='conditions',all_sites=True,
                       completion='all',require_healthy=False,filter_seed=False,filter_ratio=False,**fields)
    row = SimpleNamespace(torrent=Torrent('TR','a'*40,'样本',frozenset(),False,False,False,0,0,100,hr=False),
                          client_state='0',has_error=True,error_message='Tracker: torrent not registered',
                          progress=.2,downloaded=20,added_at=100,download_speed=0)
    return task,row


def test_state_or_keywords_or_groups_and():
    """同组任一命中，不同组同时满足，TR error 与数字状态独立。"""
    task,row=case(states=['4','error'],error_keywords=['missing','NOT REGISTERED'])
    assert assess_conditions(task,row,{}, {}, {},200000).eligible
    row.error_message='timeout'
    assert not assess_conditions(task,row,{}, {}, {},200000).eligible
    row.error_message='not registered';row.has_error=False
    assert not assess_conditions(task,row,{}, {}, {},200000).eligible
    row.client_state='4'
    assert assess_conditions(task,row,{}, {}, {},200000).eligible


def test_state_observation_recovers_and_resets():
    """持续条件不跨状态恢复或进度变化累计。"""
    task,row=case(states=['error'],observe_state=True,error_observation_hours=1)
    data={}
    for n in [200000,201800,203600]: result=assess_conditions(task,row,data,{}, {},n)
    assert result.eligible
    row.progress=.3
    assert not assess_conditions(task,row,data,{}, {},204000).eligible
    assert next(iter(data.values()))['count']==1
    row.has_error=False
    assert not assess_conditions(task,row,data,{}, {},204100).eligible and not data


@pytest.mark.parametrize('patch',[{'hr':True},{'hr':None},{'tags':frozenset({'H&R'})}])
def test_protection_still_blocks(patch):
    """状态命中不能覆盖 H&R 与标签保护。"""
    task,row=case(states=['error'])
    row.torrent=replace(row.torrent,**patch)
    assert not assess_conditions(task,row,{}, {}, {},200000).eligible


def test_disabled_thresholds_do_not_filter():
    """未勾选的做种时间和分享率条件不参与判断。"""
    task,row=case(states=['0'])
    assert assess_conditions(task,row,{}, {}, {},200000).eligible
    task.filter_seed=True
    assert not assess_conditions(task,row,{}, {}, {},200000).eligible
