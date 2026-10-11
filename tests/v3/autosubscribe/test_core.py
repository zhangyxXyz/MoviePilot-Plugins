"""真实模型与独立执行器测试，网关替身不会创建真实订阅。"""
import copy
import importlib.util
import pathlib
import sys
import types
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[3]
PACKAGE=ROOT/'plugins.v3/autosubscribe'
pkg=types.ModuleType('auto_candidate');pkg.__path__=[str(PACKAGE)];sys.modules[pkg.__name__]=pkg
from auto_candidate.models import Task, save_task, reorder
from auto_candidate.engine import Engine, media_key, filter_reason
from auto_candidate.migration import migrate

class Gateway:
    def __init__(self, items): self.items=items;self.added=[];self.existing={};self.fail=set()
    def candidates(self, task, pending): return copy.deepcopy(self.items)
    def exists(self,item): return self.existing.get(item['media_id'])
    def subscribe(self,task,item):
        if item['media_id'] in self.fail: raise RuntimeError('transient')
        self.added.append(media_key(item));return len(self.added)

class CoreTest(unittest.TestCase):
    def setUp(self):
        self.data={};self.task=Task(id='one',name='测试',source='douban',media_type='movie',min_rating=8,max_add=1).model_dump()
        self.item={'media_source':'themoviedb','media_id':'1','media_type':'movie','title':'测试','year':'2026','douban_rating':8.5,'tmdb_rating':4}
    def run_engine(self,items,preview=False,gateway=None):
        g=gateway or Gateway(items)
        result=Engine(g,lambda k:copy.deepcopy(self.data.get(k)),lambda k,v:self.data.update({k:copy.deepcopy(v)})).run(self.task,preview)
        return result,g
    def test_douban_does_not_use_tmdb_rating(self):
        i={**self.item,'douban_rating':None,'tmdb_rating':9}
        self.assertEqual(filter_reason(self.task,i)[0],'retry')
        self.assertEqual(filter_reason(self.task,self.item)[0],'eligible')
    def test_preview_has_no_persistent_side_effect(self):
        result,g=self.run_engine([self.item],True)
        self.assertEqual(result['items'][0]['status'],'eligible');self.assertEqual(self.data,{});self.assertEqual(g.added,[])
    def test_preview_respects_limit_without_creating_subscriptions(self):
        result,g=self.run_engine([self.item,{**self.item,'media_id':'2'}],True)
        self.assertEqual([x['status'] for x in result['items']],['eligible','limited'])
        self.assertEqual(result['would_add'],1)
        self.assertEqual(g.added,[])
    def test_success_only_consumes_limit(self):
        g=Gateway([{**self.item,'media_id':str(i)} for i in range(4)]);g.existing['0']='existing';g.fail.add('1')
        r,_=self.run_engine([],gateway=g)
        self.assertEqual([x['status'] for x in r['items']],['existing','retry','subscribed','limited']);self.assertEqual(r['added'],1)
    def test_cross_task_dedupe_and_provenance(self):
        self.run_engine([self.item]);self.task.update(id='two',name='另一来源')
        r,g=self.run_engine([self.item]);self.assertEqual(g.added,[]);self.assertEqual(r['items'][0]['status'],'duplicate')
        self.assertEqual(self.data['ledger'][media_key(self.item)]['task_ids'],['one','two'])
    def test_seasons_and_types_have_distinct_keys(self):
        keys={media_key(self.item),media_key({**self.item,'media_type':'tv','season':1}),media_key({**self.item,'media_type':'tv','season':2})}
        self.assertEqual(len(keys),3)
    def test_rescan_preserves_success_history(self):
        self.run_engine([self.item]);self.run_engine([self.item]);self.assertEqual(next(iter(self.data['records'].values()))['status'],'subscribed')
    def test_retry_not_poisoned_by_failed_attempt(self):
        self.run_engine([{**self.item,'error':'暂时失败'}]);self.assertFalse(self.data.get('ledger'));r,g=self.run_engine([self.item]);self.assertEqual(len(g.added),1)
    def test_sort_then_edit_keeps_order(self):
        ts=[Task(id=str(i),name=str(i),source='popular').model_dump() for i in range(3)]
        ts=reorder(ts,['2','0','1']);edited={**ts[1],'name':'改名'};ts=save_task(ts,edited)
        self.assertEqual([t['id'] for t in ts],['2','0','1']);self.assertEqual(ts[1]['name'],'改名')
    def test_stale_order_rejected(self):
        with self.assertRaises(ValueError): reorder([self.task],['missing'])
        with self.assertRaises(ValueError): reorder([self.task],['one','one'])
    def test_migration_preserves_raw_processed_and_season(self):
        snapshot={'PopularSubscribe':{'config':{'movie_enabled':True},'data':{'already_handle':['tmdb:1'], 'history':[{'title':'旧剧','type':'电视剧','media_source':'themoviedb','media_id':'2','season':'第二季'}]}}}
        result=migrate(snapshot);r=next(iter(result['records'].values()))
        self.assertEqual(r['season'],2);self.assertEqual(r['task_id'],'legacy-popular');self.assertEqual(result['legacy_processed']['PopularSubscribe'],['tmdb:1'])
        self.assertEqual(set(result['records']),set(migrate(snapshot)['records']))
    def test_missing_votes_waits(self):
        self.task['min_votes']=1000;self.assertEqual(filter_reason(self.task,self.item)[0],'retry')
    def test_stopping_prevents_add(self):
        g=Gateway([self.item]);r=Engine(g,lambda k:None,lambda k,v:None,lambda:True).run(self.task)
        self.assertEqual(r['added'],0);self.assertEqual(g.added,[])

