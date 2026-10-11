"""接口配置拒绝含糊范围和非法限额。"""
import pytest
from pydantic import ValidationError
from app.plugins.torrentcleanup.models import CleanupTask, SettingsPayload, SitePolicy


@pytest.mark.parametrize('template', ['https://evil.test/?q={hash}', '//evil.test/?q={hash}',
    '/{hash}?a=1', '/search?q={unknown}', '/search?q={hash.__class__}', '/search?q={hash!r}', '/search?q=fixed'])
def test_search_template_rejects_unsafe_or_unsupported_fields(template):
    """模板不能改变请求站点，不能读取 Python 属性或遗漏搜索变量。"""
    with pytest.raises(ValidationError):
        SitePolicy(site_id=3, search_url_template=template)


@pytest.mark.parametrize('patch', [{'site_ids': []}, {'site_ids': [-1]}, {'cron': 'invalid'},
    {'min_seed_hours': float('nan')}, {'max_count': 0}, {'max_gb': 0},
    {'path_prefix': '/media/../other'}, {'min_size_gb': 10, 'max_size_gb': 5}])
def test_invalid_task(patch):
    """无效条件在保存入口就被拒绝。"""
    with pytest.raises(ValidationError):
        CleanupTask.model_validate(dict(name='测试', downloader='QB', site_ids=[3]) | patch)


def test_hr_tag_cannot_be_removed():
    """API 与界面都不能绕过内置保护标签。"""
    task = CleanupTask(name='测试', downloader='QB', site_ids=[3], protect_tags=[])
    assert 'H&R' in task.protect_tags


def test_ambiguous_tracker_mapping_rejected():
    """一个 Tracker 域名不能属于两个站点。"""
    with pytest.raises(ValidationError):
        SettingsPayload(sites=[{'site_id': i, 'tracker_hosts': ['t.test']} for i in [1, 2]])
