"""检查通知明确区分实际确认结果和未完成操作，不误报释放空间。"""
from types import SimpleNamespace
from app.plugins.torrentcleanup.notifications import execution_notification


def test_execution_results_are_distinct():
    task = SimpleNamespace(name='下载错误清理', downloader='QB', action='delete')
    report = {'items': [
        {'name': '已处理影片', 'size': 2*1024**3, 'status': 'confirmed', 'cleanup_reasons': ['命中状态 error']},
        {'name': '未确认影片', 'size': 3*1024**3, 'status': 'unconfirmed', 'reason': '操作结果未确认'},
        {'name': '恢复影片', 'size': 4*1024**3, 'status': 'skipped', 'reason': '下载仍在进行'},
    ]}
    title, text = execution_notification(task, report)
    assert title == '清理任务：下载错误清理'
    assert '已确认任务体积：2.00 GiB' in text
    assert '【待确认】未确认影片' in text and '【跳过】恢复影片' in text
    assert '命中状态 error' in text and '保留文件' in text
    assert '释放' not in text


def test_empty_and_bounded_notifications():
    task = SimpleNamespace(name='清理', downloader='TR', action='deletefile')
    assert '没有处理任何种子' in execution_notification(task, {'items': []})[1]
    report = {'items': [{'name': '片名'*300, 'status': 'failed', 'reason': '失败'*300} for _ in range(50)]}
    text = execution_notification(task, report)[1]
    assert len(text) < 6000 and '其余' in text and '失败 50 项' in text


import pytest


@pytest.mark.parametrize('statuses,notify,expected', [
    ([], True, 0),
    (['skipped'], True, 0),
    (['failed', 'unconfirmed'], True, 0),
    (['confirmed'], True, 1),
    (['confirmed', 'failed', 'skipped'], True, 1),
    (['confirmed'], False, 0),
])
@pytest.mark.parametrize('scheduled', [True, False])
def test_run_notifies_only_confirmed_actions(monkeypatch, statuses, notify, expected, scheduled):
    import app.plugins.torrentcleanup as module
    plugin = module.TorrentCleanup()
    task = SimpleNamespace(id='task', name='清理', downloader='QB', action='delete', enabled=True, notify=notify)
    plan = {'id': 'plan', 'items': []}
    report = {'id': 'run', 'items': [{'name': '测试种子', 'status': status} for status in statuses]}
    stored = {'plans': {'task': plan}}
    notices = []
    class FakeEngine:
        def __init__(self, *args): pass
        def preview(self, *args, **kwargs): return plan
        def execute(self, *args, progress, **kwargs):
            progress(report)
            return report
    monkeypatch.setattr(module, 'Engine', FakeEngine)
    plugin._config = {'enabled': True, 'revision': 'revision'}
    plugin._task = lambda _: task
    plugin._gateway = lambda: None
    plugin.get_data = stored.get
    plugin.save_data = lambda key, value: stored.update({key: value})
    plugin.post_message = lambda **message: notices.append(message)
    plugin.run('task', plan_id=None if scheduled else 'plan', scheduled=scheduled)
    assert len(notices) == expected
    assert stored['runs'] == [report]
    assert plugin._runtime['task']['status'] == 'idle'
