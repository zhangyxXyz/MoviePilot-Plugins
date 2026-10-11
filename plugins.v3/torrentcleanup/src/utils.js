export const actions = [
  { title: '仅预览', value: 'preview' }, { title: '标记清理候选', value: 'mark' },
  { title: '暂停做种', value: 'pause' }, { title: '仅删除任务', value: 'delete' },
  { title: '删除任务及文件', value: 'deletefile' },
]
export const modes = [{ title: '保种条件清理', value: 'retention' }, { title: '站点失效清理', value: 'invalid' }, { title: '下载错误清理（qBittorrent）', value: 'download_error' }]
export const actionName = value => actions.find(a => a.value === value)?.title || value
export const modeName = value => value === 'conditions' ? '按勾选条件筛选' : modes.find(a => a.value === value)?.title || value
export const qbStates = Object.entries({downloading:'正在下载，传输数据',stalledDL:'正在下载，未建立连接',uploading:'正在上传，传输数据',stalledUP:'正在上传，未建立连接',error:'发生错误',pausedDL:'暂停，下载未完成',pausedUP:'暂停，下载完成',stoppedDL:'停止，下载未完成（QB 5）',stoppedUP:'停止，下载完成（QB 5）',missingFiles:'文件丢失',checkingDL:'检查中，下载未完成',checkingUP:'检查中，下载完成',checkingResumeData:'启动时恢复数据',forcedDL:'强制下载，忽略队列',queuedDL:'下载排队',forcedUP:'强制上传，忽略队列',queuedUP:'上传排队',allocating:'分配磁盘空间',metaDL:'获取元数据',forcedMetaDL:'强制获取元数据',moving:'移动文件',unknown:'未知状态'}).map(([value,title])=>({value,title:`${title}（${value}）`}))
export const trStates = Object.entries({'0':'停止 stopped','1':'校验队列 check_pending','2':'校验中 checking','3':'下载队列 download_pending','4':'下载中 downloading','5':'做种队列 seed_pending','6':'做种 seeding','error':'发生错误，error 非 0'}).map(([value,title])=>({value,title:`${title}（${value}）`}))
export const stateName = (value, downloaderType) => (downloaderType === 'transmission' ? trStates : qbStates).find(s=>s.value.toLowerCase()===String(value).toLowerCase())?.title || value || '未知'

export function conditionDraft(task = {}) {
  const draft = taskDraft(task)
  if (task.mode === 'conditions') return draft
  const old = !!task.id, error = task.mode === 'download_error'
  return { ...draft, mode: 'conditions', states: error ? [...draft.error_states] : [], error_keywords: [],
    completion: old ? error ? 'incomplete' : 'completed' : 'all', require_healthy: old && !error,
    filter_seed: old && !error, filter_ratio: old && !error, verify_invalid: task.mode === 'invalid',
    observe_state: error, require_idle_download: error, filter_age: error, filter_progress: error }
}
export const tags = value => [...new Set((Array.isArray(value) ? value : [value]).flatMap(v => String(v || '').split(/[,，\n]/)).map(v => v.trim()).filter(Boolean))]
export const formatBytes = value => Number(value || 0) < 1024 ** 3 ? `${(Number(value || 0) / 1024 ** 2).toFixed(1)} MB` : `${(Number(value || 0) / 1024 ** 3).toFixed(2)} GB`
export const dateTime = value => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '—'

export function unwrap(response) {
  const value = response?.data?.success !== undefined ? response.data : response
  if (value?.success === false) throw new Error(value.message || '操作未完成')
  return value?.data ?? value
}

export function errorText(error) {
  const body = error?.response?.data
  if (Array.isArray(body?.data)) return body.data.map(v => v.message || v.msg).filter(Boolean).join('，') || body.message
  const detail = body?.detail
  return typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(v => v.msg).join('，') : body?.message || error?.message || '操作失败'
}

export function taskDraft(task = {}) {
  return JSON.parse(JSON.stringify({ id: '', name: '', enabled: false, downloader: '', site_ids: [],
    mode: 'retention', action: 'preview', cron: '0 */6 * * *', min_seed_hours: 168, min_ratio: 0,
    all_sites: false, categories: [], only_moviepilot: false, max_upload_speed_kib: null,
    error_states: ['error', 'missingFiles'], error_min_age_hours: 24, error_observation_hours: 6,
    error_min_observations: 3, error_gap_minutes: 15, error_max_progress: 99.9, error_protect_unknown_hr: true,
    states: [], error_keywords: [], completion: 'completed', require_healthy: true, filter_seed: true, filter_ratio: true,
    verify_invalid: false, observe_state: false, require_idle_download: false, filter_age: false, filter_progress: false,
    min_size_gb: 0, max_size_gb: null, path_prefix: '', include_tags: [],
    protect_tags: ['H&R', '保留', '勿删'], observation_hours: 48, min_observations: 3,
    observation_gap_hours: 6, max_count: 10, max_gb: 100, notify: false, ...task }))
}

export function normalizeTask(draft) {
  const result = taskDraft(draft)
  for (const key of ['include_tags', 'protect_tags', 'categories', 'error_keywords']) result[key] = tags(result[key])
  for (const key of ['min_seed_hours', 'min_ratio', 'min_size_gb', 'observation_hours', 'min_observations', 'observation_gap_hours', 'max_count', 'max_gb']) result[key] = Number(result[key])
  result.max_size_gb = result.max_size_gb === '' || result.max_size_gb == null ? null : Number(result.max_size_gb)
  result.max_upload_speed_kib = result.max_upload_speed_kib === '' || result.max_upload_speed_kib == null ? null : Number(result.max_upload_speed_kib)
  for (const key of ['error_min_age_hours', 'error_observation_hours', 'error_min_observations', 'error_gap_minutes', 'error_max_progress']) result[key] = Number(result[key])
  if (!['download_error', 'conditions'].includes(result.mode)) result.all_sites = false
  return result
}

export function canExecute(config, task, plan, busy, now = Date.now() / 1000) {
  return !!(config?.enabled && task?.enabled && !busy && plan?.candidate_count > 0 &&
    task.action !== 'preview' && plan.action === task.action && plan.revision === config.revision &&
    now >= plan.created_at && now - plan.created_at <= 900)
}
