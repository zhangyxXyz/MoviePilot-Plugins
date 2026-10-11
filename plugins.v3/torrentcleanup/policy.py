"""清理策略的纯计算内核，不访问下载器、站点或持久化配置。"""

from dataclasses import dataclass, replace
from math import isfinite


@dataclass(frozen=True)
class Rule:
    """单个下载器上的独立清理策略，站点范围必须显式指定。"""

    id: str
    downloader: str
    site_ids: frozenset[int]
    mode: str = "retention"
    action: str = "preview"
    min_seed_hours: float = 168
    min_ratio: float = 0
    include_tags: frozenset[str] = frozenset()
    protect_tags: frozenset[str] = frozenset({"H&R", "保留", "勿删"})
    observation_hours: float = 48
    min_observations: int = 3
    observation_gap_hours: float = 6
    max_count: int = 10
    max_bytes: int = 100 * 1024**3
    all_sites: bool = False

    def validate(self) -> None:
        """拒绝空范围、非有限阈值以及没有间隔的失效观察策略。"""
        if not self.id or not self.downloader or not self.site_ids and not (self.mode in {'download_error', 'conditions'} and self.all_sites):
            raise ValueError("任务、下载器和站点范围不能为空")
        if self.mode not in {"retention", "invalid", "download_error", "conditions"}:
            raise ValueError("不支持的清理策略")
        if self.action not in {"preview", "mark", "pause", "delete", "deletefile"}:
            raise ValueError("不支持的处理动作")
        thresholds = (self.min_seed_hours, self.min_ratio, self.observation_hours,
                      self.observation_gap_hours, self.max_count, self.max_bytes)
        if any(not isfinite(v) or v < 0 for v in thresholds):
            raise ValueError("阈值必须为非负有限数")
        if self.max_count < 1 or self.max_bytes < 1:
            raise ValueError("每次处理数量和体积上限必须大于零")
        if self.mode == "invalid" and (
            self.observation_hours < 1 or self.min_observations < 2
            or self.observation_gap_hours < 1
        ):
            raise ValueError("失效策略必须经过多次间隔观察")


@dataclass(frozen=True)
class Torrent:
    """从下载器归一的任务快照，未知保护信息使用 None 表示。"""

    downloader: str
    hash: str
    name: str
    site_ids: frozenset[int]
    sites_complete: bool
    completed: bool
    healthy: bool
    seed_seconds: float | None
    ratio: float | None
    size: int
    tags: frozenset[str] = frozenset()
    hr: bool | None = None
    files: frozenset[str] = frozenset()
    files_verified: bool = False


@dataclass(frozen=True)
class Evidence:
    """指定站点、种子和下载器的一次复核结果，不保存 Cookie 或 passkey。"""

    downloader: str
    hash: str
    site_id: int
    checked_at: float
    tracker_result: str
    site_result: str
    authenticated: bool
    binding_verified: bool
    reason: str = ""
    detail_url: str = ""
    source: str = ""
    cached: bool = False
    site_skipped: bool = False

    @property
    def confirmed_missing(self) -> bool:
        """仅接受已核实身份且登录有效的双重不存在证据。"""
        return (self.tracker_result == "missing" and self.site_result == "missing"
                and self.authenticated and self.binding_verified)


@dataclass(frozen=True)
class Observation:
    """跨周期保存的失效观察，不把短时间重复点击视作多次证据。"""

    first: float
    last: float
    count: int


def observe(previous: Observation | None, evidence: Evidence,
            gap_hours: float) -> Observation | None:
    """非确定性结果清空连续观察，时间倒退或间隔不足不增加次数。"""
    if not evidence.confirmed_missing:
        return None
    if previous is None or evidence.checked_at < previous.last:
        return Observation(evidence.checked_at, evidence.checked_at, 1)
    if evidence.checked_at - previous.last < gap_hours * 3600:
        return previous
    return Observation(previous.first, evidence.checked_at, previous.count + 1)


@dataclass(frozen=True)
class Decision:
    """可向任意界面展示的策略结果及其依据。"""

    eligible: bool
    reasons: tuple[str, ...]
    action: str = "preview"