class GatewayTest(unittest.TestCase):
    def test_rss_score_is_explicit_douban_score(self):
        from auto_candidate.gateway import parse_rss
        xml='<rss><channel><item><title>电影</title><link>https://movie.douban.com/subject/123/</link><description>评分：8.5分 标签：2026 / 剧情</description></item></channel></rss>'
        item=parse_rss(xml)[0]
        self.assertEqual(item['douban_rating'],8.5)
        self.assertEqual(item['year'],'2026')
        self.assertEqual(item['douban_id'],'123')
    def test_rss_does_not_guess_rating_from_year(self):
        from auto_candidate.gateway import parse_rss
        item=parse_rss('<rss><item><title>电影</title><link>https://movie.douban.com/subject/123/</link><description>2026 年上映</description></item></rss>')[0]
        self.assertIsNone(item['douban_rating'])
    def test_douban_uses_fresh_rss_cache(self):
        import time
        from auto_candidate.gateway import Gateway
        g=object.__new__(Gateway)
        g.load=lambda key:{'time':time.time(),'xml':'<rss><item><title>缓存电影</title><link>https://movie.douban.com/subject/123/</link><description>评分：8.1</description></item></rss>'}
        g.save=lambda *args: self.fail('fresh cache should not be overwritten')
        g.resolve_douban=lambda item:item
        items=list(g.douban({'ranks':['movie-weekly'],'rss_urls':[]}))
        self.assertEqual(items[0]['douban_rating'],8.1)
    def test_one_bad_douban_detail_does_not_abort_feed(self):
        import time
        from auto_candidate.gateway import Gateway
        g=object.__new__(Gateway)
        g.load=lambda key:{'time':time.time(),'xml':'<rss><item><title>电影</title><link>https://movie.douban.com/subject/123/</link></item></rss>'}
        g.save=lambda *args:None
        def fail(item): raise ValueError('temporary')
        g.resolve_douban=fail
        items=list(g.douban({'ranks':['movie-weekly'],'rss_urls':[]}))
        self.assertIn('error',items[0])

    def test_xml_entities_are_rejected(self):
        from auto_candidate.gateway import parse_rss
        with self.assertRaises(ValueError): parse_rss('<!DOCTYPE rss><rss/>')

if __name__=='__main__': unittest.main()
