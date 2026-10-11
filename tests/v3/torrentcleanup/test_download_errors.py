"""下载错误必须经过持续采样，并在恢复与保护状态变化时停止处理。"""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from app.plugins.torrentcleanup.download_errors import assess_download_error
from app.plugins.torrentcleanup.models import CleanupTask
from app.plugins.torrentcleanup.policy import Torrent
from app.plugins.torrentcleanup.mteam import parse_identity, candidates_from_text


def case():
    """构造已添加两天、未完成的明确错误。"""
    task = CleanupTask(id='err', name='错误', downloader='QB', mode='download_error', all_sites=True,
                       action='delete', error_observation_hours=1, error_gap_minutes=15)
    row = SimpleNamespace(torrent=Torrent('QB', 'a'*40, '错误', frozenset(), False,
        False, False, 0, 0, 100, hr=False), client_state='error', progress=.2,
        download_speed=0, added_at=100, downloaded=20)
    return task, row, {}, 200000


def test_duration_and_spacing_required():
    """反复点击不增加有效采样，次数和时间缺一不可。"""
    task, row, data, now = case()
    assert not assess_download_error(task, row, data, now).eligible
    assert not assess_download_error(task, row, data, now+1).eligible
    assert next(iter(data.values()))['count'] == 1
    assert not assess_download_error(task, row, data, now+1800).eligible
    assert assess_download_error(task, row, data, now+3600).eligible


@pytest.mark.parametrize('field,value', [('client_state','downloading'), ('progress',.3), ('downloaded',30), ('download_speed',1), ('added_at',199999)])
def test_recovery_or_identity_change_restarts_observation(field, value):
    """状态恢复、下载量变化或重新添加都不能沿用旧观察。"""
    task, row, data, now = case()
    for offset in [0, 1800, 3600]: assess_download_error(task, row, data, now+offset)
    setattr(row, field, value)
    assert not assess_download_error(task, row, data, now+4000).eligible
    assert not data or next(iter(data.values()))['count'] == 1


@pytest.mark.parametrize('patch', [{'completed': True}, {'hr': True}, {'hr': None}, {'tags': frozenset({'H&R'})}])
def test_completed_and_hr_protected(patch):
    """已完成、明确或未知 H&R 以及保护标签均阻止默认清理。"""
    task, row, data, now = case()
    row.torrent = replace(row.torrent, **patch)
    for offset in [0, 1800, 3600]: result = assess_download_error(task, row, data, now+offset)
    assert not result.eligible


def test_stalled_is_explicit_opt_in_and_sites_are_scoped():
    """默认不清停滞，指定站点时不允许未知来源。"""
    task, row, data, now = case()
    row.client_state = 'stalledDL'
    assert not assess_download_error(task, row, data, now).eligible
    task.error_states = ['stalledDL']
    for offset in [0, 1800, 3600]: result = assess_download_error(task, row, data, now+offset)
    assert result.eligible
    task.all_sites = False; task.site_ids = [1, 2]
    assert not assess_download_error(task, row, data, now+4000).eligible


def test_mteam_requires_exact_hash_and_id():
    """HTTP 成功或仅有数字 ID 不能证明种子身份，错误响应也不是不存在证据。"""
    for payload in [None, [], {}, {'code': 0, 'data': {'id': '1'}}, {'code': 404},
                    {'code': 0, 'data': {'id': '2', 'hash': 'a'*40}}]:
        assert not parse_identity(payload, '1', 'a'*40)
    assert parse_identity({'code': '0', 'data': {'id': 1, 'infoHash': 'a'*40}}, '1', 'a'*40)
    assert candidates_from_text('https://kp.m-team.cc/', '998871', True) == ['https://kp.m-team.cc/detail/998871']
    assert candidates_from_text('https://kp.m-team.cc/', 'https://evil.test/detail/1') == []
