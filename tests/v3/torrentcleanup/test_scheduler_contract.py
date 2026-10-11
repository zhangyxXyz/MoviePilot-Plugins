"""验证宿主周期服务从 func_kwargs 读取任务参数，无需启动下载器。"""
from types import SimpleNamespace
from app.plugins.torrentcleanup import TorrentCleanup


def test_host_dispatch_passes_each_task_identity():
    calls = []
    def run(task_id, scheduled=False):
        calls.append((task_id, scheduled))
    tasks = [{'id': str(i), 'name': str(i), 'enabled': True, 'cron': cron} for i, cron in enumerate(
        ['5 */6 * * *', '*/15 * * * *', '10,25,40,55 * * * *'])]
    obj = SimpleNamespace(_config={'enabled': True, 'tasks': tasks}, run=run)
    services = TorrentCleanup.get_service(obj)
    for service in services:
        service['func'](**(service.get('func_kwargs') or {}))
        assert 'task_id' not in service.get('kwargs', {})
    assert calls == [('0', True), ('1', True), ('2', True)]
    obj._config['enabled'] = False
    assert TorrentCleanup.get_service(obj) == []


def test_real_plugin_initializes_without_scheduling_or_downloader_writes():
    """真实宿主基类加载后，默认只展示界面，重复停止可安全执行。"""
    plugin = TorrentCleanup()
    plugin.init_plugin({})
    assert plugin.get_state()
    assert plugin.get_service() == []
    assert plugin.get_form()[1]['enabled'] is False
    assert plugin.get_render_mode() == ('vue', 'dist/assets')
    assert all(route['auth'] == 'bear' for route in plugin.get_api())
    plugin.stop_service()
    plugin.stop_service()
    assert plugin._stop.is_set()
