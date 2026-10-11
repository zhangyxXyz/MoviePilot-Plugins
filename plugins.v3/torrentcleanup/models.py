"""清理任务及站点规则的配置合同。"""

import re
from typing import Literal
from urllib.parse import urlsplit

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .policy import Rule


class SitePolicy(BaseModel):
    """站点身份映射及保种要求；未知要求不允许执行清理。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    site_id: int = Field(gt=0)
    tracker_hosts: list[str] = Field(default_factory=list, max_length=20)
    hr_mode: Literal["unknown", "none", "minimum", "managed"] = "unknown"
    hr_detection: Literal['marked', 'all'] = 'marked'
    hr_required_hours: float = Field(default=48, ge=1, le=8760)
    hr_buffer_hours: float = Field(default=24, ge=1, le=8760)
    hr_deadline_days: int = Field(default=14, ge=1, le=365)
    hr_seed_ratio: float = Field(default=0, ge=0, le=1000000)
    hr_ratio_basis: Literal['client', 'full_size'] = 'client'
    min_seed_hours: float = Field(default=168, ge=0, le=876000)
    verification: Literal["unsupported", "nexusphp", "mteam"] = "unsupported"
    api_url: str = Field(default='', max_length=2048)
    auto_discover: bool = True
    search_url_template: str = Field(default="", max_length=2048)
    detail_path: str = Field(default="/details.php", max_length=200)
    detail_id_param: str = Field(default="id", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    check_interval_hours: float = Field(default=6, ge=1, le=168)
    request_gap_seconds: float = Field(default=2, ge=1, le=30)
    max_requests_per_hour: int = Field(default=20, ge=1, le=120)
    max_detail_candidates: int = Field(default=5, ge=1, le=10)
    detail_requests_per_hour: int = Field(default=100, ge=0, le=10000)
    search_requests_per_day: int = Field(default=1000, ge=0, le=100000)
    torrent_downloads_per_hour: int = Field(default=100, ge=0, le=10000)
    torrent_downloads_per_day: int = Field(default=1000, ge=0, le=100000)
    allow_torrent_verification: bool = False
    error_cache_minutes: float = Field(default=10, ge=1, le=1440)
    torrent_download_path: str = Field(default='/download.php?id={id}', max_length=2048)
    torrent_download_hosts: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_site_paths(self):
        """搜索模板限定站内相对路径，替换值只允许进入查询参数。"""
        import re
        from string import Formatter
        path = self.torrent_download_path
        if path:
            target = urlsplit(path)
            if (not path.startswith('/') or path.startswith('//') or target.netloc or target.scheme
                    or target.fragment or '\\' in path or '..' in target.path.split('/') or '{' in target.path):
                raise ValueError('种子下载模板必须为本站相对路径，占位符仅允许在查询参数中')
            if any(field is not None and (field != 'id' or fmt or conv) for _, field, fmt, conv in Formatter().parse(path)):
                raise ValueError('种子下载模板只支持 {id}')
        if self.api_url:
            parsed_api = urlsplit(self.api_url)
            if (parsed_api.scheme != 'https' or not parsed_api.hostname or parsed_api.username or parsed_api.password
                    or parsed_api.query or parsed_api.fragment or parsed_api.path.rstrip('/') not in ['', '/api']):
                raise ValueError('API 地址必须为 HTTPS 站点根地址或 /api 路径，不填写凭据')
        for value in [self.detail_path, self.search_url_template]:
            if not value:
                continue
            parsed = urlsplit(value)
            if (not value.startswith("/") or value.startswith("//") or parsed.netloc
                    or parsed.scheme or ".." in parsed.path.split("/") or "\\" in value):
                raise ValueError("请填写本站相对路径，例如 /torrents.php?search={hash}")
        if "?" in self.detail_path or "#" in self.detail_path or "{" in self.detail_path:
            raise ValueError("详情路径不能包含参数或占位符")
        if self.search_url_template:
            parsed = urlsplit(self.search_url_template)
            if "{" in parsed.path or parsed.fragment:
                raise ValueError("搜索占位符只能放在查询参数内")
            fields = list(Formatter().parse(self.search_url_template))
            if any(field is not None and (field not in {"hash", "name", "keyword"} or fmt or conv)
                   for _, field, fmt, conv in fields):
                raise ValueError("搜索模板只支持 {hash}、{name}、{keyword}")
            if not re.search(r"\{(?:hash|name|keyword)\}", parsed.query):
                raise ValueError("搜索模板至少需要一个搜索占位符")
        return self

    @field_validator("tracker_hosts", "torrent_download_hosts")
    @classmethod
    def validate_hosts(cls, values: list[str]) -> list[str]:
        """只接受精确主机名，不接受通配符、路径或认证信息。"""
        result = []
        for value in values:
            value = value.strip().lower().rstrip(".")
            if not value or any(c in value for c in "/:@* ?#") or "." not in value:
                raise ValueError("Tracker 域名仅填写主机名，例如 tracker.carpt.net")
            result.append(value)
        return sorted(set(result))


class CleanupTask(BaseModel):
    """独立任务配置；只有明确启用后才参与定时处理。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = ""
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = False
    downloader: str = Field(min_length=1, max_length=100)
    site_ids: list[int] = Field(default_factory=list, max_length=100)
    mode: Literal["retention", "invalid", "download_error", "conditions"] = "retention"
    states: list[str] = Field(default_factory=list, max_length=40)
    error_keywords: list[str] = Field(default_factory=list, max_length=30)
    completion: Literal['all', 'completed', 'incomplete'] = 'completed'
    require_healthy: bool = True
    filter_seed: bool = True
    filter_ratio: bool = True
    verify_invalid: bool = False
    observe_state: bool = False
    require_idle_download: bool = False
    filter_age: bool = False
    filter_progress: bool = False
    all_sites: bool = False
    categories: list[str] = Field(default_factory=list, max_length=100)
    only_moviepilot: bool = False
    max_upload_speed_kib: float | None = Field(default=None, ge=0)
    error_states: list[Literal["error", "missingFiles", "stalledDL", "metaDL", "forcedMetaDL"]] = Field(default_factory=lambda: ["error", "missingFiles"], min_length=1)
    error_min_age_hours: float = Field(default=24, ge=0, le=8760)
    error_observation_hours: float = Field(default=6, ge=0.25, le=8760)
    error_min_observations: int = Field(default=3, ge=2, le=100)
    error_gap_minutes: float = Field(default=15, ge=1, le=1440)
    error_max_progress: float = Field(default=99.9, ge=0, lt=100)
    error_protect_unknown_hr: bool = True
    hr_local_conditions: bool = False
    hr_tags: list[str] = Field(default_factory=list, max_length=100)
    hr_seed_hours: float = Field(default=168, ge=0, le=876000)
    hr_upload_gb: float = Field(default=0, ge=0, le=100000000)
    action: Literal["preview", "mark", "pause", "delete", "deletefile"] = "preview"
    cron: str = "0 */6 * * *"
    min_seed_hours: float = Field(default=168, ge=0, le=876000)
    min_ratio: float = Field(default=0, ge=0, le=1000000)
    min_size_gb: float = Field(default=0, ge=0)
    max_size_gb: float | None = Field(default=None, gt=0)
    path_prefix: str = Field(default="", max_length=1024)
    include_tags: list[str] = Field(default_factory=list, max_length=100)
    protect_tags: list[str] = Field(default_factory=lambda: ["H&R", "保留", "勿删"], max_length=100)
    observation_hours: float = Field(default=48, ge=1, le=8760)
    min_observations: int = Field(default=3, ge=2, le=100)
    observation_gap_hours: float = Field(default=6, ge=1, le=720)
    max_count: int = Field(default=10, ge=1, le=100)
    max_gb: float = Field(default=100, gt=0, le=100000)
    notify: bool = False

    @field_validator('hr_tags', 'protect_tags', 'include_tags', mode='before')
    @classmethod
    def normalize_tags(cls, values):
        values = values if isinstance(values, list) else [values]
        return sorted({tag.strip() for value in values for tag in re.split(r'[,，\n]', str(value or '')) if tag.strip()})

    @field_validator("cron")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        """保存前验证五段 Cron，防止加载时破坏宿主调度。"""
        CronTrigger.from_crontab(value)
        return value

    @model_validator(mode="after")
    def validate_ranges(self):
        """拒绝空标签、无效站点和互相矛盾的体积范围。"""
        self.site_ids = sorted(set(self.site_ids))
        if self.all_sites and self.mode not in {'download_error', 'conditions'}:
            raise ValueError('当前条件不允许不限站点')
        if self.verify_invalid and self.all_sites:
            raise ValueError('站点失效复核需要明确选择作用站点')
        allowed = {'downloading','stalleddl','uploading','stalledup','error','pauseddl','pausedup','stoppeddl','stoppedup','missingfiles','checkingdl','checkingup','checkingresumedata','forceddl','queueddl','forcedup','queuedup','allocating','metadl','forcedmetadl','moving','unknown','0','1','2','3','4','5','6'}
        self.states = sorted(set(v.strip() for v in self.states if v.strip()))
        if any(v.lower() not in allowed for v in self.states):
            raise ValueError('任务状态不在下载器状态字典内')
        self.error_keywords = sorted(set(v.strip() for v in self.error_keywords if v.strip()))
        if any(len(v) > 200 for v in self.error_keywords):
            raise ValueError('错误关键词最长 200 字符')
        if self.observe_state and not self.states and not self.error_keywords:
            raise ValueError('持续观察需要至少选择一个状态或错误关键词')
        if not self.site_ids and not self.all_sites:
            raise ValueError('请选择站点，或在下载错误策略中明确选择不限站点')
        self.categories = sorted(set(v.strip() for v in self.categories if v.strip()))
        self.error_states = sorted(set(self.error_states))
        if any(v <= 0 for v in self.site_ids):
            raise ValueError("站点 ID 无效")
        if self.max_size_gb is not None and self.max_size_gb < self.min_size_gb:
            raise ValueError("体积上限不能小于下限")
        self.include_tags = sorted({v.strip() for v in self.include_tags if v.strip()})
        self.protect_tags = sorted({v.strip() for v in self.protect_tags if v.strip()})
        if self.hr_local_conditions and (self.mode != 'conditions' or not (self.hr_seed_hours > 0 or self.hr_upload_gb > 0)):
            raise ValueError('本地 H&R 条件仅用于按勾选条件筛选，且至少启用时间或上传量一项')
        if self.path_prefix and (not self.path_prefix.startswith("/") or ".." in self.path_prefix.split("/")):
            raise ValueError("目录范围必须为下载器中的绝对路径")
        return self

    def rule(self) -> Rule:
        """将接口配置转换为无依赖策略模型。"""
        return Rule(self.id, self.downloader, frozenset(self.site_ids), self.mode,
                    self.action, self.min_seed_hours, self.min_ratio,
                    frozenset(self.include_tags), frozenset(self.protect_tags),
                    self.observation_hours, self.min_observations,
                    self.observation_gap_hours, self.max_count, int(self.max_gb * 1024**3), self.all_sites)


