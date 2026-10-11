"""自动订阅：热门媒体、豆瓣榜单、演员作品和将映关注。"""
import copy
import threading
import time
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, HTTPException
from app.api.dependencies.auth import get_current_active_manage_user
from app.sdk.plugin import _PluginBase, PluginManager
from app.sdk.config import settings
from app.sdk.logging import logger
from app.sdk.scheduler import add_plugin_once_job, remove_plugin_once_job, update_plugin_job
from .models import Task, Settings, TaskOrder, HistoryAction, SOURCES, RANKS, save_task, reorder
from .engine import Engine
from .gateway import Gateway
from .migration import LEGACY, migrate

_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()

class AutoSubscribe(_PluginBase):
    plugin_name = '自动订阅'
    plugin_desc = '按多套任务管理热门媒体、豆瓣榜单、演员作品和将映关注，统一去重与分类历史。'
    plugin_icon = 'movie.jpg'
    plugin_version = '1.0.0'
    plugin_author = 'seiun'
    author_url = 'https://github.com/zhangyxXyz/MoviePilot-Plugins'
    plugin_config_prefix = 'autosubscribe_'
    plugin_order = 91
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._config, self._runtime = {}, {}
        self._stop = threading.Event()
        self._pending = set()

    def init_plugin(self, config=None):
        self.stop_service()
        self._stop = threading.Event()
        config = config or {}
        self._config = {**Settings.model_validate(config).model_dump(),
                        'tasks': [Task.model_validate(t).model_dump() for t in config.get('tasks', [])]}

    def get_state(self):
        return self._config.get('enabled') or self._config.get('show_sidebar', True)

    @staticmethod
    def get_render_mode():
        return 'vue', 'dist/assets'

    def get_sidebar_nav(self):
        return [{'nav_key': 'main', 'title': '自动订阅', 'icon': 'mdi-playlist-plus',
                 'section': 'subscribe', 'permission': 'manage', 'order': 45}] if self._config.get('show_sidebar', True) else []

    def get_form(self):
        return [], copy.deepcopy(self._config)

    def get_page(self):
        return []

    def get_api(self):
        routes = [('/status', self.status, 'GET'), ('/settings', self.settings_save, 'POST'),
                  ('/tasks/order', self.task_order, 'POST'), ('/tasks', self.task_save, 'POST'),
                  ('/tasks/{task_id}', self.task_delete, 'DELETE'),
                  ('/tasks/{task_id}/preview', self.preview, 'POST'), ('/tasks/{task_id}/run', self.execute, 'POST'),
                  ('/history/hide', self.history_hide, 'POST'), ('/history/retry', self.history_retry, 'POST'),
                  ('/migration', self.migration, 'POST')]
        return [{'path': p, 'endpoint': fn, 'methods': [m], 'auth': 'bear', 'summary': fn.__doc__} for p, fn, m in routes]

    def get_service(self):
        return [{'id': 'AutoSubscribe_'+t['id'], 'name': '自动订阅：'+t['name'],
                 'trigger': CronTrigger.from_crontab(t['cron'], timezone=settings.TZ), 'func': self.run,
                 'func_kwargs': {'task_id': t['id']}} for t in self._config.get('tasks', [])
                if self._config.get('enabled') and t['enabled']]

    def stop_service(self):
        self._stop.set()
        for task_id in list(self._pending):
            remove_plugin_once_job(self.__class__.__name__, task_id)
        self._pending.clear()

    def _persist(self, config):
        if config == self._config:
            return
        if not self.update_config(config):
            raise HTTPException(500, '配置保存失败')
        self._config = config
        update_plugin_job(self.__class__.__name__)

    def status(self, _: object = Depends(get_current_active_manage_user)):
        """返回任务、预览、分类记录及迁移摘要。"""
        with _LOCK:
            records = [r for r in (self.get_data('records') or {}).values() if not r.get('hidden')]
            records.sort(key=lambda r: r.get('time', 0), reverse=True)
            # 不向界面传回重试候选的内部快照。
            rows = [{k: v for k, v in r.items() if k != 'candidate'} for r in records]
            next_runs = {}
            from datetime import datetime
            import pytz
            now = datetime.now(pytz.timezone(settings.TZ))
            for t in self._config.get('tasks', []):
                if t['enabled'] and self._config['enabled']:
                    next_runs[t['id']] = CronTrigger.from_crontab(t['cron'], timezone=settings.TZ).get_next_fire_time(None, now).timestamp()
            return {'config': copy.deepcopy(self._config), 'records': rows, 'runtime': copy.deepcopy(self._runtime),
                    'plans': self.get_data('plans') or {}, 'runs': self.get_data('runs') or [], 'next_runs': next_runs,
                    'sources': SOURCES, 'ranks': RANKS, 'migration': self.get_data('migration_report')}

    def settings_save(self, payload: Settings, _: object = Depends(get_current_active_manage_user)):
        """保存全局开关，保留任务和顺序。"""
        with _LOCK:
            self._persist({**self._config, **payload.model_dump()})
        return {'success': True}

    def task_save(self, payload: Task, _: object = Depends(get_current_active_manage_user)):
        """编辑时原位更新，新增时追加到列表末尾。"""
        with _LOCK:
            if payload.id in self._pending or self._runtime.get(payload.id, {}).get('status') == 'running':
                raise HTTPException(409, '任务运行中，请结束后再编辑')
            self._persist({**self._config, 'tasks': save_task(self._config['tasks'], payload.model_dump())})
            plans = self.get_data('plans') or {}
            plans.pop(payload.id, None)
            self.save_data('plans', plans)
        return {'success': True, 'data': {'id': payload.id}}

    def task_order(self, payload: TaskOrder, _: object = Depends(get_current_active_manage_user)):
        """只调整展示顺序，不改变任务 ID、规则和历史。"""
        with _LOCK:
            try:
                tasks = reorder(self._config['tasks'], payload.ids)
            except ValueError as err:
                raise HTTPException(409, str(err)) from err
            self._persist({**self._config, 'tasks': tasks})
        return {'success': True}

    def task_delete(self, task_id: str, _: object = Depends(get_current_active_manage_user)):
        """删除任务，已有订阅和历史保留。"""
        with _LOCK:
            if self._runtime.get(task_id, {}).get('status') in ('running', 'queued'):
                raise HTTPException(409, '任务运行中，请稍后删除')
            self._persist({**self._config, 'tasks': [t for t in self._config['tasks'] if t['id'] != task_id]})
        return {'success': True}

    def _queue(self, task_id, preview):
        task = next((t for t in self._config['tasks'] if t['id'] == task_id), None)
        if not task:
            raise HTTPException(404, '任务不存在')
        if not preview and not (task['enabled'] and self._config['enabled']):
            raise HTTPException(409, '请先启用全局开关和当前任务')
        with _LOCK:
            if task_id in self._pending or self._runtime.get(task_id, {}).get('status') == 'running':
                raise HTTPException(409, '该任务已在运行或排队')
            self._pending.add(task_id)
            self._runtime[task_id] = {'status': 'queued', 'preview': preview}
            ok = add_plugin_once_job(self.__class__.__name__, task_id, self.run, '自动订阅：'+task['name'],
                                     delay_seconds=1, func_kwargs={'task_id': task_id, 'preview': preview})
            if not ok:
                self._pending.discard(task_id)
                self._runtime[task_id] = {'status': 'failed', 'error': '排队失败'}
                raise HTTPException(503, '任务排队失败')
        return {'success': True, 'message': '已排队'}

    def preview(self, task_id: str, _: object = Depends(get_current_active_manage_user)):
        """仅扫描和预览，不创建订阅或修改处理记录。"""
        return self._queue(task_id, True)

    def execute(self, task_id: str, _: object = Depends(get_current_active_manage_user)):
        """排队执行一次订阅扫描。"""
        return self._queue(task_id, False)

    def run(self, task_id, preview=False):
        # 跨任务串行，防止两个来源同时创建同一作品的订阅。
        with _RUN_LOCK:
            self._pending.discard(task_id)
            task = next((copy.deepcopy(t) for t in self._config['tasks'] if t['id'] == task_id), None)
            if self._stop.is_set() or not task or (not preview and not (task['enabled'] and self._config['enabled'])):
                self._runtime[task_id] = {'status': 'stopped'}
                return
            started = time.time()
            self._runtime[task_id] = {'status': 'running', 'started_at': started, 'preview': preview}
            stop_event = self._stop
            stopped = lambda: stop_event.is_set() or (not preview and not self._config.get('enabled'))
            try:
                result = Engine(Gateway(stopped, self.get_data, self.save_data), self.get_data, self.save_data, stopped).run(task, preview)
                with _LOCK:
                    if preview:
                        plans = self.get_data('plans') or {}
                        plans[task_id] = result
                        self.save_data('plans', plans)
                    self._runtime[task_id] = {'status': 'stopped' if stopped() else 'done', 'started_at': started,
                                               'finished_at': time.time(), 'added': result['added'], 'scanned': result['scanned'], 'preview': preview}
            except Exception as err:
                logger.warning(f'自动订阅任务 {task["name"]} 暂未完成：{type(err).__name__}')
                self._runtime[task_id] = {'status': 'failed', 'started_at': started, 'finished_at': time.time(),
                                          'error': (str(err)[:180] if isinstance(err, ValueError) else f'数据源访问或处理失败（{type(err).__name__}），下轮重试'), 'preview': preview}
            with _LOCK:
                runs = self.get_data('runs') or []
                runs.append({**self._runtime[task_id], 'task_id': task_id, 'task_name': task['name']})
                self.save_data('runs', runs[-300:])

    def history_hide(self, payload: HistoryAction, _: object = Depends(get_current_active_manage_user)):
        """隐藏展示记录，不重置去重账本，也不删除已有订阅。"""
        with _RUN_LOCK, _LOCK:
            records = self.get_data('records') or {}
            for key in payload.ids:
                if key in records:
                    records[key]['hidden'] = True
            self.save_data('records', records)
        return {'success': True}

    def history_retry(self, payload: HistoryAction, _: object = Depends(get_current_active_manage_user)):
        """将未成功的条目加入下轮重试，已有订阅不会重复创建。"""
        with _RUN_LOCK, _LOCK:
            records = self.get_data('records') or {}
            for key in payload.ids:
                r = records.get(key, {})
                if r.get('candidate') and r.get('status') not in ('subscribed', 'migrated', 'existing', 'library', 'duplicate'):
                    r.update(status='retry', reason='已加入下轮重试', hidden=False)
            self.save_data('records', records)
        return {'success': True}

    def migration(self, _: object = Depends(get_current_active_manage_user)):
        """备份并导入四个旧插件的数据，可重复调用，不重复导入。"""
        with _RUN_LOCK, _LOCK:
            if self.get_data('migration_report'):
                return {'success': True, 'data': self.get_data('migration_report')}
            if self._config.get('enabled'):
                raise HTTPException(409, '请先关闭自动订阅，再进行迁移')
            manager = PluginManager()
            snapshot = {}
            for pid in LEGACY:
                snapshot[pid] = {'config': manager.get_plugin_config(pid) or {}, 'data': {}}
                for key in ('history', 'already_handle', 'unique_flags'):
                    snapshot[pid]['data'][key] = self.get_data(key, plugin_id=pid)
            self.save_data('legacy_backup', snapshot)
            result = migrate(snapshot)
            self.save_data('records', {**(self.get_data('records') or {}), **result['records']})
            self.save_data('ledger', {**(self.get_data('ledger') or {}), **result['ledger']})
            self.save_data('legacy_processed', result['legacy_processed'])
            tasks = self._config['tasks']
            for task in result['tasks']:
                tasks = save_task(tasks, task)
            self._persist({**self._config, 'tasks': tasks})
            self.save_data('migration_report', result['report'])
            return {'success': True, 'data': result['report']}
