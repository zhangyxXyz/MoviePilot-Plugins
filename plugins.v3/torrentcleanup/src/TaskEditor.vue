<script setup>
import { computed, ref, watch } from 'vue'
import { actions, conditionDraft, normalizeTask, qbStates, trStates } from './utils'
const props = defineProps({ modelValue: Boolean, task: Object, options: Object, saving: Boolean })
const emit = defineEmits(['update:modelValue', 'save'])
const draft = ref(conditionDraft()), tab = ref('base'), form = ref(null)
const required = v => !!String(v ?? '').trim() || '请填写此项'
const downloaderType = computed(() => props.options?.downloaders?.find(d => d.value === draft.value.downloader)?.type)
const states = computed(() => downloaderType.value === 'transmission' ? trStates : qbStates)
const conditionSummary = computed(() => {
  const d = draft.value, parts = []
  if (d.verify_invalid) parts.push(`站点确认失效，观察至少 ${d.observation_hours} 小时 / ${d.min_observations} 次，证据间隔至少 ${d.observation_gap_hours} 小时`)
  else parts.push('不要求站点失效')
  if (d.states?.length) parts.push(`状态：${d.states.join(' 或 ')}`)
  if (d.error_keywords?.length) parts.push(`错误包含：${d.error_keywords.join(' 或 ')}`)
  if (d.completion !== 'all') parts.push(d.completion === 'completed' ? '仅已完成下载' : '仅未完成下载')
  if (d.filter_seed) parts.push(`累计做种 ≥ ${d.min_seed_hours} 小时`)
  if (d.filter_ratio) parts.push(`分享率 ≥ ${d.min_ratio}`)
  if (d.filter_age) parts.push(`添加 ≥ ${d.error_min_age_hours} 小时`)
  if (d.filter_progress) parts.push(`下载进度 ≤ ${d.error_max_progress}%`)
  if (d.require_idle_download) parts.push('下载速度为 0')
  if (d.observe_state) parts.push(`持续命中 ≥ ${d.error_observation_hours} 小时且 ≥ ${d.error_min_observations} 次，采样间隔 ≥ ${d.error_gap_minutes} 分钟`)
  if (d.include_tags?.length) parts.push(`同时具有标签：${d.include_tags.join('、')}`)
  if (Number(d.min_size_gb) > 0) parts.push(`体积 ≥ ${d.min_size_gb} GiB`)
  if (d.max_size_gb !== '' && d.max_size_gb != null) parts.push(`体积 ≤ ${d.max_size_gb} GiB`)
  if (d.categories?.length) parts.push(`分类：${d.categories.join(' 或 ')}`)
  if (d.max_upload_speed_kib !== '' && d.max_upload_speed_kib != null) parts.push(`上传速度 ≤ ${d.max_upload_speed_kib} KiB/s`)
  if (d.only_moviepilot) parts.push('仅 MoviePilot 管理任务')
  if (d.path_prefix) parts.push(`目录：${d.path_prefix}`)
  return parts.join('，并且 ')
})
const protectionSummary = computed(() => {
  const d = draft.value, parts = ['H&R 明确未满足时保留']
  if (d.protect_tags?.length) parts.push(`保护标签任一命中：${d.protect_tags.join('、')}`)
  if (d.error_protect_unknown_hr) parts.push('H&R 无法确认时保留')
  if (d.require_healthy) parts.push('本地异常或校验中保留')
  return parts.join('；')
})
watch(() => props.modelValue, open => { if (open) { draft.value = conditionDraft(props.task); tab.value = 'base' } })
async function save() {
  const valid = await form.value?.validate()
  if (valid?.valid) emit('save', normalizeTask(draft.value))
}
</script>
<template>
<VDialog :model-value="modelValue" max-width="1080" scrollable @update:model-value="emit('update:modelValue', $event)">
<VCard class="cleanup-editor">
<VToolbar color="transparent"><VToolbarTitle>{{ draft.id ? '编辑清理任务' : '新建清理任务' }}</VToolbarTitle><VSpacer /><VBtn color="primary" variant="flat" :loading="saving" @click="save">保存任务</VBtn><VBtn icon="mdi-close" aria-label="关闭编辑器" @click="emit('update:modelValue', false)" /></VToolbar>
<VDivider /><VCardText><VForm ref="form" @submit.prevent="save">
<VTabs v-model="tab" color="primary"><VTab value="base">范围与调度</VTab><VTab value="selection">清理条件</VTab><VTab value="action">动作与限额</VTab></VTabs>
<section v-show="tab === 'base'" class="editor-section">
<p>每个任务选择一个下载器，可以同时选择多个站点。清理条件直接在下一页勾选。</p>
<div class="field-grid">
<VTextField v-model="draft.name" label="任务名称" :rules="[required]" />
<VSelect v-model="draft.downloader" :items="options?.downloaders || []" label="下载器" :rules="[required]" @update:model-value="draft.states = []" />
<VSwitch class="wide" v-model="draft.all_sites" :disabled="draft.verify_invalid" label="不限站点（包含未识别站点的任务）" color="primary" hide-details />
<VSelect class="wide" v-model="draft.site_ids" :disabled="draft.all_sites" :items="options?.sites || []" multiple chips closable-chips label="作用站点（可多选）" hint="选择允许处理的站点范围。多 Tracker 种子的所有关联站点都必须在所选范围内，无法识别的保留。" persistent-hint :rules="[v => draft.all_sites || v?.length > 0 || '请选择至少一个站点']" />
<VTextField v-model="draft.cron" label="执行周期（Cron）" hint="*/15 * * * * 每 15 分钟，0 */6 * * * 每 6 小时" persistent-hint :rules="[required]" />
<VSwitch v-model="draft.enabled" label="启用此任务的定时计划" inset color="primary" hide-details />
</div></section>
<section v-show="tab === 'selection'" class="editor-section">
<VAlert type="info" variant="tonal"><strong>生效规则：各组之间“并且”，保护条件优先</strong><div>已启用的条件必须全部满足。同组多个状态、错误关键词、QB 分类匹配任意一个即可（或者）。必需标签必须全部具备（并且），保护标签命中任意一个就保留（或者）。留空或未勾选的筛选条件不参与判断。</div></VAlert>
<div class="condition-summary"><h3>当前生效条件摘要</h3><p>{{ conditionSummary }}</p><p>保护：{{ protectionSummary }}</p><p>上述条件同时满足，并通过下方保留保护后，才执行“动作与限额”中的动作。</p></div>
<div class="condition-group"><h3>1. 站点失效确认</h3><p>清理死种时开启。正常 Tracker 不请求站点，超时、无速度、搜索不到都不算确认失效。</p>
<VSwitch v-model="draft.verify_invalid" label="必须确认站点已删除种子才允许处理" color="primary" hint="开启后：Tracker 明确不存在，并且站点明确删除，并且下方观察条件全部满足，才可处理。关闭后不要求站点失效，只按其他条件判断。" persistent-hint @update:model-value="v => { if (v) draft.all_sites = false }" />
<div v-if="draft.verify_invalid" class="field-grid">
<VTextField v-model="draft.observation_hours" type="number" min="1" label="站点失效观察期（小时）" />
<VTextField v-model="draft.min_observations" type="number" min="2" label="失效至少确认次数" />
<VTextField v-model="draft.observation_gap_hours" type="number" min="1" label="两次站点证据间隔（小时）" />
<p>观察期、确认次数和证据间隔必须同时满足（并且）。多站点种子要求所有关联站点均确认失效。缓存不增加确认次数。</p>
</div>
</div>
<div class="condition-heading"><h3>2. 下载器状态与门槛</h3><p>这里填写的条件与上方站点失效确认同时生效。只清理死种时，任务状态和错误关键词可以留空。</p></div>
<div class="field-grid">
<VSelect class="wide" v-model="draft.states" :items="states" multiple chips closable-chips label="任务状态（多个任一匹配，或者）" hint="留空不限。状态只是筛选条件。暂停、停滞、排队不等于死种，勾选后会按所选动作处理。" persistent-hint />
<VCombobox class="wide" v-model="draft.error_keywords" multiple chips closable-chips label="错误信息关键词（多个任一匹配，或者）" hint="留空不限。多个关键词匹配任意一个即可，但如果同时选了状态，还必须命中状态（并且）。回车、英文逗号、中文逗号或换行分隔，忽略大小写，非正则。QB 读取 Tracker 消息，TR 同时读取 errorString。" persistent-hint />
<VSelect v-model="draft.completion" label="下载完成范围" :items="[{title:'不限完成状态',value:'all'},{title:'仅已完成下载',value:'completed'},{title:'仅未完成下载',value:'incomplete'}]" />
<VSwitch v-model="draft.require_idle_download" label="要求当前下载速度为 0" color="primary" hide-details />
<div><VCheckbox v-model="draft.filter_seed" label="限制最低累计做种时长" color="primary" hide-details /><VTextField v-if="draft.filter_seed" v-model="draft.min_seed_hours" type="number" min="0" label="最低累计做种（小时）" /></div>
<div><VCheckbox v-model="draft.filter_ratio" label="限制最低分享率" color="primary" hide-details /><VTextField v-if="draft.filter_ratio" v-model="draft.min_ratio" type="number" min="0" step="0.1" label="最低分享率" /></div>
<div><VCheckbox v-model="draft.filter_age" label="设置添加后的保护期" color="primary" hide-details /><VTextField v-if="draft.filter_age" v-model="draft.error_min_age_hours" type="number" min="0" label="添加后至少经过（小时）" /></div>
<div><VCheckbox v-model="draft.filter_progress" label="限制最高下载进度" color="primary" hide-details /><VTextField v-if="draft.filter_progress" v-model="draft.error_max_progress" type="number" min="0" max="99.99" label="最高下载进度（%）" /></div>
<VCheckbox class="wide" v-model="draft.observe_state" label="状态或错误关键词需持续多次命中才允许处理" color="primary" hide-details />
<template v-if="draft.observe_state">
<VTextField v-model="draft.error_observation_hours" type="number" min="0.25" label="持续命中时间（小时）" />
<VTextField v-model="draft.error_min_observations" type="number" min="2" label="至少确认次数" />
<VTextField v-model="draft.error_gap_minutes" type="number" min="1" label="有效采样间隔（分钟）" />
<p>持续时间和确认次数必须同时达到，采样还必须满足间隔。状态恢复、错误消息变化或下载进度改变会重新计时，连续点击不会增加次数。</p>
</template>
<div class="wide condition-heading"><h3>3. 标签、体积与目录范围</h3><p>可选的额外筛选，与前面所有已启用条件同时满足。</p></div>
<VTextField v-model="draft.min_size_gb" type="number" min="0" label="最小体积（GiB，0 不限）" />
<VTextField v-model="draft.max_size_gb" type="number" min="0" label="最大体积（GiB，留空不限）" clearable />
<VCombobox class="wide" v-model="draft.include_tags" multiple chips closable-chips label="必须包含的标签" hint="每输入一个标签按回车确认，也支持英文逗号、中文逗号或换行分隔。多个标签必须全部匹配，例如：刷流,已整理。留空不限制。" persistent-hint />
<VCombobox v-if="downloaderType !== 'transmission'" class="wide" v-model="draft.categories" multiple chips closable-chips label="QB 分类（留空不限，多个任一匹配）" />
<VTextField v-model="draft.max_upload_speed_kib" type="number" min="0" label="当前上传速度上限（KiB/s，留空不限）" clearable />
<VSwitch v-model="draft.only_moviepilot" label="只处理带 MoviePilot 标签的任务" color="primary" hide-details />
<VTextField class="wide" v-model="draft.path_prefix" label="下载器中的目录范围（留空不限）" hint="按目录层级匹配，例如 /media/a 不包含 /media/abc。" persistent-hint />
</div>
<div class="condition-group"><h3>4. 保留保护</h3><p>命中任意一项保护就保留，即使前面的清理条件全部满足。</p>
<VCombobox v-model="draft.protect_tags" multiple chips closable-chips label="保护标签" hint="按回车添加，或用英文逗号、中文逗号、换行分隔。匹配任意一个即保留，H&R 标签始终受保护。" persistent-hint />
<VSwitch v-model="draft.error_protect_unknown_hr" label="H&R 无法确认时保留" color="primary" hint="明确未满足 H&R 或命中保护标签始终保留。" persistent-hint />
<VSwitch v-model="draft.require_healthy" label="本地状态异常或校验中时保留" color="primary" hint="如果需要清理 error、missingFiles 等异常状态，请关闭此项。" persistent-hint />
</div></section>
<section v-show="tab === 'action'" class="editor-section action-panel">
<VSelect v-model="draft.action" :items="actions" label="满足条件后的动作" hide-details />
<VAlert v-if="['delete','deletefile'].includes(draft.action)" type="warning" variant="tonal">启用总开关及任务后，会按计划执行所选删除动作。不要求打开预览页面。</VAlert>
<VAlert v-if="draft.action === 'deletefile'" type="info" variant="tonal">删除文件前核实所有下载器的文件引用，共享文件或无法确认时保留。</VAlert>
<div class="field-grid">
<VTextField v-model="draft.max_count" type="number" min="1" max="100" label="每轮最多处理任务数" hint="这是上限，不是最低任务数。" persistent-hint />
<VTextField v-model="draft.max_gb" type="number" min="0.1" label="每轮最多处理体积（GiB）" />
</div>
<VSwitch v-model="draft.notify" label="发送执行结果通知" color="primary" inset hide-details />
</section>
</VForm></VCardText></VCard></VDialog>
</template>
<style scoped>
.editor-section{display:flex;flex-direction:column;gap:24px;padding:24px 0;min-height:360px}
.condition-group{display:flex;flex-direction:column;gap:18px;border:1px solid rgba(var(--v-border-color),.16);border-radius:10px;padding:20px}.condition-summary{padding:16px;border-radius:10px;background:rgba(var(--v-theme-primary),.06);overflow-wrap:anywhere}.condition-summary p,.condition-heading p,.condition-group>p{margin:8px 0 0}.cleanup-editor h3{font-size:15px;font-weight:600}
.field-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;align-items:start}
.field-grid>.wide{grid-column:1/-1}.editor-section p{font-size:13px;line-height:1.8;color:rgba(var(--v-theme-on-surface),.7)}
.cleanup-editor :deep(.v-messages){line-height:1.65}.cleanup-editor :deep(.v-toolbar){padding-inline:12px}
@media(max-width:650px){.field-grid{grid-template-columns:1fr}.editor-section{gap:20px}.field-grid{gap:20px}}
</style>