class SettingsPayload(BaseModel):
    """全局运行开关与站点级规则。"""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    show_sidebar: bool = True
    show_preview: bool = True
    hr_enabled: bool = False
    hr_downloaders: list[str] = Field(default_factory=list, max_length=30)
    hr_check_minutes: int = Field(default=15, ge=5, le=120)
    hr_auto_release: bool = False
    sites: list[SitePolicy] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_unique(self):
        """拒绝重复站点和可能使任务归属不确定的域名映射。"""
        ids = [v.site_id for v in self.sites]
        hosts = [h for v in self.sites for h in v.tracker_hosts]
        if len(ids) != len(set(ids)) or len(hosts) != len(set(hosts)):
            raise ValueError("站点和 Tracker 域名不能重复配置")
        return self


class RunPayload(BaseModel):
    """手动执行必须引用尚未过期的预览。"""

    plan_id: str = Field(min_length=1, max_length=64)


class TaskOrderPayload(BaseModel):
    """提交全部任务的稳定 ID 顺序，服务端校验并发变化。"""

    model_config = ConfigDict(extra='forbid')
    ids: list[str] = Field(max_length=100)


class HRGuardPayload(BaseModel):
    """供刷流在执行删除前查询实时保护结果。"""
    downloader: str
    hashes: list[str] = Field(max_length=10000)


class HRResolvePayload(BaseModel):
    """用户明确确认单个托管任务的 H&R 属性。"""
    downloader: str
    hash: str = Field(pattern=r'^[0-9a-fA-F]{40}$')
    is_hr: bool


class BindPayload(BaseModel):
    """显式建立站点详情与种子 Hash 的可验证关联。"""

    downloader: str
    hash: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    site_id: int = Field(gt=0)
    url: str = Field(default="", max_length=2048)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """拒绝非 HTTP 地址和 URL 中嵌入的认证信息。"""
        parsed = urlsplit(value)
        if not value.strip():
            return ""
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError("请输入站点详情页地址")
        return value
