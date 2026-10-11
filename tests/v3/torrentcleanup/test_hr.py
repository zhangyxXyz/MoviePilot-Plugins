"""删除插件仅消费助手标签配置，不维护 H&R 状态。"""
import sys
from types import SimpleNamespace
from app.plugins.torrentcleanup.hr_labels import protection_tags

def test_default_labels(monkeypatch):
    monkeypatch.setitem(sys.modules,'app.sdk.plugins',SimpleNamespace(PluginManager=lambda:SimpleNamespace(get_plugin_config=lambda _:{})))
    assert protection_tags()=={'H&R','H&R待核验'}

def test_custom_and_renamed_labels_remain_protected_when_helper_off(monkeypatch):
    cfg={'enabled':False,'confirmed_tag':'我的HR','pending_tag':'待确认','tag_aliases':['H&R','H&R待核验']}
    monkeypatch.setitem(sys.modules,'app.sdk.plugins',SimpleNamespace(PluginManager=lambda:SimpleNamespace(get_plugin_config=lambda _:cfg)))
    assert protection_tags()=={'我的HR','待确认','H&R','H&R待核验'}

def test_cleanup_has_no_hr_write_routes_or_events():
    from app.plugins.torrentcleanup import TorrentCleanup
    assert not hasattr(TorrentCleanup,'hr_run')
    assert not hasattr(TorrentCleanup,'hr_download_added')
