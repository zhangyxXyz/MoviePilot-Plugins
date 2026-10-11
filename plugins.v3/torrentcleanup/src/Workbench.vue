<script setup>
import { computed, inject, onMounted, onUnmounted, ref, watch } from 'vue'
import TaskEditor from './TaskEditor.vue'
import { orderedIds, actionName, canExecute, dateTime, errorText, formatBytes, modeName, stateName, tags, unwrap } from './utils'

const props = defineProps({ api: { type: Object, default: () => ({}) }, pluginId: { type: String, default: 'TorrentCleanup' } })
const emit = defineEmits(['action'])
const state = ref({ config: { enabled: false, tasks: [], sites: [] }, options: { sites: [], downloaders: [] }, plans: {}, runs: [], runtime: {}, bindings: [] })
const selected = ref(''), tab = ref('preview'), loading = ref(false), saving = ref(false), error = ref(''), info = ref('')
const downloaderFilter = ref('')
const autoLoaded = new Set()
const editor = ref(false), editorTask = ref({}), settingsOpen = ref(false), settingsDraft = ref({ sites: [] })
const onlyEligible = ref(false), confirmOpen = ref(false), deleteOpen = ref(false), bindOpen = ref(false)
const bindDraft = ref({}), now = ref(Date.now() / 1000), toast = inject('moviepilot:toast', null)
const hrConfirm = ref(null)
const dragged = ref('')
async function resolveHR() {
  try { await post('/hr/resolve', hrConfirm.value); hrConfirm.value = null; notify('确认已保存，已排队更新保护状态'); await refresh(true) } catch {}
}
let timer, lastLoad = 0
const base = computed(() => `plugin/${props.pluginId}`)
const tasks = computed(() => state.value.config.tasks || [])
const visibleTasks = computed(() => tasks.value.filter(t => !downloaderFilter.value || t.downloader === downloaderFilter.value))
const showPreview = computed(() => state.value.config.show_preview !== false)
const task = computed(() => tasks.value.find(t => t.id === selected.value))
const plan = computed(() => state.value.plans?.[selected.value])
const runtime = computed(() => state.value.runtime?.[selected.value] || {})
const busy = computed(() => ['queued', 'running'].includes(runtime.value.status))
const elapsed = computed(() => runtime.value.started_at ? Math.max(0, Math.floor((runtime.value.finished_at || now.value) - runtime.value.started_at)) : 0)
const progressPercent = computed(() => runtime.value.total > 0 ? Math.floor(100 * (runtime.value.completed || 0) / runtime.value.total) : 0)
const planExpired = computed(() => plan.value && now.value - plan.value.created_at > 900)
const items = computed(() => (plan.value?.items || []).filter(i => !onlyEligible.value || i.eligible))
const runs = computed(() => state.value.runs.filter(r => r.task_id === selected.value))
const executable = computed(() => canExecute(state.value.config, task.value, plan.value, busy.value, now.value))
const siteName = id => state.value.options.sites.find(s => s.value === id)?.title || `站点 ${id}`
const currentBinding = computed(() => state.value.bindings.find(b => b.hash === bindDraft.value.hash && b.site_id === bindDraft.value.site_id))
const bindingPolicy = computed(() => state.value.config.sites.find(s => s.site_id === bindDraft.value.site_id))
watch(() => bindDraft.value.site_id, () => { bindDraft.value.url = currentBinding.value?.url || '' })
const resultName = s => ({ confirmed: '已确认', unconfirmed: '待核实', failed: '执行失败', skipped: '已跳过' }[s] || s)
const headers = [{ title: '种子', key: 'name' }, { title: '累计做种', key: 'seed_hours' }, { title: '分享率', key: 'ratio' }, { title: '判断结果', key: 'reasons', sortable: false }, { title: '站点核验', key: 'verification', sortable: false }]
watch(downloaderFilter, () => { if (!visibleTasks.value.some(t => t.id === selected.value)) selected.value = visibleTasks.value[0]?.id || '' })
watch([selected, showPreview], () => { tab.value = showPreview.value ? 'preview' : 'history'; ensureSnapshot() })
async function ensureSnapshot() {
  const id = selected.value, key = `${id}:${state.value.config.revision}`
  if (!showPreview.value || !id || plan.value || busy.value || autoLoaded.has(key) || state.value.error) return
  autoLoaded.add(key)
  try { unwrap(await props.api.post(`${base.value}/tasks/${id}/preview`, {})); await refresh(true) }
  catch (e) { error.value = errorText(e) }
}

