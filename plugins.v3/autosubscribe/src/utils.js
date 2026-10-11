export const sources = {popular:'热门媒体',douban:'豆瓣榜单',actor:'演员作品',release:'将映关注',wish:'豆瓣想看'}
export const statuses = {subscribed:'已创建订阅',migrated:'已订阅',existing:'订阅已存在',library:'媒体库已存在',duplicate:'已处理',filtered:'暂不符合条件',retry:'等待重试',waiting:'等待上映',limited:'等待下轮额度',eligible:'符合条件',legacy:'旧版处理记录'}
export const dateTime = x => x ? new Date(x * 1000).toLocaleString('zh-CN',{hour12:false}) : '—'
export function unwrap(r) { const v = r?.data?.success !== undefined ? r.data : r; if(v?.success === false) throw Error(v.message || '操作失败'); return v?.data ?? v }
export function errorText(e) { const d=e?.response?.data?.detail; return typeof d==='string'?d:Array.isArray(d)?d.map(x=>x.msg).join('，'):e?.message || '操作失败' }
export const lines = x => [...new Set(String(x || '').split(/[\n，,]/).map(s=>s.trim()).filter(Boolean))]
export function draft(t={}) { return JSON.parse(JSON.stringify({name:'',source:'popular',enabled:false,cron:'0 9 * * *',media_type:'movie',scan_limit:30,max_add:3,min_year:0,min_rating:0,min_votes:0,min_popularity:5000,ranks:[],rss_urls:[],actors:[],douban_user:'',wish_days:7,titles:[],release_mode:'recognized',release_days:30,season_mode:'first',save_path:'',...t})) }
export function orderedIds(tasks,from,to) { const ids=tasks.map(t=>t.id),a=ids.indexOf(from),b=ids.indexOf(to); if(a<0||b<0) return ids; ids.splice(b,0,ids.splice(a,1)[0]); return ids }
