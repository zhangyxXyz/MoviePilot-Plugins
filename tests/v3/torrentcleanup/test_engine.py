"""执行闭环测试，下载器与持久化全部使用内存替身。"""
import copy
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from app.plugins.torrentcleanup.engine import Engine
from app.plugins.torrentcleanup.models import CleanupTask
from app.plugins.torrentcleanup.policy import Torrent


class FakeGateway:
    """记录命令，并模拟现场条件变化及不确定响应。"""
    def __init__(self):
        """建立一条满足保留条件的任务。"""
        self.row = SimpleNamespace(torrent=Torrent('QB', 'a'*40, '示例', frozenset({3}), True,
            True, True, 200*3600, 2, 1024, hr=False,
            files=frozenset({'path:/test/a'}), files_verified=True), path='/test', local_paths=['/test/a'])
        self.commands = []
        self.accepted = True
        self.extra = []
        self.complete = True

    def snapshot(self, *args, **kwargs):
        """返回当前快照。"""
        return [self.row]

    def inventory(self):
        """返回跨下载器文件引用。"""
        return [self.row, *self.extra], self.complete

    def apply(self, torrent, action):
        """记录命令以验证不会误操作。"""
        self.commands.append((torrent.hash, action))
        return self.accepted

    def confirm(self, *args):
        """模拟已下发命令的下载器确认。"""
        return bool(self.commands) and self.accepted


@pytest.fixture
def setup_engine():
    """构造具有拷贝语义的数据存储。"""
    data = {}
    gateway = FakeGateway()
    def read(key):
        """模拟持久化读。"""
        return copy.deepcopy(data.get(key))
    def save(key, value):
        """模拟持久化写。"""
        data[key] = copy.deepcopy(value)
    task = CleanupTask(id='task', name='测试', downloader='QB', site_ids=[3], action='delete')
    return Engine(gateway, read, save, lambda: False), gateway, task, data


def test_preview_is_read_only_and_execution_is_confirmed(setup_engine):
    """预览不产生写命令，执行持久化结果。"""
    engine, gateway, task, data = setup_engine
    plan = engine.preview(task, 'r1')
    assert plan['candidate_count'] == 1 and not gateway.commands
    report = engine.execute(task, plan, 'r1')
    assert len(gateway.commands) == 1
    assert report['items'][0]['status'] == 'confirmed'
    assert data['receipts']['QB:' + 'a'*40]['status'] == 'confirmed'


def test_preview_reports_stages_and_completed_count(setup_engine):
    """逐项发布进度，完成前不保存可执行计划，也不产生下载器写操作。"""
    engine, gateway, task, data = setup_engine
    events = []
    plan = engine.preview(task, 'r1', progress=events.append)
    assert events[0]['phase'] == 'download' and events[0]['total'] is None
    assert any(e.get('current_name') == '示例' and e['completed'] == 0 for e in events)
    assert events[-1]['phase'] == 'complete'
    assert events[-1]['completed'] == events[-1]['total'] == len(plan['items']) == 1
    assert 'plans' not in data and not gateway.commands


def test_download_error_execution_rechecks_recovery(setup_engine, monkeypatch):
    """候选经过错误观察后若已恢复下载，执行前必须跳过且不发送命令。"""
    engine, gateway, _, data = setup_engine
    task = CleanupTask(id='task', name='错误', downloader='QB', mode='download_error', all_sites=True,
                       action='delete', error_observation_hours=1)
    gateway.row.torrent = replace(gateway.row.torrent, completed=False, healthy=False)
    for key, value in dict(client_state='error', progress=.2, download_speed=0, added_at=100, downloaded=20).items():
        setattr(gateway.row, key, value)
    for now in [200000, 201800, 203600]:
        monkeypatch.setattr('app.plugins.torrentcleanup.engine.time.time', lambda: now)
        plan = engine.preview(task, 'r1')
    assert plan['candidate_count'] == 1
    gateway.row.client_state = 'downloading'
    report = engine.execute(task, plan, 'r1')
    assert report['items'][0]['status'] == 'skipped' and not gateway.commands


@pytest.mark.parametrize('change', ['tag', 'path', 'size', 'pending_tag'])
def test_fresh_changes_block_execution(setup_engine, change):
    """预览之后保护条件、文件范围变化会阻止写命令。"""
    engine, gateway, task, _ = setup_engine
    plan = engine.preview(task, 'r1')
    if change == 'path':
        gateway.row.path = '/new'
    else:
        patch = {'tag': {'tags': frozenset({'H&R'})}, 'size': {'size': 2048}, 'pending_tag': {'tags': frozenset({'H&R待核验'})}}[change]
        gateway.row.torrent = replace(gateway.row.torrent, **patch)
    assert engine.execute(task, plan, 'r1')['items'][0]['status'] == 'skipped'
    assert not gateway.commands


@pytest.mark.parametrize('change', ['revision', 'expired', 'action'])
def test_stale_plan_cannot_execute(setup_engine, change):
    """配置、动作或有效期变化要求重新预览。"""
    engine, gateway, task, _ = setup_engine
    plan = engine.preview(task, 'r1')
    if change == 'revision': plan['revision'] = 'old'
    if change == 'expired': plan['created_at'] = time.time() - 901
    if change == 'action': plan['action'] = 'deletefile'
    with pytest.raises(ValueError): engine.execute(task, plan, 'r1')
    assert not gateway.commands


def test_unconfirmed_operation_is_not_retried(setup_engine):
    """下载器返回不确定结果后，重复计划不会再次删除。"""
    engine, gateway, task, data = setup_engine
    plan = engine.preview(task, 'r1')
    gateway.accepted = False
    assert engine.execute(task, plan, 'r1')['items'][0]['status'] == 'unconfirmed'
    assert engine.execute(task, plan, 'r1')['items'][0]['status'] == 'skipped'
    assert len(gateway.commands) == 1


def test_intent_must_persist_before_delete(setup_engine):
    """意图保存失败时绝不能调用下载器。"""
    engine, gateway, task, _ = setup_engine
    plan = engine.preview(task, 'r1')
    original = engine.save
    def fail(key, value):
        """模拟存储故障。"""
        if key == 'receipts': raise OSError('disk')
        original(key, value)
    engine.save = fail
    with pytest.raises(OSError): engine.execute(task, plan, 'r1')
    assert not gateway.commands


def test_new_shared_reference_blocks_file_deletion(setup_engine):
    """预览后其他下载器新增共享引用，执行前再次检查会发现。"""
    engine, gateway, task, _ = setup_engine
    task.action = 'deletefile'
    plan = engine.preview(task, 'r1')
    assert plan['candidate_count'] == 1
    gateway.extra = [SimpleNamespace(torrent=replace(gateway.row.torrent, downloader='TR'))]
    assert engine.execute(task, plan, 'r1')['items'][0]['status'] == 'skipped'
    assert not gateway.commands


def test_reconcile_only_reads_and_updates_original_report(setup_engine):
    """事后核对能解除已确认操作的锁定，但绝不重发命令。"""
    engine, gateway, task, data = setup_engine
    gateway.accepted = False
    report = engine.execute(task, engine.preview(task, 'r1'), 'r1')
    data['runs'] = [report]
    assert engine.reconcile(task.id) == {'confirmed': 0, 'pending': 1}
    gateway.accepted = True
    assert engine.reconcile(task.id) == {'confirmed': 1, 'pending': 0}
    assert len(gateway.commands) == 1
    assert data['runs'][0]['items'][0]['status'] == 'confirmed'