function notify(message) { info.value = message; toast?.success?.(message) }
async function refresh(silent = false) {
  if (loading.value) return
  loading.value = true
  try {
    state.value = unwrap(await props.api.get(`${base.value}/status`))
    if (!visibleTasks.value.some(t => t.id === selected.value)) selected.value = visibleTasks.value[0]?.id || ''
    if (!silent) error.value = ''
    lastLoad = Date.now()
  } catch (e) { error.value = errorText(e) } finally { loading.value = false }
  if (!error.value) ensureSnapshot()
}
async function post(path, payload = {}) {
  saving.value = true; error.value = ''
  try { const result = unwrap(await props.api.post(`${base.value}${path}`, payload)); emit('action'); return result }
  catch (e) { error.value = errorText(e); toast?.error?.(error.value); info.value = error.value; throw e } finally { saving.value = false }
}
function openEditor(value = {}) { editorTask.value = value; editor.value = true }
async function sortTasks(from, to) {
  if (saving.value || !from || from === to) return
  const ids = orderedIds(tasks.value, from, to, visibleTasks.value)
  try { await post('/tasks/order', { ids }); await refresh(true); notify('顺序已保存，预览和观察记录保持不变') } catch {}
}
function moveTask(id, direction) {
  const neighbor = visibleTasks.value[visibleTasks.value.findIndex(t => t.id === id) + direction]
  if (neighbor) return sortTasks(id, neighbor.id)
}
function startTaskDrag(event, id) {
  dragged.value = id
  event.dataTransfer?.setData('text/plain', id)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move'
}
async function saveTask(payload) {
  try { const result = await post('/tasks', payload); editor.value = false; selected.value = result.id; await refresh(); notify(plan.value ? '任务已保存，原预览与观察记录已保留' : '任务已保存，判断条件变化后需要重新预览') } catch {}
}
async function preview() {
  try { await post(`/tasks/${selected.value}/preview`); tab.value = 'preview'; await refresh(); notify('预览检查已排队') } catch {}
}
async function execute() {
  if (!executable.value) return
  try { await post(`/tasks/${selected.value}/execute`, { plan_id: plan.value.id }); confirmOpen.value = false; tab.value = 'history'; await refresh() } catch {}
}
async function reconcile() {
  try {
    const result = await post(`/tasks/${selected.value}/reconcile`)
    await refresh()
    notify(`核对完成：确认 ${result.confirmed} 项，仍待核实 ${result.pending} 项`)
  } catch {}
}
async function removeTask() {
  saving.value = true
  try { unwrap(await props.api.delete(`${base.value}/tasks/${selected.value}`)); deleteOpen.value = false; await refresh(); notify('已删除策略，下载器任务未改变') }
  catch (e) { error.value = errorText(e) } finally { saving.value = false }
}
async function openSettings() {
  await refresh()
  if (state.value.error) { error.value = state.value.error; return }
  const existing = state.value.config.sites || []
  settingsDraft.value = { enabled: state.value.config.enabled, show_sidebar: state.value.config.show_sidebar !== false, show_preview: showPreview.value,
    hr_enabled: state.value.config.hr_enabled || false, hr_downloaders: [...(state.value.config.hr_downloaders || [])],
    hr_check_minutes: state.value.config.hr_check_minutes || 15, hr_auto_release: state.value.config.hr_auto_release || false,
    sites: state.value.options.sites.map(s => JSON.parse(JSON.stringify(existing.find(p => p.site_id === s.value) || {
      site_id: s.value, tracker_hosts: [], hr_mode: 'unknown', min_seed_hours: 168, verification: 'unsupported',
      auto_discover: true, search_url_template: '', detail_path: '/details.php', detail_id_param: 'id',
      check_interval_hours: 6, request_gap_seconds: 2, max_requests_per_hour: 20, max_detail_candidates: 5,
      detail_requests_per_hour: 100, search_requests_per_day: 1000, torrent_downloads_per_hour: 100, torrent_downloads_per_day: 1000, error_cache_minutes: 10,
      allow_torrent_verification: false, torrent_download_path: '/download.php?id={id}', torrent_download_hosts: [],
      hr_detection: 'marked', hr_required_hours: 48, hr_buffer_hours: 24, hr_deadline_days: 14, hr_seed_ratio: 0, hr_ratio_basis: 'client',
    }))) }
  settingsOpen.value = true
}
async function saveSettings() {
  const payload = JSON.parse(JSON.stringify(settingsDraft.value))
  payload.hr_check_minutes = Number(payload.hr_check_minutes)
  for (const site of payload.sites) {
    for (const key of ['min_seed_hours', 'check_interval_hours', 'request_gap_seconds', 'max_requests_per_hour', 'max_detail_candidates', 'detail_requests_per_hour', 'search_requests_per_day', 'torrent_downloads_per_hour', 'torrent_downloads_per_day', 'error_cache_minutes', 'hr_required_hours', 'hr_buffer_hours', 'hr_deadline_days', 'hr_seed_ratio']) site[key] = Number(site[key])
  }
  try { await post('/settings', payload); settingsOpen.value = false; await refresh(); notify('设置已保存，只有判断依据变化才会清空预览') } catch {}
}
function openBinding(item) {
  error.value = ''
  bindDraft.value = { hash: item.hash, downloader: task.value.downloader, site_id: item.site_ids[0], url: '' }
  bindDraft.value.url = currentBinding.value?.url || ''
  bindOpen.value = true
}
async function bind() {
  try { const result = await post('/bindings', bindDraft.value); await refresh(); bindDraft.value.url = result.url; notify(`详情页已核实并保存（${result.source || '已有关联'}）${result.cached ? '，本次使用缓存' : ''}`) } catch {}
}
onMounted(async () => {
  await refresh()
  timer = setInterval(() => {
    now.value = Date.now() / 1000
    const running = Object.values(state.value.runtime || {}).some(v => ['queued', 'running'].includes(v.status))
    if (!editor.value && !settingsOpen.value && !bindOpen.value && Date.now() - lastLoad >= (running ? 5000 : 30000)) refresh(true)
  }, 1000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="cleanup-workbench">
    <header class="cleanup-header"><div class="d-flex align-center ga-3"><VAvatar color="primary" variant="tonal" rounded="lg" size="44"><VIcon icon="mdi-delete-outline" /></VAvatar><div><h2 class="text-h5 font-weight-bold">种子清理</h2><div class="text-body-2 text-medium-emphasis">按站点保留 · 有依据地清理</div></div></div>
      <div class="d-flex align-center ga-2 flex-wrap">
        <VBtn icon="mdi-refresh" variant="text" aria-label="刷新" :loading="loading" @click="refresh()" /><VBtn variant="outlined" prepend-icon="mdi-cog-outline" @click="openSettings">站点与保护</VBtn></div>
    </header>
    <VAlert v-if="error || state.error" type="error" variant="tonal" class="mb-4" closable @click:close="error = ''; state.error = ''">{{ error || state.error }}</VAlert>
    <VAlert v-if="!state.config.enabled" type="info" variant="tonal" class="mb-4" density="compact">当前仅可预览和核验，启用“站点与保护”的总开关后，已启用任务才按计划执行。</VAlert>
    <div class="cleanup-layout">
      <aside class="cleanup-task-list">
        <VSelect v-model="downloaderFilter" :items="[{ title: '全部下载器', value: '' }, ...state.options.downloaders]" label="按下载器查看" density="compact" hide-details class="mb-3" />
        <div v-for="(entry, index) in visibleTasks" :key="entry.id" class="task-row" :class="{ selected: selected === entry.id }" :draggable="!saving" @dragstart="startTaskDrag($event, entry.id)" @dragend="dragged = ''" @dragover.prevent @drop.prevent="sortTasks(dragged, entry.id); dragged = ''">
        <button class="task-button" :class="{ selected: selected === entry.id }" @click="selected = entry.id; onlyEligible = false">
          <div class="d-flex align-center justify-space-between ga-2"><VIcon icon="mdi-drag-vertical" size="18" class="task-drag-handle" /><strong class="flex-grow-1" :title="entry.name">{{ entry.name }}</strong><VChip size="x-small" :color="entry.enabled ? 'success' : 'default'">{{ entry.enabled ? '启用' : '停用' }}</VChip></div>
          <div class="rail-tags"><span class="rail-pill rail-accent" :title="entry.downloader">{{ entry.downloader }}</span><span class="rail-pill" :class="entry.action.startsWith('delete')?'rail-warn':'rail-neutral'">{{ actionName(entry.action) }}</span></div>
          <div class="rail-details"><div class="rail-row"><span class="rail-pill rail-label">生效站点</span><span class="rail-pill" :title="entry.all_sites ? '全部站点' : entry.site_ids.map(siteName).join('、') || '未选择'">{{ entry.all_sites ? '全部站点' : entry.site_ids.map(siteName).join('、') || '未选择' }}</span></div><div class="rail-row"><span class="rail-pill rail-label">执行周期</span><span class="rail-pill" :title="entry.cron">{{ entry.cron }}</span></div></div>
        </button>
        <div class="task-order"><VBtn icon="mdi-arrow-up" size="x-small" variant="text" :aria-label="`上移 ${entry.name}`" :title="`上移 ${entry.name}`" :disabled="saving || index === 0" @click="moveTask(entry.id, -1)" /><VBtn icon="mdi-arrow-down" size="x-small" variant="text" :aria-label="`下移 ${entry.name}`" :title="`下移 ${entry.name}`" :disabled="saving || index === visibleTasks.length - 1" @click="moveTask(entry.id, 1)" /><VBtn icon="mdi-pencil" size="x-small" variant="text" aria-label="编辑任务" @click.stop="openEditor(entry)" /></div>
        </div>
        <div v-if="!tasks.length" class="pa-5 text-body-2 text-medium-emphasis">先添加一个任务，选择下载器和需要管理的站点。</div>
        <VBtn block variant="tonal" color="primary" prepend-icon="mdi-plus" class="mt-3" @click="openEditor()">新建任务</VBtn>
      </aside>
      <main class="cleanup-detail">
        <template v-if="task">
          <div class="detail-header"><div><div class="text-overline text-medium-emphasis">{{ task.downloader }}</div><h3 class="text-h6">{{ task.name }}</h3><p class="text-body-2 text-medium-emphasis mt-1">{{ task.all_sites ? '不限站点' : task.site_ids.map(siteName).join('、') }} · {{ task.cron }}</p></div>
            <div class="d-flex ga-1"><VBtn variant="text" prepend-icon="mdi-pencil-outline" @click="openEditor(task)">编辑</VBtn><VBtn icon="mdi-delete-outline" aria-label="删除策略" variant="text" @click="deleteOpen = true" /></div>
          </div>
          <div v-if="showPreview" class="summary-grid"><div><span>候选任务</span><strong>{{ plan?.candidate_count ?? '—' }} <small>个</small></strong></div><div><span>候选体积</span><strong>{{ plan ? formatBytes(plan.candidate_bytes) : '—' }}</strong></div><div><span>最低累计做种</span><strong>{{ task.mode === 'conditions' && !task.filter_seed ? '不限' : task.min_seed_hours }} <small v-if="task.mode !== 'conditions' || task.filter_seed">小时</small></strong></div><div><span>满足条件后</span><strong class="action-summary">{{ actionName(task.action) }}</strong></div></div>
          <div class="policy-summary"><VIcon icon="mdi-shield-check-outline" size="18" color="primary" /><span>分享率 {{ task.mode === 'conditions' && !task.filter_ratio ? '不限' : '≥ ' + task.min_ratio }} · 每轮 ≤ {{ task.max_count }} 个 / {{ task.max_gb }} GiB · {{ task.protect_tags.join('、') }} 受保护</span></div>
          <section class="cleanup-content-card"><div class="tab-toolbar"><VTabs v-model="tab" color="primary"><VTab v-if="showPreview" value="preview">候选预览</VTab><VTab value="history">执行记录 <VChip class="ml-2" size="x-small">{{ runs.length }}</VChip></VTab></VTabs><div v-if="showPreview" class="d-flex ga-2 flex-wrap"><VBtn variant="tonal" color="primary" prepend-icon="mdi-magnify" :loading="busy" :disabled="saving" @click="preview">刷新并检查</VBtn><VBtn v-if="task.action !== 'preview'" :disabled="!executable" color="primary" @click="confirmOpen = true">执行本次计划</VBtn></div></div>
          <div v-if="busy || runtime.started_at" class="scan-progress" aria-live="polite">
            <div class="d-flex justify-space-between ga-3 flex-wrap"><strong>{{ runtime.message }}</strong><span v-if="runtime.started_at">耗时 {{ Math.floor(elapsed / 60) }} 分 {{ elapsed % 60 }} 秒</span></div>
            <VProgressLinear v-if="busy" class="my-3" :indeterminate="runtime.total == null" :model-value="progressPercent" color="primary" rounded height="8" />
            <div v-if="runtime.total != null" class="mt-2">已检查 {{ runtime.completed || 0 }} / {{ runtime.total }} 个 · {{ progressPercent }}% · 站点请求 {{ runtime.request_count || 0 }} 次 · 无需站点请求 {{ runtime.skipped_checks || 0 }} 项 · 复用缓存 {{ runtime.cached_checks || 0 }} 次 · 限流暂缓 {{ runtime.limited_checks || 0 }} 次</div>
            <div v-if="busy && runtime.current_name" class="scan-current mt-2">当前：{{ runtime.current_name }}</div>
            <div v-if="busy" class="text-caption text-medium-emphasis mt-2">每 5 秒更新进度，完成整轮后生成预览。站点请求需等待网络响应和配置的请求间隔。</div>
          </div>
          <VAlert v-if="runtime.status === 'failed'" type="error" variant="tonal" class="mt-4">{{ runtime.message }}</VAlert>
          <div v-if="showPreview && tab === 'preview'">
            <div v-if="plan" class="preview-caption"><span>检查时间：{{ dateTime(plan.created_at) }} · {{ busy ? '正在刷新，以下为上次结果' : planExpired ? '历史预览仍可查看，执行前需重新检查（已超过 15 分钟）' : '执行前将再次复核' }}</span><VSwitch v-model="onlyEligible" label="只看可处理" hide-details density="compact" color="primary" /></div>
            <VDataTable v-if="plan" :headers="headers" :items="items" :items-per-page="10" item-value="hash" density="comfortable" no-data-text="没有符合当前视图的任务" class="cleanup-table">
              <template #item.name="{ item }"><div class="torrent-name">{{ item.name }}</div><div class="text-caption text-medium-emphasis">{{ formatBytes(item.size) }} · {{ item.site_ids.map(siteName).join('、') || '站点未识别' }}<div>{{ stateName(item.client_state, state.options.downloaders.find(d => d.value === task.downloader)?.type) }} · 下载进度 {{ item.download_progress ?? '未知' }}%</div><div v-if="item.error_message">错误信息：{{ item.error_message }}</div></div></template>
              <template #item.seed_hours="{ item }">{{ item.seed_hours == null ? '未知' : `${item.seed_hours} h` }}</template>
              <template #item.ratio="{ item }">{{ item.ratio == null ? '未知' : Number(item.ratio).toFixed(2) }}</template>
              <template #item.reasons="{ item }"><VChip :color="item.eligible ? 'success' : 'default'" size="x-small" variant="tonal">{{ item.eligible ? '可处理' : '保留' }}</VChip><div class="reason-list">{{ item.reasons.join('，').replaceAll('；', '，') }}</div></template>
              <template #item.verification="{ item }"><div v-if="!item.evidence?.length" class="text-caption text-medium-emphasis">{{ task.mode === 'download_error' ? '下载错误策略，仅检查下载器状态，无需站点详情核验。' : task.mode === 'retention' ? '按做种时长等条件筛选，无需站点详情核验。' : !task.verify_invalid ? '未勾选站点失效复核，本任务按下载器条件筛选。' : '尚无站点核验证据，请检查站点映射。' }}</div><div v-for="e in item.evidence" :key="e.site_id" class="text-caption mb-1">{{ e.reason.replaceAll('；', '，') }}<div v-if="e.detail_url"><a :href="e.detail_url" target="_blank" rel="noopener noreferrer">查看详情页</a> · {{ e.source }} · {{ dateTime(e.checked_at) }}</div></div><VBtn v-if="item.site_ids.length" variant="text" size="small" color="primary" prepend-icon="mdi-link-variant" @click="openBinding(item)">自动查找 / 核验</VBtn></template>
            </VDataTable>
            <div v-else class="empty-state"><VIcon icon="mdi-text-box-search-outline" size="48" color="primary" /><h4>{{ busy ? '正在读取下载器任务' : '暂未取得下载器结果' }}</h4><p>进入页面自动读取下载器，仅疑似失效的种子请求站点核验，下载错误按下载器状态判断。</p><VBtn variant="tonal" color="primary" :loading="busy" @click="preview">重新读取</VBtn></div>
          </div>
          <div v-else class="history-area"><VBtn class="mb-4" variant="tonal" :loading="saving" :disabled="busy" prepend-icon="mdi-refresh" @click="reconcile">核对待确认结果</VBtn><VExpansionPanels v-if="runs.length" variant="accordion"><VExpansionPanel v-for="run in runs" :key="run.id"><VExpansionPanelTitle><span>{{ dateTime(run.started_at) }} · {{ actionName(run.action) }}</span><VSpacer /><span class="text-caption mr-3">确认 {{ run.items.filter(i => i.status === 'confirmed').length }} / {{ run.items.length }}</span></VExpansionPanelTitle><VExpansionPanelText><VAlert v-if="run.summary" :type="run.status === 'failed' ? 'error' : 'info'" variant="tonal" class="mb-3">{{ run.summary }}</VAlert><div v-for="item in run.items" :key="item.hash" class="history-item"><VChip size="small" :color="item.status === 'confirmed' ? 'success' : 'warning'" variant="tonal">{{ resultName(item.status) }}</VChip><div><div class="torrent-name">{{ item.name }}</div><div class="text-caption text-medium-emphasis">{{ item.reason?.replaceAll('；', '，') }}</div></div></div><p v-if="run.status === 'running'" class="text-body-2">正在处理，结果会自动刷新。</p></VExpansionPanelText></VExpansionPanel></VExpansionPanels><div v-else class="empty-state"><VIcon icon="mdi-history" size="48" color="primary" /><h4>还没有执行记录</h4><p>处理任务后，这里会保留结果和下载器确认情况。</p></div></div>
        </section></template>
        <div v-else class="empty-state welcome"><VIcon icon="mdi-delete-outline" size="56" color="primary" /><h3>把清理规则分开管理</h3><p>为不同下载器和站点建立独立任务。<br>从预览开始，确认范围后再启用处理动作。</p><VBtn color="primary" prepend-icon="mdi-plus" @click="openEditor()">新建第一个任务</VBtn></div>
      </main>
    </div>
    <TaskEditor v-model="editor" :task="editorTask" :options="state.options" :saving="saving" @save="saveTask" />
    <VDialog v-model="settingsOpen" max-width="1080" scrollable><VCard><VToolbar color="transparent"><VToolbarTitle>站点适配与保护</VToolbarTitle><VSpacer /><VBtn color="primary" :loading="saving" @click="saveSettings">保存</VBtn><VBtn icon="mdi-close" aria-label="关闭站点设置" @click="settingsOpen = false" /></VToolbar><VDivider /><VCardText>
      <VAlert type="info" variant="tonal" class="mb-4">站点列表实时读取 MoviePilot 的“站点管理”（当前 {{ state.options.sites.length }} 个）。新增或修改站点后重新打开本页即可同步，网站地址与登录凭据仍在站点管理中维护。</VAlert>
      <div class="d-flex ga-6 flex-wrap align-start"><VSwitch v-model="settingsDraft.enabled" label="启用定时任务和手动执行" hide-details inset color="primary" /><VSwitch v-model="settingsDraft.show_sidebar" label="显示侧栏入口" hide-details inset color="primary" /><VSwitch v-model="settingsDraft.show_preview" label="显示候选预览" hide-details inset color="primary" /></div>
      <VAlert type="info" variant="tonal" class="my-4">H&R 识别与解除条件在助手中配置，清理任务各自选择保护标签，选用 H&R 标签后会跟随改名。</VAlert>
      <p class="text-caption text-medium-emphasis mt-2 mb-4">关闭候选预览不影响定时执行，结果可在执行记录中查看。</p>
      <VExpansionPanels variant="accordion"><VExpansionPanel v-for="site in settingsDraft.sites" :key="site.site_id"><VExpansionPanelTitle>{{ siteName(site.site_id) }}<VChip class="ml-2" size="x-small" :color="site.verification === 'unsupported' ? 'warning' : 'success'">{{ site.verification === 'unsupported' ? '详情未适配' : site.verification === 'mteam' ? '馒头 API 已配置' : 'NexusPHP 已配置' }}</VChip></VExpansionPanelTitle><VExpansionPanelText><VRow>
        <VCol cols="12"><p class="text-caption text-medium-emphasis">MoviePilot 已有认证：{{ state.options.sites.find(s => s.value === site.site_id)?.auth_types?.join(' / ') || '未配置' }}。已有认证不代表本站已接入详情核验适配。</p></VCol>
        <VCol cols="12"><div class="text-subtitle-1 mb-2">域名与身份</div><p class="text-body-2 text-medium-emphasis">网站：{{ state.options.sites.find(s => s.value === site.site_id)?.domain }}。网站地址、Cookie / API Key、请求头和代理沿用 MoviePilot 站点管理，无需重复填写凭据。</p></VCol>
        <VCol cols="12"><VCombobox v-model="site.tracker_hosts" multiple chips closable-chips label="关联 Tracker 域名" hint="网站子域自动匹配，Tracker 域名不同时在此补充，仅填主机名，可配置多个，不填完整链接或 passkey。" persistent-hint /></VCol>
        <VCol cols="12"><VSelect v-model="site.verification" label="站点详情复核" :items="[{ title: '未适配：失效结果保持未知', value: 'unsupported' }, { title: 'NexusPHP：使用 MoviePilot 中的 Cookie', value: 'nexusphp' }, { title: '馒头 API：复用 MoviePilot API Key（x-api-key）', value: 'mteam' }]" /></VCol>
        <VCol cols="12"><VBtn variant="tonal" size="small" @click="Object.assign(site, { verification: 'nexusphp', auto_discover: true, search_url_template: '/torrents.php?search={keyword}&search_area=0&search_mode=0&incldead=0', detail_path: '/details.php', detail_id_param: 'id' })">填入 NexusPHP 标准规则</VBtn><p class="text-caption text-medium-emphasis mt-2">适用于兼容 NexusPHP 的站点（已验证 CARPT、观众），填入后仍可修改。馒头使用独立 API，不能套用此规则。</p></VCol>
        <VCol v-if="site.verification === 'mteam'" cols="12"><VTextField v-model="site.api_url" label="API 服务地址（可留空自动推导）"  :placeholder="state.options.sites.find(s => s.value === site.site_id)?.api_url || 'https://api.m-team.cc/api'" hint="留空复用宿主索引 API，凭据读取站点管理，鉴权失败保持未知，缺少 Hash 可启用下方种子文件核验。" persistent-hint /></VCol>
        <template v-if="['nexusphp', 'mteam'].includes(site.verification)">
          <VCol cols="12"><VDivider class="mb-4" /><div class="text-subtitle-1">自动查找规则</div><VSwitch v-model="site.auto_discover" label="无已有关联时自动查找详情页" color="primary" hide-details /><p class="text-body-2 text-medium-emphasis mt-2">先查最近 500 条下载记录和下载器备注，再使用下面的搜索模板。任何候选都必须核实详情页 Hash，搜索无结果不会判定失效。</p></VCol>
          <VCol v-if="site.verification === 'nexusphp'" cols="12"><VTextField v-model="site.search_url_template" label="站内搜索模板（留空跳过搜索）" placeholder="/torrents.php?search={hash}" hint="填写本站相对路径，{hash} 为 Hash，{name} 为完整名称，{keyword} 为标题关键词，参数须受本站支持" persistent-hint /></VCol>
          <VCol v-if="site.verification === 'nexusphp'" cols="12" md="6"><VTextField v-model="site.detail_path" label="详情页路径" placeholder="/details.php" /></VCol>
          <VCol v-if="site.verification === 'nexusphp'" cols="12" md="6"><VTextField v-model="site.detail_id_param" label="详情页 ID 参数名" placeholder="id" /></VCol>
          <VCol cols="12"><VDivider class="mb-4" /><div class="text-subtitle-1">访问频率</div><p class="text-body-2 text-medium-emphasis mt-2">任务按各自 Cron 运行，缓存和请求限额按站点共享，重复预览或核验不会绕过缓存。执行前重新获取证据，仍受请求限额约束。</p></VCol>
          <VCol cols="12" md="6"><VTextField v-model="site.check_interval_hours" type="number" min="1" max="168" label="存在状态缓存有效期（小时）" /></VCol>
          <VCol cols="12" md="6"><VTextField v-model="site.request_gap_seconds" type="number" min="1" max="30" label="本站请求最小间隔（秒）" /></VCol>
          <VCol cols="12" md="6"><VTextField v-model="site.max_requests_per_hour" type="number" min="1" max="120" label="所有请求合计每小时预算（包括重定向）" /></VCol>
          <VCol cols="12" md="6"><VTextField v-model="site.max_detail_candidates" type="number" min="1" max="10" label="每个来源最多核验候选页数" /></VCol>
          <VCol cols="12"><VDivider class="my-3" /><div class="text-subtitle-1">按请求类别限制配额</div><p class="text-caption mt-2">同站点所有任务共享配额，重启不清零，0 表示禁止该类请求。分类配额与总请求预算同时生效，仅统计本插件用量。</p></VCol>
          <VCol cols="12" md="6"><VTextField v-model="site.detail_requests_per_hour" type="number" min="0" label="详情每小时最多请求次数" /></VCol>
          <VCol cols="12" md="6"><VTextField v-model="site.search_requests_per_day" type="number" min="0" label="搜索滚动 24 小时最多请求次数" /></VCol>
          <VCol cols="12" md="6"><VTextField v-model="site.torrent_downloads_per_hour" type="number" min="0" label="种子文件每小时最多下载次数" /></VCol>
          <VCol cols="12" md="6"><VTextField v-model="site.torrent_downloads_per_day" type="number" min="0" label="种子文件滚动 24 小时最多下载次数" /></VCol>
          <VCol cols="12"><div v-for="q in state.quotas?.[site.site_id] || []" :key="q.title" class="text-caption mb-2">{{ q.title }}：已用 {{ q.used }} / {{ q.limit }}<span v-if="q.blocked">，{{ q.next_at ? '下次释放：' + dateTime(q.next_at) : '已禁止此类请求' }}</span></div></VCol>
          <VCol cols="12"><VDivider class="my-3" /><div class="text-subtitle-1">种子文件核验与缓存</div><VSwitch v-model="site.allow_torrent_verification" label="缺少 Hash 时允许下载种子文件核验身份" color="primary" hide-details /><p class="text-caption mt-3">下载 .torrent 计算 Hash，验证后的身份关联长期保存，不代表种子仍存在。仅在 Tracker 异常时复核站点，额度不足或下载失败则保持未知。</p></VCol>
          <VCol cols="12" md="6"><VTextField v-model="site.error_cache_minutes" type="number" min="1" max="1440" label="失败或未知结果退避时间（分钟）" /></VCol>
          <VCol v-if="site.verification === 'nexusphp'" cols="12"><VTextField v-model="site.torrent_download_path" label="种子下载路径模板" hint="填写本站相对路径，如 /download.php?id={id}，ID 从详情地址提取" persistent-hint /></VCol>
          <VCol cols="12"><VCombobox v-model="site.torrent_download_hosts" multiple chips closable-chips label="允许种子下载跳转的分发域名" hint="回车添加精确主机名，分发请求不携带站点 API Key 或 Cookie" persistent-hint /></VCol>
        </template>
      </VRow></VExpansionPanelText></VExpansionPanel></VExpansionPanels>
    </VCardText></VCard></VDialog>
    <VDialog v-model="confirmOpen" max-width="520"><VCard title="执行本次清理计划"><VCardText>将对本次预览中的 {{ plan?.candidate_count }} 个候选执行“{{ actionName(task?.action) }}”。执行前会重新检查，条件变化的任务会跳过。<VAlert v-if="task?.action === 'deletefile'" type="warning" variant="tonal" class="mt-4">该动作包含删除下载数据文件。</VAlert></VCardText><VCardActions><VSpacer /><VBtn @click="confirmOpen = false">取消</VBtn><VBtn color="primary" :loading="saving" :disabled="!executable" @click="execute">确认执行</VBtn></VCardActions></VCard></VDialog>
    <VDialog v-model="deleteOpen" max-width="480"><VCard title="删除清理策略"><VCardText>删除“{{ task?.name }}”的配置，保留种子和执行历史。</VCardText><VCardActions><VSpacer /><VBtn @click="deleteOpen = false">取消</VBtn><VBtn color="error" :loading="saving" @click="removeTask">删除策略</VBtn></VCardActions></VCard></VDialog>
    <VDialog v-model="bindOpen" max-width="680"><VCard title="详情识别与核验"><VCardText class="binding-fields">
      <VSelect v-model="bindDraft.site_id" :items="state.options.sites.filter(s => task?.site_ids.includes(s.value))" label="站点" hide-details />
      <VAlert v-if="currentBinding" type="success" variant="tonal"><div>已识别并保存 · {{ currentBinding.source }}</div><div class="text-caption mt-1">身份 Hash 核实：{{ dateTime(currentBinding.hash_verified_at || currentBinding.verified_at) }}。最近确认存在：{{ dateTime(currentBinding.verified_at) }}</div><a class="binding-url" :href="currentBinding.url" target="_blank" rel="noopener noreferrer">{{ currentBinding.url }}</a></VAlert>
      <VAlert v-else-if="!bindingPolicy || bindingPolicy.verification === 'unsupported'" type="warning" variant="tonal">请先在“站点与保护”配置核验适配，再自动查找详情。</VAlert>
      <VAlert v-else type="info" variant="tonal">点击“自动查找并保存”核实 Hash，成功后显示详情链接。</VAlert>
      <div><VTextField v-model="bindDraft.url" label="详情链接（自动识别后回填，也可手动填写）" hide-details /><p class="binding-help">留空时按本站规则自动查找，填写链接时核实该链接。只有 Hash 完全一致才保存关联。</p></div>
      <div class="binding-identity"><div class="text-caption text-medium-emphasis mb-2">种子 Hash</div><code>{{ bindDraft.hash }}</code></div>
      <VAlert v-if="error" type="error" variant="tonal">{{ error }}</VAlert>
    </VCardText><VCardActions><VSpacer /><VBtn @click="bindOpen = false">关闭</VBtn><VBtn color="primary" :loading="saving" :disabled="!bindingPolicy || bindingPolicy.verification === 'unsupported'" @click="bind">{{ bindDraft.url ? '核验并保存' : '自动查找并保存' }}</VBtn></VCardActions></VCard></VDialog>
    <VSnackbar :model-value="!!info" :timeout="3500" @update:model-value="v => { if (!v) info = '' }">{{ info }}</VSnackbar>
  </div>
</template>

<style scoped>
.task-drag-handle{cursor:grab;opacity:.6}

.task-row{display:flex;flex-direction:column;margin-bottom:12px;border:1px solid rgba(var(--v-theme-on-surface),.12);border-radius:12px;overflow:hidden;background:rgb(var(--v-theme-surface));cursor:pointer}.task-row.selected{border-color:rgb(var(--v-theme-primary));background:rgba(var(--v-theme-primary),.06)}.task-row .task-button{min-width:0;width:100%;margin-bottom:0;border:0;background:transparent}.task-order{display:flex;justify-content:flex-end;gap:4px;padding:0 8px 6px}
.scan-progress{margin-top:16px;padding:16px;border-radius:10px;background:rgba(var(--v-theme-primary),.06);font-size:13px;line-height:1.7}.scan-current{overflow-wrap:anywhere}
.binding-fields{display:flex;flex-direction:column;gap:22px;padding-top:12px!important}.binding-help{margin:10px 0 0;font-size:12px;line-height:1.7;color:rgba(var(--v-theme-on-surface),.65)}.binding-identity{border-top:1px solid rgba(var(--v-border-color),var(--v-border-opacity));padding-top:18px}.binding-identity code,.binding-url{overflow-wrap:anywhere;word-break:break-all}.binding-identity code{font-size:13px;line-height:1.8}.binding-url{display:block;margin-top:8px;font-size:13px}
.cleanup-workbench{padding:24px;color:rgb(var(--v-theme-on-surface));font-family:inherit}.cleanup-header{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:24px}.cleanup-layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:24px;min-height:560px}.cleanup-task-list,.cleanup-detail{border:1px solid rgba(var(--v-border-color),var(--v-border-opacity));border-radius:12px;background:rgb(var(--v-theme-surface))}.cleanup-task-list{padding:0;height:fit-content;border:0;border-radius:0;background:transparent}.list-caption{display:flex;align-items:center;justify-content:space-between;padding:8px 10px 16px;font-size:13px;color:rgba(var(--v-theme-on-surface),.6)}.task-button{display:block;width:100%;text-align:left;padding:16px 14px;border:1px solid transparent;border-radius:8px;margin-bottom:8px;color:inherit;transition:background .15s;overflow-wrap:anywhere}.task-button:hover{background:rgba(var(--v-theme-primary),.04)}.task-button.selected{background:transparent}.task-subtitle{font-size:12px;color:rgba(var(--v-theme-on-surface),.6);margin-top:7px}.status-dot{width:7px;height:7px;border-radius:50%;background:rgba(var(--v-theme-on-surface),.25);flex-shrink:0}.status-dot.on{background:rgb(var(--v-theme-success))}.cleanup-detail{padding:24px;min-width:0}.detail-header{display:flex;justify-content:space-between;align-items:center;gap:12px}.summary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:24px 0 16px}.summary-grid>div{background:rgba(var(--v-theme-on-surface),.025);padding:16px;border-radius:8px}.summary-grid span{display:block;font-size:12px;color:rgba(var(--v-theme-on-surface),.6);margin-bottom:10px}.summary-grid strong{font-size:23px;font-weight:600}.summary-grid small{font-size:12px;font-weight:400}.summary-grid .action-summary{font-size:16px}.policy-summary{display:flex;align-items:center;gap:8px;background:rgba(var(--v-theme-primary),.045);padding:12px 14px;border-radius:8px;font-size:12px;line-height:1.7;margin-bottom:24px}.tab-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;border-bottom:1px solid rgba(var(--v-border-color),var(--v-border-opacity));padding-bottom:10px;flex-wrap:wrap}.preview-caption{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:12px;color:rgba(var(--v-theme-on-surface),.6);padding:12px 0}.torrent-name{font-size:13px;overflow-wrap:anywhere;line-height:1.55;min-width:120px;max-width:360px}.reason-list{font-size:12px;line-height:1.6;color:rgba(var(--v-theme-on-surface),.65);margin-top:6px;min-width:160px;max-width:300px}.empty-state{text-align:center;padding:64px 18px;color:rgba(var(--v-theme-on-surface),.65)}.empty-state h4,.empty-state h3{margin:20px 0 8px;color:rgb(var(--v-theme-on-surface));font-weight:500}.empty-state p{font-size:14px;line-height:1.8;margin-bottom:22px}.welcome{padding-top:110px}.history-area{padding-top:20px}.history-item{display:flex;gap:12px;align-items:start;padding:12px 0;border-bottom:1px solid rgba(var(--v-border-color),.1)}.cleanup-table :deep(td){padding-block:14px!important}.cleanup-table :deep(th){font-size:12px!important;white-space:nowrap}@media(max-width:1100px){.cleanup-layout{grid-template-columns:220px minmax(0,1fr)}.summary-grid{grid-template-columns:repeat(2,1fr)}.cleanup-detail{padding:16px}.cleanup-header{align-items:flex-start;flex-direction:column}}@media(max-width:700px){.cleanup-workbench{padding:12px}.cleanup-layout{grid-template-columns:1fr}.cleanup-task-list{max-height:280px;overflow:auto}.detail-header{align-items:flex-start}.preview-caption{flex-wrap:wrap}.tab-toolbar{gap:16px}.summary-grid strong{font-size:20px}}

