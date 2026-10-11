"""种子清理：多站点任务、预览执行与站点失效复核。"""

import copy
import threading
import time
import uuid

from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, HTTPException

from app.api.dependencies.auth import get_current_active_manage_user
from app.sdk.config import settings
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase
from app.sdk.scheduler import add_plugin_once_job, remove_plugin_once_job, update_plugin_job

from .engine import Engine
from .notifications import execution_notification
from .configuration import reconcile_config, save_task_in_place, reorder_tasks
from .gateway import Gateway
from .models import BindPayload, CleanupTask, RunPayload, SettingsPayload, TaskOrderPayload
from .verification import detail_url, parse_detail

_OPERATION_LOCK = threading.Lock()
_GLOBAL_FIELDS = ('enabled', 'show_sidebar', 'show_preview', 'sites', 'hr_enabled',
                  'hr_downloaders', 'hr_check_minutes', 'hr_auto_release')


class TorrentCleanup(_PluginBase):
    """独立清理插件，未启用时仍可预览但不会执行下载器写操作。"""

    plugin_name = "种子清理"
    plugin_desc = "按下载器和站点管理多套清理策略，支持预览、保种保护与失效复核。"
    plugin_icon = "delete.jpg"
    plugin_version = "0.6.5"
    plugin_author = "seiun"
    author_url = "https://github.com/zhangyxXyz/MoviePilot-Plugins"
    plugin_label = "下载管理,做种"
    plugin_config_prefix = "torrentcleanup_"
    plugin_order = 90
    auth_level = 1

    def __init__(self):
        """每个插件实例拥有独立生命周期标志和配置。"""
        super().__init__()
        self._stop = threading.Event()
        self._config = {}
        self._runtime = {}
        self._pending = set()

    def init_plugin(self, config=None):
        """验证配置后加载任务，不在初始化时触发清理。"""
        self.stop_service()
        self._stop = threading.Event()
        config = {**(config or {}), "hr_enabled": False}
        global_config = SettingsPayload.model_validate({k: config[k] for k in _GLOBAL_FIELDS if k in config})
        tasks = [CleanupTask.model_validate(t).model_dump() for t in config.get("tasks", [])]
        self._config = {**global_config.model_dump(), "tasks": tasks,
                        "revision": config.get("revision") or uuid.uuid4().hex}

    def get_state(self):
        """侧栏可独立启用，自动操作另由配置 enabled 严格控制。"""
        return bool(self._config.get("enabled") or self._config.get('show_sidebar', True))

    @staticmethod
    def get_render_mode():
        """使用与刷流保种相同的 Vue 联邦界面。"""
        return "vue", "dist/assets"

    def get_sidebar_nav(self):
        """按独立显示开关在整理区域展示工作台，不依赖自动清理是否启用。"""
        if self._config.get("show_sidebar", True):
            return [{"nav_key": "main", "title": "种子清理", "icon": "mdi-delete-outline",
                     "section": "organize", "permission": "manage", "order": 46}]
        return []

    @staticmethod
    def get_command():
        """首版不开放远程聊天删种命令。"""
        return []

    def get_form(self):
        """Vue 表单仅接收无凭据的插件配置。"""
        return [], copy.deepcopy(self._config)

    def get_page(self):
        """详情页由 Vue 工作台渲染。"""
        return []

    def get_api(self):
        """所有前端接口需要宿主认证及管理权限。"""
        routes = [("/status", self.status, "GET"), ("/settings", self.settings_update, "POST"),
                  ("/tasks/order", self.task_order, "POST"),
                  ("/tasks", self.task_save, "POST"), ("/tasks/{task_id}", self.task_delete, "DELETE"),
                  ("/tasks/{task_id}/preview", self.preview, "POST"),
                  ("/tasks/{task_id}/execute", self.execute, "POST"),
                  ("/tasks/{task_id}/reconcile", self.reconcile, "POST"),
                  ("/bindings", self.bind, "POST")]
        return [{"path": path, "endpoint": endpoint, "methods": [method], "auth": "bear",
                 "summary": endpoint.__doc__} for path, endpoint, method in routes]

    def get_service(self):
        """为每个启用任务登记独立的宿主 Cron 调度。"""
        jobs = [{"id": f"TorrentCleanup_{t['id']}", "name": f"清理：{t['name']}",
                 "trigger": CronTrigger.from_crontab(t["cron"], timezone=settings.TZ),
                 "func": self.run, "func_kwargs": {"task_id": t["id"], "scheduled": True}}
                for t in self._config.get("tasks", []) if t["enabled"] and self._config.get('enabled')]
        return jobs

    def stop_service(self):
        """停止已排队的一次性任务并要求执行器在下一个边界退出。"""
        self._stop.set()
        for job in list(self._pending):
            remove_plugin_once_job(self.__class__.__name__, job)
        self._pending.clear()

    def _settings(self):
        """读取当前全局模型，避免复用可变配置对象。"""
        return SettingsPayload.model_validate({k: self._config[k] for k in _GLOBAL_FIELDS if k in self._config})

    def _gateway(self):
        """为清理与标签托管使用一致的当前保护配置。"""
        config = self._settings()
        return Gateway(config.sites, hr_auto_release=config.hr_auto_release)

    def _task(self, task_id):
        """按稳定 ID 获取任务，不存在时返回明确的接口错误。"""
        for task in self._config.get("tasks", []):
            if task["id"] == task_id:
                return CleanupTask.model_validate(task)
        raise HTTPException(404, "清理任务不存在")

    def _save_config(self, config):
        """仅使判断依据变化的任务失效，改名和调度设置保留既有预览。"""
        config, plans, observations = reconcile_config(self._config, config,
            self.get_data("plans"), self.get_data("observations"))
        if self.update_config(config) is False:
            raise HTTPException(500, "配置保存失败")
        self._config = config
        self.save_data("observations", observations)
        self.save_data("plans", plans)
        update_plugin_job(self.__class__.__name__)

    def status(self, _: object = Depends(get_current_active_manage_user)):
        """返回任务列表、最近预览及执行历史，不返回站点认证信息。"""
        error = ""
        try:
            options = self._gateway().options()
        except Exception as err:
            options = {"sites": [], "downloaders": []}
            error = f"站点或下载器选项读取失败（{type(err).__name__}）"
        from .quota import summaries
        return {"success": True, "data": {"config": copy.deepcopy(self._config), "options": options,
                "quotas": summaries(self._settings().sites,self.get_data('site_request_rates') or {}),
                "plans": self.get_data("plans") or {}, "runs": self.get_data("runs") or [],
                "runtime": copy.deepcopy(self._runtime), "error": error,
                "bindings": [{"site_id": b["site_id"], "hash": b["hash"], "url": b["url"],
                              "source": b.get("source", "手动链接"), "verified_at": b["verified_at"],
                              "hash_verified_at": b.get('hash_verified_at', b['verified_at'])}
                             for b in (self.get_data("bindings") or {}).values()]}}

    def settings_update(self, payload: SettingsPayload, _: object = Depends(get_current_active_manage_user)):
        """保存全局开关和站点规则；操作执行中拒绝改变判断依据。"""
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise HTTPException(409, "任务正在检查或执行，请稍后保存")
        try:
            self._save_config({**self._config, **payload.model_dump()})
            return {"success": True}
        finally:
            _OPERATION_LOCK.release()

    def task_save(self, payload: CleanupTask, _: object = Depends(get_current_active_manage_user)):
        """创建或修改清理任务，保留稳定 ID 并校验配置引用。"""
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise HTTPException(409, "任务正在检查或执行，请稍后保存")
        try:
            gateway = self._gateway()
            if payload.downloader not in gateway.helper.get_configs():
                raise HTTPException(422, "所选下载器不存在或未启用")
            if payload.mode == 'download_error' and gateway.helper.get_configs()[payload.downloader].type != 'qbittorrent':
                raise HTTPException(422, '下载错误策略目前适用于 qBittorrent 下载器')
            if not set(payload.site_ids).issubset(gateway.sites):
                raise HTTPException(422, "所选站点不存在")
            if payload.id:
                self._task(payload.id)
            else:
                payload.id = uuid.uuid4().hex
            try:
                tasks = save_task_in_place(self._config['tasks'], payload.model_dump())
            except ValueError as err:
                raise HTTPException(422, str(err)) from err
            self._save_config({**self._config, "tasks": tasks})
            return {"success": True, "data": payload.model_dump()}
        finally:
            _OPERATION_LOCK.release()

    def task_order(self, payload: TaskOrderPayload, _: object = Depends(get_current_active_manage_user)):
        """持久化左侧任务顺序，保留预览和观察记录。"""
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise HTTPException(409, '任务正在检查或执行，请稍后排序')
        try:
            try:
                tasks = reorder_tasks(self._config['tasks'], payload.ids)
            except ValueError as err:
                raise HTTPException(409, str(err)) from err
            self._save_config({**self._config, 'tasks': tasks})
            return {'success': True}
        finally:
            _OPERATION_LOCK.release()

    def task_delete(self, task_id: str, _: object = Depends(get_current_active_manage_user)):
        """仅删除策略配置，保留执行历史和下载器中的种子。"""
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise HTTPException(409, "任务正在运行")
        try:
            self._task(task_id)
            self._save_config({**self._config, "tasks": [t for t in self._config["tasks"] if t["id"] != task_id]})
            return {"success": True}
        finally:
            _OPERATION_LOCK.release()

    def _queue(self, task_id, plan_id=None):
        """使用宿主一次性任务异步执行，防止 HTTP 超时导致重复提交。"""
        self._task(task_id)
        job = f"cleanup_{task_id}"
        if job in self._pending:
            raise HTTPException(409, "该任务已经排队或正在执行")
        self._pending.add(job)
        self._runtime[task_id] = {"status": "queued", "message": "等待检查", "queued_at": time.time()}
        accepted = add_plugin_once_job(self.__class__.__name__, job, self.run, "清理任务检查",
            delay_seconds=1, func_kwargs={"task_id": task_id, "plan_id": plan_id})
        if not accepted:
            self._pending.discard(job)
            self._runtime[task_id] = {"status": "failed", "message": "宿主调度不可用"}
            raise HTTPException(503, "宿主调度不可用")
        return {"success": True, "message": "任务已排队"}

    def preview(self, task_id: str, _: object = Depends(get_current_active_manage_user)):
        """排队生成候选预览，不修改下载器任务。"""
        return self._queue(task_id)

    def execute(self, task_id: str, payload: RunPayload, _: object = Depends(get_current_active_manage_user)):
        """使用用户检查过的预览执行已配置动作，执行器仍重新核实。"""
        task = self._task(task_id)
        if not self._config.get('enabled', False) or not task.enabled:
            raise HTTPException(409, "请先启用插件及该任务")
        return self._queue(task_id, payload.plan_id)

    def reconcile(self, task_id: str, _: object = Depends(get_current_active_manage_user)):
        """只读核对待确认操作，绝不重发删除、暂停或标签命令。"""
        self._task(task_id)
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise HTTPException(409, "任务正在运行，请稍后核对")
        try:
            engine = Engine(self._gateway(), self.get_data, self.save_data, self._stop.is_set)
            return {"success": True, "data": engine.reconcile(task_id)}
        finally:
            _OPERATION_LOCK.release()

    def bind(self, payload: BindPayload, _: object = Depends(get_current_active_manage_user)):
        """链接可留空自动查找；详情页 Hash 核验成功后才保存关联。"""
        if not _OPERATION_LOCK.acquire(blocking=False):
            raise HTTPException(409, "任务正在运行，请稍后关联详情页")
        try:
            gateway = self._gateway()
            rows = gateway.snapshot(payload.downloader)
            row = next((r for r in rows if r.torrent.hash == payload.hash.lower()), None)
            if not row or payload.site_id not in row.torrent.site_ids:
                raise HTTPException(422, "任务不存在或不属于所选站点")
            gateway.configure_verification(self.get_data, self.save_data, self._stop.is_set)
            try:
                result = gateway.verifier.check(row, payload.site_id, supplied_url=payload.url)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            if result.site_result != "active":
                raise HTTPException(422, result.reason)
            return {"success": True, "message": "详情页 Hash 已核实并保存关联",
                    "data": {"url": result.detail_url, "source": result.source, "cached": result.cached}}
        finally:
            _OPERATION_LOCK.release()

    def _report(self, report):
        """边执行边持久化报告，仅保留最近一百次执行。"""
        reports = self.get_data("runs") or []
        reports = [r for r in reports if r["id"] != report["id"]]
        self.save_data("runs", [copy.deepcopy(report)] + reports[:99])

    def run(self, task_id: str, plan_id=None, scheduled=False):
        """调度入口串行处理策略，互斥锁覆盖复核及下载器写操作。"""
        lifecycle = self._stop
        self._runtime[task_id] = {"status": "queued", "message": "等待其他清理任务完成"}
        while not _OPERATION_LOCK.acquire(timeout=1):
            if lifecycle.is_set():
                self._pending.discard(f"cleanup_{task_id}")
                return
        try:
            started_at = time.time()
            self._runtime[task_id] = {"status": "running", "message": "正在读取下载器任务",
                                     "started_at": started_at, "updated_at": started_at,
                                     "completed": 0, "total": None, "phase": "download"}
            def preview_progress(event):
                """发布本轮逐项进度，不把尚未完成的结果作为可执行计划。"""
                self._runtime[task_id] = {**self._runtime[task_id], **event, "updated_at": time.time()}
            task = self._task(task_id)
            if lifecycle.is_set():
                self._runtime[task_id] = {"status": "stopped", "message": "插件已停止"}
                return
            if (scheduled or plan_id) and (not self._config.get('enabled', False) or not task.enabled):
                raise ValueError("插件或任务已停用")
            engine = Engine(self._gateway(), self.get_data, self.save_data, lifecycle.is_set)
            plans = self.get_data("plans") or {}
            if plan_id:
                plan = plans.get(task_id)
                if not plan or plan["id"] != plan_id:
                    raise ValueError("预览已更新，请重新检查")
            else:
                plan = engine.preview(task, self._config["revision"], progress=preview_progress)
                plans[task_id] = plan
                self.save_data("plans", plans)
            if scheduled and task.action == 'preview':
                self._report({'id': uuid.uuid4().hex, 'task_id': task.id, 'task_name': task.name,
                              'action': task.action, 'started_at': started_at, 'finished_at': time.time(),
                              'status': 'finished', 'summary': f"检查 {len(plan['items'])} 项，候选 {plan['candidate_count']} 项（仅检查）", 'items': []})
            if plan_id or scheduled and task.action != "preview":
                report = engine.execute(task, plan, self._config["revision"], progress=self._report)
                # 消费过的计划不能被重复手动执行。
                plans.pop(task_id, None)
                self.save_data("plans", plans)
                if task.notify:
                    title, text = execution_notification(task, report)
                    self.post_message(title=title, text=text)
            self._runtime[task_id] = {**self._runtime[task_id], "status": "idle", "message": "检查完成",
                                     "finished_at": time.time(), "time": time.time()}
        except Exception as err:
            # 不输出异常原文，避免底层 HTTP 异常包含 Tracker passkey。
            message = str(err) if isinstance(err, ValueError) else f"检查失败（{type(err).__name__}），请核对连接和配置"
            self._runtime[task_id] = {**self._runtime.get(task_id, {}), "status": "failed", "message": message,
                                     "finished_at": time.time(), "time": time.time()}
            logger.error(f"种子清理任务 {task_id}：{message}")
            if scheduled:
                self._report({'id': uuid.uuid4().hex, 'task_id': task_id, 'task_name': task_id,
                              'action': 'preview', 'started_at': started_at, 'finished_at': time.time(),
                              'status': 'failed', 'summary': message, 'items': []})
        finally:
            self._pending.discard(f"cleanup_{task_id}")
            _OPERATION_LOCK.release()
