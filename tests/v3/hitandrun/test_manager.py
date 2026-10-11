"""真实管理器 + 内存持久化/下载器，验证重复扫描、重加、改名及故障。"""
import copy
import threading
import time
from app.plugins.hitandrun import HitAndRun
from app.plugins.hitandrun.policy import Config
import app.plugins.hitandrun as module

class FakeGateway:
    rows=[]
    fail=False
    def __init__(self, rules):self.policies={s.site_id:s for s in rules}
    def snapshot(self,name):return copy.deepcopy(self.rows)
    def hr_tags(self,name,h,add=(),remove=()):
        if self.fail: raise RuntimeError('unavailable')
        row=next(r for r in self.rows if r['hash']==h)
        row['tags']=list((set(row['tags'])|set(add))-set(remove))

def setup(monkeypatch):
    monkeypatch.setattr(module,'Gateway',FakeGateway)
    FakeGateway.fail=False
    FakeGateway.rows=[dict(hash='a'*40,name='seed',tags=['辅种','保留'],site_ids=[1],sites_complete=True,completed=True,seed_seconds=0,ratio=0,uploaded=0,total_size=100,added_at=200)]
    plugin=HitAndRun();plugin._stop=threading.Event()
    plugin._config=Config.model_validate(dict(enabled=True,started_at=100,sites=[dict(site_id=1)],tasks=[dict(id='a',name='a',downloader='qb',extra_tags=['辅种'])]))
    store={}
    plugin.get_data=lambda k:copy.deepcopy(store.get(k))
    plugin.save_data=lambda k,v:store.update({k:copy.deepcopy(v)})
    return plugin,store

def test_release_does_not_reprotect_source_label(monkeypatch):
    p,db=setup(monkeypatch);p.run();assert set(FakeGateway.rows[0]['tags'])=={'辅种','保留','H&R'}
    FakeGateway.rows[0]['seed_seconds']=168*3600
    p.run();p.run();assert set(FakeGateway.rows[0]['tags'])=={'辅种','保留'}
    assert db['records']['qb:'+'a'*40]['state']=='released'

def test_readd_resets_completed_record(monkeypatch):
    p,db=setup(monkeypatch);FakeGateway.rows[0]['seed_seconds']=168*3600;p.run()
    FakeGateway.rows[0].update(added_at=300,seed_seconds=0)
    p.run();assert 'H&R' in FakeGateway.rows[0]['tags']

def test_history_not_imported_except_explicit_extra_tags(monkeypatch):
    p,db=setup(monkeypatch);FakeGateway.rows[0].update(added_at=50,tags=[])
    p.run();assert not db['records']
    FakeGateway.rows[0]['tags']=['辅种'];p.run();assert 'H&R' in FakeGateway.rows[0]['tags']

def test_rename_adds_new_removes_owned_old(monkeypatch):
    p,db=setup(monkeypatch);p.run();p._config.confirmed_tag='我的HR';p._config.tag_aliases=['H&R'];p.run()
    assert set(FakeGateway.rows[0]['tags'])=={'辅种','保留','我的HR'}

def test_failed_rename_keeps_old_protection(monkeypatch):
    p,db=setup(monkeypatch);p.run();p._config.confirmed_tag='我的HR';FakeGateway.fail=True;p.run()
    assert 'H&R' in FakeGateway.rows[0]['tags'];assert db['last_run']['failed']==1

def test_duplicate_event_does_not_reprotect_released(monkeypatch):
    p,db=setup(monkeypatch);FakeGateway.rows[0]['seed_seconds']=168*3600;p.run()
    db['events']={'qb:'+'a'*40:{'at':time.time(),'signals':{'1':True}}};p.run()
    assert 'H&R' not in FakeGateway.rows[0]['tags']

def test_release_extra_tags_is_explicit_and_logged(monkeypatch):
    p,db=setup(monkeypatch);p._config.tasks[0].remove_extra_tags=True
    p.run();FakeGateway.rows[0]['seed_seconds']=168*3600;p.run()
    assert set(FakeGateway.rows[0]['tags'])=={'保留'}
    result=db['runs'][0]['tasks'][0]
    assert result['task_id']=='a' and result['changed']==1
    assert set(result['details'][0]['removed'])=={'辅种','H&R'}
    assert db['records']['qb:'+'a'*40]['state']=='released'

def test_enabling_extra_release_cleans_already_compliant_sources(monkeypatch):
    p,db=setup(monkeypatch);FakeGateway.rows[0]['seed_seconds']=168*3600;p.run()
    assert '辅种' in FakeGateway.rows[0]['tags']
    p._config.tasks[0].remove_extra_tags=True;p.run()
    assert '辅种' not in FakeGateway.rows[0]['tags']

def test_selected_task_scan_only_checks_that_downloader(monkeypatch):
    from app.plugins.hitandrun.policy import ScanTask
    p,db=setup(monkeypatch);p._config.tasks.append(ScanTask(id='b',name='Second',downloader='qb2',extra_tags=['辅种']))
    p.run(task_id='b')
    assert list(db['records'])==['qb2:'+'a'*40]
    assert [t['task_id'] for t in db['runs'][0]['tasks']]==['b']

def test_scan_history_keeps_failed_reads(monkeypatch):
    p,db=setup(monkeypatch)
    monkeypatch.setattr(FakeGateway,'snapshot',lambda *a:(_ for _ in ()).throw(RuntimeError('offline')))
    p.run()
    assert db['runs'][0]['tasks'][0]['failed']==1
    assert '读取失败' in db['runs'][0]['tasks'][0]['error']

def test_site_scope_skips_unselected_and_unknown(monkeypatch):
    p,db=setup(monkeypatch);p._config.tasks[0].site_ids=[2]
    p.run();assert not db['records'];assert FakeGateway.rows[0]['tags']==['辅种','保留']
    FakeGateway.rows[0]['site_ids']=[];p.run();assert not db['records']
    p._config.tasks[0].site_ids=None;p.run();assert 'H&R' in FakeGateway.rows[0]['tags']

def test_empty_site_scope_preserves_existing_protection(monkeypatch):
    p,db=setup(monkeypatch);p.run()
    p._config.tasks[0].site_ids=[];FakeGateway.rows[0]['seed_seconds']=168*3600
    p.run();assert 'H&R' in FakeGateway.rows[0]['tags'];assert db['last_run']['checked']==0

def test_selected_site_does_not_bypass_other_site_target(monkeypatch):
    from app.plugins.hitandrun.policy import SiteRule
    p,db=setup(monkeypatch);p._config.tasks[0].site_ids=[1]
    p._config.sites.append(SiteRule(site_id=2,seed_hours=300))
    FakeGateway.rows[0].update(site_ids=[1,2],seed_seconds=168*3600)
    p.run();assert 'H&R' in FakeGateway.rows[0]['tags']

def test_download_event_respects_site_scope(monkeypatch):
    p,db=setup(monkeypatch);p._config.tasks[0].site_ids=[2]
    queued=[];monkeypatch.setattr(module,'add_plugin_once_job',lambda *a,**k:queued.append(k))
    p._event({'downloader':'qb','hash':'a'*40},{'site':1,'hit_and_run':True},True)
    assert not db and not queued
    p._event({'downloader':'qb','hash':'a'*40},{'site':2,'hit_and_run':True},True)
    assert queued and db['events']['qb:'+'a'*40]['signals']=={'2':True}