.cleanup-detail{padding:0!important;border:0;background:transparent;border-radius:0}
.detail-header,.cleanup-content-card,.policy-summary,.summary-grid>div{border:var(--app-surface-border,1px solid rgba(var(--v-theme-on-surface),.12));border-radius:var(--app-surface-radius,12px);background:rgb(var(--v-theme-surface))}
.detail-header{padding:20px 24px;margin-bottom:20px;gap:20px}
.summary-grid{margin:0 0 20px}.summary-grid>div{padding:20px;min-height:108px}
.policy-summary{padding:16px 20px;margin-bottom:20px}
.cleanup-content-card{padding:24px;min-width:0;overflow:hidden}
@media(max-width:700px){.detail-header,.cleanup-content-card{padding:16px}.detail-header{flex-wrap:wrap}}
.rail-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.rail-details{display:grid;gap:4px;margin-top:6px}.rail-row{display:grid;grid-template-columns:64px minmax(0,1fr);align-items:center;gap:6px;min-height:24px}.rail-pill{display:inline-block;justify-self:start;max-width:100%;min-width:0;padding:2px 8px;border-radius:999px;background:rgba(var(--v-theme-primary),.09);border:1px solid rgba(var(--v-theme-primary),.12);color:rgb(var(--v-theme-on-surface));font-size:11px;line-height:1.4;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}.rail-label,.rail-neutral{background:rgba(var(--v-theme-on-surface),.04);border-color:rgba(var(--v-theme-on-surface),.07);color:rgba(var(--v-theme-on-surface),.65)}.rail-accent{color:rgb(var(--v-theme-primary))}.rail-warn{color:rgb(var(--v-theme-warning));background:rgba(var(--v-theme-warning),.08);border-color:rgba(var(--v-theme-warning),.15)}
.task-button strong{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.task-order{margin-top:2px}.task-row .task-button{padding:14px 14px 6px}
</style>
