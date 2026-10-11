export const taskDefaults = {
  id: '',
  name: '',
  enabled: true,
  notify: true,
  site_id: null,
  downloader: '',
  brush_interval: 10,
  check_interval: 5,
  cron: null,
  active_time_range: null,
  site_ratio_control: false,
  site_ratio_target: null,
  disksize: null,
  maxupspeed: null,
  maxdlspeed: null,
  maxdlcount: null,
  freeleech: 'free',
  hr: 'yes',
  include: null,
  exclude: null,
  size: null,
  seeder: null,
  timezone_offset: 0,
  pubtime: null,
  min_seed_hours: null,
  seed_time: null,
  hr_seed_time: null,
  hr_seed_ratio: null,
  hr_ratio_basis: 'client',
  seed_ratio: null,
  seed_size: null,
  download_time: null,
  seed_avgspeed: null,
  seed_inactivetime: null,
  delete_size_range: null,
  up_speed: null,
  dl_speed: null,
  auto_archive_days: null,
  save_path: null,
  delete_except_tags: null,
  dedupe_hash: true,
  dedupe_site_id: true,
  except_subscribe: true,
  proxy_delete: false,
  del_no_free: null,
  qb_category: null,
  site_hr_active: false,
  site_skip_tips: false,
  rss_support: false,
}

/** 统一提取宿主 API 客户端与标准响应模型中的业务数据。 */
export function unwrapResponse(response) {
  if (response && Object.prototype.hasOwnProperty.call(response, 'success')) {
    if (response.success === false) throw new Error(response.message || '操作失败')
    return response.data
  }
  return response?.data ?? response
}

/** 基于完整默认值创建可安全编辑的任务深拷贝。 */
export function cloneTask(task = {}) {
  return JSON.parse(JSON.stringify({ ...taskDefaults, ...(task || {}) }))
}

/** 把表单空值和数字字段标准化为后端请求模型需要的类型。 */
export function normalizeTask(task) {
  const result = cloneTask(task)
  const nullableNumbers = [
    'disksize',
    'maxupspeed',
    'maxdlspeed',
    'maxdlcount',
    'site_ratio_target',
    'min_seed_hours',
    'seed_time',
    'hr_seed_time',
    'hr_seed_ratio',
    'seed_ratio',
    'seed_size',
    'download_time',
    'seed_avgspeed',
    'seed_inactivetime',
    'up_speed',
    'dl_speed',
    'auto_archive_days',
  ]
  const optionalText = [
    'cron',
    'active_time_range',
    'include',
    'exclude',
    'size',
    'seeder',
    'pubtime',
    'delete_size_range',
    'save_path',
    'delete_except_tags',
    'qb_category',
  ]
  nullableNumbers.forEach(key => {
    const value = result[key] === '' || result[key] === null ? null : Number(result[key])
    result[key] = value === 0 && key !== 'hr_seed_ratio' ? null : value
  })
  optionalText.forEach(key => {
    result[key] = String(result[key] || '').trim() || null
  })
  result.site_id = Number(result.site_id)
  result.brush_interval = Number(result.brush_interval || 10)
  result.check_interval = Number(result.check_interval || 5)
  result.timezone_offset = Number(result.timezone_offset || 0)
  return result
}

/** 把全局设置中的空值、零值和正数标准化为后端请求类型。 */
export function normalizeSettings(settings = {}) {
  const result = { ...(settings || {}) }
  const limitFields = ['global_min_seed_hours', 'global_inactive_hours', 'global_seed_ratio', 'global_seed_size', 'global_download_time', 'global_seed_avgspeed', 'global_hr_seed_time', 'global_disksize', 'global_maxdlcount', 'global_maxupspeed', 'global_maxdlspeed']
  limitFields.forEach(key => {
    const value = Number(result[key] || 0)
    result[key] = value > 0 ? value : null
  })
  result.global_proxy_delete = Boolean(result.global_proxy_delete)
  result.global_delete_size_range = result.global_proxy_delete
    ? String(result.global_delete_size_range || '').trim() || null
    : null
  return result
}

