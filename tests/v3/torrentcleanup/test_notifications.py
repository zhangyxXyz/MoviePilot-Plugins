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
