"""清理策略的边界测试；全部使用内存快照，不接触实际下载器。"""

from dataclasses import replace

import pytest
from app.plugins.torrentcleanup import policy


def sample():
    """生成满足默认保种策略的独立任务。"""
    return policy.Torrent("TR", "abc", "测试影片", frozenset({3}), True,
                          True, True, 168 * 3600, 2, 1024, hr=False,
                          files=frozenset({"volume:1:file:2"}), files_verified=True)


def rule(**kwargs):
    """构造具有显式下载器和站点范围的策略。"""
    return policy.Rule("rule1", "TR", frozenset({3}), **kwargs)


@pytest.mark.parametrize("change", [
    {"seed_seconds": None}, {"seed_seconds": 167 * 3600}, {"hr": None},
    {"hr": True}, {"completed": False}, {"healthy": False},
    {"sites_complete": False}, {"site_ids": frozenset({3, 4})},
    {"tags": frozenset({"H&R"})}, {"ratio": float("nan")},
])
def test_protection_rejects_incomplete_evidence(change):
    """保护信息缺失或不满足时不得进入候选列表。"""
    assert not policy.evaluate(rule(), replace(sample(), **change)).eligible


def test_valid_retention():
    """真正累计达到保种阈值的任务可以进入预览。"""
    result = policy.evaluate(rule(), sample())
    assert result.eligible and result.action == "preview"


def test_temporary_failure_never_confirms_dead_torrent():
    """站点存在、超时和登录失效均重置观察。"""
    previous = policy.Observation(0, 100, 3)
    for tracker, site, auth in [("timeout", "missing", True),
                                 ("missing", "active", True),
                                 ("missing", "missing", False)]:
        evidence = policy.Evidence("TR", "abc", 3, 200, tracker, site, auth, True)
        assert policy.observe(previous, evidence, 6) is None


def test_clicking_repeatedly_does_not_complete_observation():
    """密集重复运行不会缩短观察期。"""
    item = policy.Evidence("TR", "abc", 3, 100, "missing", "missing", True, True)
    first = policy.observe(None, item, 6)
    assert policy.observe(first, replace(item, checked_at=101), 6) == first


def test_dead_torrent_requires_bound_hash_and_fresh_evidence():
    """完成观察后仍然需要当前同一 Hash 的站点证据。"""
    now = 48 * 3600
    evidence = policy.Evidence("TR", "abc", 3, now, "missing", "missing", True, True)
    records = {3: policy.Observation(0, now, 3)}
    assert policy.evaluate(rule(mode="invalid"), sample(), {3: evidence}, records, now).eligible
    for changed in [replace(evidence, hash="other"), replace(evidence, binding_verified=False),
                    replace(evidence, checked_at=now - 301)]:
        assert not policy.evaluate(rule(mode="invalid"), sample(), {3: changed}, records, now).eligible


def test_shared_file_other_downloader_blocks_data_deletion():
    """同一数据仍被其他下载器使用时，不能删除文件。"""
    torrent = sample()
    other = replace(torrent, downloader="QB", hash="def")
    assert policy.file_deletion_guard(torrent, [torrent, other], True)
    assert policy.file_deletion_guard(torrent, [torrent], False)
    assert policy.file_deletion_guard(torrent, [torrent], True) is None


def test_limits_and_cross_rule_deduplication():
    """数量限制和跨规则去重只保留一份任务。"""
    torrent = sample()
    second = replace(torrent, hash="def")
    rows = [(torrent, policy.evaluate(rule(), torrent)), (second, policy.evaluate(rule(), second))]
    result = policy.build_plan(rule(max_count=1), rows)
    assert sum(item.eligible for _, item in result) == 1
    assert not policy.build_plan(rule(), rows, {("TR", "abc")})[0][1].eligible


@pytest.mark.parametrize("kwargs", [{"min_seed_hours": float("nan")}, {"max_count": 0},
                                    {"mode": "invalid", "min_observations": 1}])
def test_invalid_rules_rejected(kwargs):
    """无效配置不能退化成全量清理。"""
    with pytest.raises(ValueError):
        rule(**kwargs).validate()
