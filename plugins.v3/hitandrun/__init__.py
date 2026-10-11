"""H&R助手：统一识别、标签保护与达标解除，不删除下载任务。"""
import copy
import threading
import time
from apscheduler.triggers.cron import CronTrigger
from fastapi import Depends, HTTPException
from app.sdk.plugin import _PluginBase
from app.sdk.events import Event, eventmanager
from app.schemas.types import EventType
from app.sdk.config import settings
from app.sdk.logging import logger
from app.sdk.scheduler import add_plugin_once_job, remove_plugin_once_job
from app.api.dependencies.auth import get_current_active_manage_user
from .policy import Config, decision
from .gateway import Gateway

_LOCK = threading.RLock()

class HitAndRun(_PluginBase):
    plugin_name = 'H&R助手'
    plugin_desc = '统一识别 H&R，按站点条件解除保护及指定标签，不删除种子。'
    plugin_icon = 'https://raw.githubusercontent.com/InfinityPacer/MoviePilot-Plugins/main/icons/hitandrun.png'
    plugin_version = '2.0.0'
    plugin_author = 'seiun, InfinityPacer'
    author_url = 'https://github.com/zhangyxXyz/MoviePilot-Plugins'
    plugin_config_prefix = 'hitandrun_'
    plugin_order = 24
    auth_level = 1

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()
        self._config = Config()

    def init_plugin(self, config=None):
        self.stop_service()
        self._stop = threading.Event()
        if (config or {}).get('_migration') and not self.get_data('migration_done'):
            migration = config['_migration']
            self.save_data('records', migration.get('records', {}))
            self.save_data('events', migration.get('events', {}))
            self.save_data('migration_done', time.time())
        self._config = Config.model_validate(config or {})
        started = self._config.enabled and not self._config.started_at
        if started:
            self._config.started_at = time.time()
        if started or config and config.get('enabled') != self._config.enabled:
            self.update_config(self._config.model_dump())

    def get_state(self): return True  # 保持配置入口可见，运行由各任务开关控制
    @staticmethod
    def get_render_mode(): return 'vue', 'dist/assets'
    def get_form(self): return [], self._config.model_dump()
    def get_page(self): return []
    @staticmethod
    def get_command(): return []
    def get_sidebar_nav(self):
        return [{'nav_key':'main','title':'H&R助手','icon':'mdi-shield-check-outline','section':'organize','permission':'manage','order':47}]
    def get_service(self):
        return [{'id':f'HitAndRun_{t.id}', 'name':f'H&R：{t.name}',
                 'trigger':CronTrigger.from_crontab(t.cron, timezone=settings.TZ),
                 'func':self.run, 'func_kwargs':{'task_id':t.id}}
                for t in self._config.tasks if t.enabled and self._config.enabled]
    def stop_service(self):
        self._stop.set()
        remove_plugin_once_job(self.__class__.__name__, 'events')
        for task_id in ['all', *[t.id for t in self._config.tasks]]:
            remove_plugin_once_job(self.__class__.__name__, f'manual_{task_id}')
    def get_api(self):
        return [{'path':p,'endpoint':f,'methods':[m],'auth':'bear','summary':f.__doc__ or p}
                for p,f,m in [('/status',self.status,'GET'),('/settings',self.save_settings,'POST'),('/scan',self.scan,'POST')]]
    def status(self, _: object = Depends(get_current_active_manage_user)):
        gateway = Gateway(self._config.sites)
        return {'success':True,'data':{'config':self._config.model_dump(),
            'options':{'sites':[{'title':s['name'],'value':s['id']} for s in gateway.sites.values()],
                       'downloaders':[{'title':c.name,'value':c.name} for c in gateway.helper.get_configs().values()]},
            'records':list((self.get_data('records') or {}).values()), 'runs':self.get_data('runs') or [], 'last_run':self.get_data('last_run') or {}}}
    def save_settings(self, payload: Config, _: object = Depends(get_current_active_manage_user)):
        """保存规则；改名保留旧标签保护，扫描先加新标签再移除旧标签。"""
        with _LOCK:
            payload.started_at = self._config.started_at or time.time()
            payload.tag_aliases = sorted(set(self._config.tag_aliases + [self._config.confirmed_tag,self._config.pending_tag]) - {payload.confirmed_tag,payload.pending_tag})
            self._config = payload
            self.update_config(payload.model_dump())
        from app.sdk.scheduler import update_plugin_job
        update_plugin_job(self.__class__.__name__)
        return {'success':True}
    def scan(self, payload: dict | None = None, _: object = Depends(get_current_active_manage_user)):
        """立即检查选定下载器；不传 task_id 时检查全部已启用任务。"""
        task_id = (payload or {}).get('task_id')
        if task_id and not any(t.id == task_id and t.enabled for t in self._config.tasks):
            raise HTTPException(409, '任务不存在或已停用')
        if not self._config.enabled:
            raise HTTPException(409, '请先启用至少一个下载器任务')
        queued = add_plugin_once_job(self.__class__.__name__, f'manual_{task_id or "all"}',
                                    self.run, 'H&R 标签扫描', delay_seconds=1,
                                    func_kwargs={'task_id':task_id})
        if not queued:
            raise HTTPException(409, '检查已排队，请稍后查看记录')
        return {'success':True}

    @eventmanager.register(EventType.DownloadAdded)
    def download_added(self, event: Event = None):
        data = getattr(event, 'event_data', None) or {}
        torrent = getattr(data.get('context'), 'torrent_info', None)
        if torrent: self._event(data, torrent, False)
    @eventmanager.register(EventType.PluginTriggered)
    def brush_added(self, event: Event = None):
        data = getattr(event, 'event_data', None) or {}
        if data.get('event_name') == 'brushflow_download_added' and data.get('data'):
            self._event(data, data['data'], True)
    def _event(self, data, torrent, brush):
        import re
        if not self._config.enabled: return
        name, h = data.get('downloader'), str(data.get('hash') or '').lower()
        if not any(t.enabled and t.downloader == name for t in self._config.tasks) or not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', h): return
        get = torrent.get if isinstance(torrent, dict) else lambda k, d=None: getattr(torrent,k,d)
        try: sid = int(get('site'))
        except (ValueError,TypeError): return
        if not any(t.enabled and t.downloader == name and t.matches_sites([sid]) for t in self._config.tasks): return
        signal = True if get('hit_and_run') is True else False if brush and get('hit_and_run') is False and get('hr_verified') is True else None
        with _LOCK:
            events = self.get_data('events') or {}
            key = f'{name}:{h}'
            item = events.setdefault(key, {'at':time.time(), 'signals':{}})
            item['at'] = time.time()
            if item['signals'].get(str(sid)) is not True: item['signals'][str(sid)] = signal
            self.save_data('events',events)
        add_plugin_once_job(self.__class__.__name__, 'events', self.run, 'H&R 下载事件检查', delay_seconds=2)

    def run(self, task_id=None):
        """每个下载器 Hash 独立；解除自有保护标签及任务明确指定的额外标签。"""
        if not self._config.enabled or self._stop.is_set(): return
        if not _LOCK.acquire(blocking=False): return
        try:
            cfg = self._config
            gateway = Gateway(cfg.sites)
            records, events = self.get_data('records') or {}, self.get_data('events') or {}
            report = dict(started_at=time.time(), checked=0, changed=0, failed=0, errors=[], tasks=[])
            for task in cfg.tasks:
                if not task.enabled or task_id and task.id != task_id: continue
                result = dict(task_id=task.id, name=task.name, downloader=task.downloader,
                              checked=0, changed=0, failed=0, details=[])
                report['tasks'].append(result)
                before = {k:report[k] for k in ['checked','changed','failed']}
                try: rows = gateway.snapshot(task.downloader)
                except Exception as err:
                    report['failed'] += 1
                    result.update(failed=1,error=f'读取失败（{type(err).__name__}）')
                    report['errors'].append(f'{task.downloader}：读取失败（{type(err).__name__}）')
                    continue
                present = {r['hash'] for r in rows}
                for key, record in records.items():
                    if record.get('downloader') == task.downloader and record.get('hash') not in present:
                        record['absent'] = True
                for row in rows:
                    if self._stop.is_set(): return
                    key = f"{task.downloader}:{row['hash']}"
                    record, event = records.get(key), events.get(key)
                    associated = set(row['site_ids'])
                    for source in (record, event):
                        if source: associated.update(int(sid) for sid in source.get('signals', {}))
                    if not task.matches_sites(associated):
                        events.pop(key, None)
                        continue
                    extra = bool(set(row['tags']) & set(task.extra_tags))
                    # Hash 重加或已确认消失后出现是新任务；变更名字不重置。
                    if record and (record.get('absent') or record.get('added_at') is not None and row['added_at'] != record['added_at']):
                        record = None
                    if record and record.get('history_exempt') and not extra and not event: continue
                    if not record:
                        # 仅事件、新下载和显式追加标签纳管，历史普通任务不补待确认标签。
                        if not event and not extra and (row['added_at'] is None or row['added_at'] < cfg.started_at): continue
                        record = {'downloader':task.downloader,'hash':row['hash'],'owned':[], 'signals':{}}
                        records[key] = record
                    record.update(name=row['name'], added_at=row['added_at'], absent=False, site_ids=row['site_ids'],
                                  checked_at=time.time(), seed_hours=None if row['seed_seconds'] is None else round(row['seed_seconds']/3600,2))
                    if event:
                        for sid, signal in event['signals'].items():
                            if record.get('signals',{}).get(sid) is not True: record.setdefault('signals',{})[sid] = signal
                    # 已解除的同一次下载不因辅种来源标签或重复事件重新加保护。
                    settled = record.get('state') in {'released','clear'} and not record.get('owned')
                    source_needs_check = extra and (record.get('state') == 'clear' or task.remove_extra_tags)
                    if settled and not source_needs_check:
                        events.pop(key,None)
                        continue
                    state, reason, desired = decision(row,gateway.policies,record,task,cfg)
                    owned = set(record.get('owned',[]))
                    before_owned = set(owned)
                    add, remove = desired - set(row['tags']), owned - desired
                    if state == 'released' and task.remove_extra_tags:
                        remove |= set(task.extra_tags) & set(row['tags'])
                    confirmed_removed = set()
                    try:
                        if add:
                            record['pending_add'] = sorted(add)
                            self.save_data('records',records)
                            gateway.hr_tags(task.downloader,row['hash'],add=add)
                            owned |= add
                            record['owned'] = sorted(owned)
                            record.pop('pending_add',None)
                            self.save_data('records',records)
                        if remove:
                            gateway.hr_tags(task.downloader,row['hash'],remove=remove)
                            record['owned'] = sorted(owned-remove)
                            confirmed_removed = set(remove) & set(row['tags'])
                        record.update(state=state,reason=reason)
                        record.pop('error',None)
                        events.pop(key,None)
                        report['changed'] += bool(add or remove)
                    except Exception as err:
                        record['error'] = f'标签写入未确认（{type(err).__name__}），下轮重试'
                        report['failed'] += 1
                    if (add or remove or record.get('error')) and len(result['details']) < 200:
                        after_owned = set(record.get('owned', []))
                        result['details'].append(dict(hash=row['hash'], name=row['name'], site_ids=row['site_ids'],
                            added=sorted(after_owned-before_owned), removed=sorted(confirmed_removed),
                            reason=reason, error=record.get('error', '')))
                    report['checked'] += 1
                    self.save_data('records',records)
                self.save_data('records',records)
                self.save_data('events',events)
                result.update({k:report[k]-before[k] for k in before})
            report['finished_at'] = time.time()
            self.save_data('last_run',report)
            self.save_data('runs', ([report] + (self.get_data('runs') or []))[:100])
            logger.info(f"H&R 标签检查：检查 {report['checked']}，更新 {report['changed']}，失败 {report['failed']}")
        except Exception as err:
            logger.error(f'H&R 标签扫描失败：{type(err).__name__}')
            failed = {'started_at':time.time(),'finished_at':time.time(),'checked':0,'changed':0,'failed':1,
                      'errors':[type(err).__name__], 'tasks':[dict(task_id=t.id,name=t.name,downloader=t.downloader,
                      checked=0,changed=0,failed=1,error=f'扫描失败（{type(err).__name__}）',details=[])
                      for t in self._config.tasks if t.enabled and (not task_id or t.id == task_id)]}
            self.save_data('last_run', failed)
            self.save_data('runs', ([failed] + (self.get_data('runs') or []))[:100])
        finally: _LOCK.release()