def evaluate(rule: Rule, torrent: Torrent,
             evidence: dict[int, Evidence] | None = None,
             observations: dict[int, Observation] | None = None,
             now: float = 0) -> Decision:
    """按范围、保种保护、策略证据依次筛选，不触发任何处理动作。"""
    rule.validate()
    reasons = []
    if torrent.downloader != rule.downloader:
        reasons.append("不在选定下载器内")
    if not torrent.sites_complete or not torrent.site_ids:
        reasons.append("Tracker 与站点映射不完整")
    elif not torrent.site_ids.issubset(rule.site_ids):
        reasons.append("任务还关联未选中的站点")
    if not rule.include_tags.issubset(torrent.tags):
        reasons.append("缺少必需标签")
    if rule.protect_tags.intersection(torrent.tags):
        reasons.append("命中保护标签")
    if torrent.hr is not False:
        reasons.append("H&R 未满足或无法确认")
    if not torrent.completed or not torrent.healthy:
        reasons.append("下载未完成、校验中或本地状态异常")
    if torrent.seed_seconds is None or not isfinite(torrent.seed_seconds):
        reasons.append("缺少可信累计做种时间")
    elif torrent.seed_seconds < rule.min_seed_hours * 3600:
        reasons.append("累计做种时间不足")
    if torrent.ratio is None or not isfinite(torrent.ratio) or torrent.ratio < rule.min_ratio:
        reasons.append("分享率不足或未知")
    if torrent.size <= 0:
        reasons.append("任务体积未知")
    if rule.mode == "invalid":
        for site_id in sorted(torrent.site_ids):
            item = (evidence or {}).get(site_id)
            record = (observations or {}).get(site_id)
            if item and item.tracker_result == 'active':
                reasons.append(f'站点 {site_id} Tracker 正常，保留')
                continue
            if item and item.tracker_result == 'unknown':
                reasons.append(f'站点 {site_id} Tracker 状态不确定，保留')
                continue
            if (not item or item.downloader != torrent.downloader or item.hash != torrent.hash
                    or item.site_id != site_id or not item.confirmed_missing
                    or not 0 <= now - item.checked_at <= 300):
                reasons.append(f"站点 {site_id} 缺少当前有效的失效复核")
            elif (not record or record.count < rule.min_observations
                  or not 0 <= item.checked_at - record.last < rule.observation_gap_hours * 3600
                  or record.last - record.first < rule.observation_hours * 3600):
                reasons.append(f"站点 {site_id} 尚未完成观察期")
    if reasons:
        return Decision(False, tuple(reasons), rule.action)
    return Decision(True, ("满足全部筛选及保护条件",), rule.action)


def file_deletion_guard(candidate: Torrent, inventory: list[Torrent],
                        inventory_complete: bool) -> str | None:
    """共享文件未查清或仍被其他下载器任务引用时禁止删除数据。"""
    if not inventory_complete or not candidate.files_verified or not candidate.files:
        return "无法完整核实文件及全部下载器的引用关系"
    for other in inventory:
        if (other.downloader, other.hash) == (candidate.downloader, candidate.hash):
            continue
        if not other.files_verified or not other.files:
            return "其他任务的文件引用未知"
        if candidate.files.intersection(other.files):
            return "文件仍被其他任务使用"
    return None


def build_plan(rule: Rule, rows: list[tuple[Torrent, Decision]],
               already_selected: set[tuple[str, str]] | None = None) -> list[tuple[Torrent, Decision]]:
    """对已评估候选限量、按任务身份去重，保持原有不命中原因。"""
    rule.validate()
    selected = set(already_selected or ())
    count = total = 0
    result = []
    for torrent, decision in sorted(rows, key=lambda row: (row[0].downloader, row[0].hash)):
        identity = (torrent.downloader, torrent.hash)
        if decision.eligible:
            reason = None
            if identity in selected:
                reason = "其他策略已经选中该任务"
            elif count >= rule.max_count or total + torrent.size > rule.max_bytes:
                reason = "达到本轮数量或体积上限"
            if reason:
                decision = replace(decision, eligible=False, reasons=(reason,))
            else:
                selected.add(identity)
                count += 1
                total += torrent.size
        result.append((torrent, decision))
    return result