/** 将字节数格式化为适合紧凑界面展示的容量文本。 */
export function formatBytes(value) {
  const bytes = Number(value || 0)
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const number = bytes / 1024 ** index
  return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)} ${units[index]}`
}

/** 将时间值格式化为当前界面使用的月日与时分。 */
export function formatDateTime(value) {
  if (!value) return '暂无'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

/** 计算一次运行记录的秒级耗时。 */
export function formatDuration(startedAt, finishedAt) {
  if (!startedAt || !finishedAt) return '-'
  const seconds = Math.max(Math.round((new Date(finishedAt) - new Date(startedAt)) / 1000), 0)
  return `${seconds} 秒`
}

/** 返回任务状态对应的中文文本、主题色和图标。 */
export function taskStateMeta(state) {
  const states = {
    running: { text: '运行中', color: 'success', icon: 'mdi-check-circle-outline' },
    brush: { text: '正在刷新', color: 'primary', icon: 'mdi-sync' },
    check: { text: '正在检查', color: 'info', icon: 'mdi-progress-check' },
    paused: { text: '已暂停', color: 'secondary', icon: 'mdi-pause-circle-outline' },
    waiting: { text: '等待时段', color: 'warning', icon: 'mdi-clock-outline' },
    waiting_ratio: { text: '待刷流', color: 'warning', icon: 'mdi-target' },
    ratio_unavailable: { text: '等待数据', color: 'info', icon: 'mdi-database-clock-outline' },
    disabled: { text: '插件停用', color: 'secondary', icon: 'mdi-stop-circle-outline' },
    error: { text: '运行异常', color: 'error', icon: 'mdi-alert-circle-outline' },
  }
  return states[state] || states.running
}

/** 根据已下载量和总大小计算种子完成百分比。 */
export function torrentProgress(item) {
  const size = Number(item?.size || 0)
  if (!size) return 0
  return Math.min(Math.round((Number(item.downloaded || 0) * 100) / size), 100)
}

/** Keep torrent status independent from deletion policy. */
export function torrentStateLabel(item) {
  if (item.deleted) return '已删除'
  const progress = torrentProgress(item)
  return progress >= 100 ? '做种中' : `下载中 ${progress}%`
}

/** Task values replace corresponding global fields; blanks inherit. */
export function effectiveDeleteSettings(settings = {}, task = {}) {
  const result = { ...settings }
  for (const key of ['seed_ratio', 'seed_size', 'download_time', 'seed_avgspeed', 'hr_seed_time', 'hr_seed_ratio', 'hr_ratio_basis', 'del_no_free', 'delete_except_tags']) {
    if (task[key] !== null && task[key] !== undefined && task[key] !== '') result[`global_${key}`] = task[key]
  }
  result.global_min_seed_hours = task.min_seed_hours ?? task.seed_time ?? settings.global_min_seed_hours
  if (task.seed_inactivetime != null && task.seed_inactivetime !== '') result.global_inactive_hours = Number(task.seed_inactivetime) / 60
  return result
}

/** Full explanation used by tooltips and mobile cards. */
export function globalDeleteRules(settings = {}, task = {}) {
  settings = effectiveDeleteSettings(settings, task)
  const rules = []
  const hours = Number(settings.global_min_seed_hours || 0)
  if (hours > 0) rules.push(`累计做种 ${hours} 小时`)
  if (Number(settings.global_inactive_hours) > 0) rules.push(`未活动 ${settings.global_inactive_hours} 小时`)
  if (Number(settings.global_seed_ratio) > 0) rules.push(`分享率达到 ${settings.global_seed_ratio}`)
  if (Number(settings.global_seed_size) > 0) rules.push(`上传量达到 ${settings.global_seed_size} GB`)
  if (Number(settings.global_download_time) > 0) rules.push(`未完成下载超过 ${settings.global_download_time} 小时`)
  if (Number(settings.global_seed_avgspeed) > 0) rules.push(`平均上传速度低于 ${settings.global_seed_avgspeed} KB/s`)
  if (settings.global_del_no_free) rules.push('促销过期且下载未完成')
  let text = rules.join(' 或 ') || '未配置普通种子条件'
  text = `触发条件：${text}。保护标签、容量和共享文件检查须同时通过，H&R 标签由助手维护`
  text += '。共享文件的每个关联任务均须分别达标（且关系），不合并时间或上传量，任意任务未达标或受保护则整组保留'
  return text
}

/** Compact summary follows the configured criteria, without rounding durations. */
export function globalDeleteSummary(settings = {}, task = {}) {
  settings = effectiveDeleteSettings(settings, task)
  const duration = value => Number(value) % 24 === 0 ? `${Number(value) / 24}天` : `${Number(value)}小时`
  const rules = []
  const hours = Number(settings.global_min_seed_hours || 0)
  if (hours > 0) rules.push(duration(hours))
  if (Number(settings.global_inactive_hours) > 0) rules.push(`闲置${duration(settings.global_inactive_hours)}`)
  if (Number(settings.global_seed_ratio) > 0) rules.push(`分享率≥${settings.global_seed_ratio}`)
  if (Number(settings.global_seed_size) > 0) rules.push(`上传≥${settings.global_seed_size}GB`)
  if (Number(settings.global_download_time) > 0) rules.push(`下载超时${duration(settings.global_download_time)}`)
  if (Number(settings.global_seed_avgspeed) > 0) rules.push(`均速≤${settings.global_seed_avgspeed}KB/s`)
  if (settings.global_del_no_free) rules.push('促销过期')
  let summary = rules.slice(0, 2).join('或')
  if (rules.length > 2) summary += `等${rules.length}项`
  if (!summary) summary = '按容量清理'
  return `全局托管 · ${summary}`
}

/** 拖动插入目标位置；筛选时仅重排可见任务，隐藏任务保持原位。 */
export function orderedIds(tasks, from, to, visible = tasks) {
  const ids = visible.map(t => t.id), a = ids.indexOf(from), b = ids.indexOf(to)
  if (a < 0 || b < 0 || a === b) return tasks.map(t => t.id)
  ids.splice(b, 0, ids.splice(a, 1)[0])
  const selected = new Set(ids)
  let index = 0
  return tasks.map(t => selected.has(t.id) ? ids[index++] : t.id)
}
