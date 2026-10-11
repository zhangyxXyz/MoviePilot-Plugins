<script setup>
import TagInput from './TagInput.vue'
import { computed, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'
import { cloneTask, normalizeTask } from '../utils'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  task: { type: Object, default: () => ({}) },
  sites: { type: Array, default: () => [] },
  downloaders: { type: Array, default: () => [] },
  globalDynamicDelete: { type: Boolean, default: false },
  globalSettings: { type: Object, default: () => ({}) },
  saving: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'save', 'open-global'])
const display = useDisplay()
const formRef = ref(null)
const activeTab = ref('base')
const localTask = ref(cloneTask())

const dialogTitle = computed(() => (localTask.value.id ? '编辑刷流任务' : '新建刷流任务'))
const siteName = computed(() => props.sites.find(item => item.value === Number(localTask.value.site_id))?.title || '未选择')
const scheduleText = computed(() => localTask.value.cron || `每 ${localTask.value.brush_interval || 10} 分钟`)

const globalFields = [
  ['global_disksize', '新增容量上限（GB）'],
  ['global_delete_size_range', '清理容量下限–上限（GB）'],
  ['global_min_seed_hours', '普通做种目标（小时）'],
  ['global_inactive_hours', '普通未活动时间（小时）'],
  ['global_seed_ratio', '普通分享率'],
  ['global_seed_size', '普通上传量（GB）'],
  ['global_download_time', '下载超时（小时）'],
  ['global_seed_avgspeed', '普通平均上传速度（KB/s）'],
]
const globalValue = key => props.globalSettings[key] ?? '未设置'

// 每次打开弹窗都从服务端任务快照重新创建本地草稿。
watch(
  () => props.modelValue,
  visible => {
    if (!visible) return
    localTask.value = cloneTask(props.task)
    if (props.globalDynamicDelete && localTask.value.min_seed_hours == null) {
      localTask.value.min_seed_hours = localTask.value.seed_time
    }
    activeTab.value = 'base'
  },
)

// 关闭编辑器并丢弃尚未保存的草稿。
function closeDialog() {
  emit('update:modelValue', false)
}

// 校验必填项后提交标准化任务数据。
async function saveTask() {
  const result = await formRef.value?.validate()
  if (result && !result.valid) return
  const payload = normalizeTask(localTask.value)
  if (props.globalDynamicDelete) payload.seed_time = null // 合并旧时间字段，清空后可真正继承全局
  emit('save', payload)
}
</script>

<template>
  <VDialog
    :model-value="modelValue"
    scrollable
    :fullscreen="display.smAndDown.value"
    max-width="74rem"
    @update:model-value="value => emit('update:modelValue', value)"
  >
    <VCard class="brushflow-editor">
      <VToolbar color="transparent" density="comfortable" class="brushflow-editor__toolbar">
        <VToolbarTitle>{{ dialogTitle }}</VToolbarTitle>
        <VSpacer />
        <VBtn color="primary" variant="flat" prepend-icon="mdi-content-save" :loading="saving" @click="saveTask">
          保存任务
        </VBtn>
        <VBtn icon="mdi-close" variant="text" aria-label="关闭" @click="closeDialog" />
      </VToolbar>
      <VDivider />

      <VCardText class="brushflow-editor__body">
        <VForm ref="formRef" class="brushflow-editor__form" @submit.prevent="saveTask">
          <VTabs
            v-model="activeTab"
            :direction="display.mdAndUp.value ? 'vertical' : 'horizontal'"
            color="primary"
            class="brushflow-editor__tabs"
          >
            <VTab value="base" prepend-icon="mdi-calendar-clock">基础与调度</VTab>
            <VTab value="selection" prepend-icon="mdi-filter-cog-outline">选种规则</VTab>
            <VTab value="limits" prepend-icon="mdi-gauge">运行限额</VTab>
            <VTab value="delete" prepend-icon="mdi-delete-clock-outline">删种规则</VTab>
            <VTab value="advanced" prepend-icon="mdi-tune-variant">高级</VTab>
          </VTabs>

          <VDivider :vertical="display.mdAndUp.value" />

          <VWindow v-model="activeTab" :touch="false" class="brushflow-editor__window">
            <VWindowItem value="base">
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">任务身份</div>
                    <div class="text-body-2 text-medium-emphasis">每个任务绑定一个站点和下载器</div>
                  </div>
                  <VChip size="small" color="primary" variant="tonal">必填</VChip>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model="localTask.name"
                      label="任务名称"
                      :rules="[value => !!String(value || '').trim() || '请输入任务名称']"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VSelect
                      v-model="localTask.site_id"
                      :items="sites"
                      label="站点"
                      :rules="[value => !!value || '请选择站点']"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VSelect
                      v-model="localTask.downloader"
                      :items="downloaders"
                      label="下载器"
                      :rules="[value => !!value || '请选择下载器']"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model="localTask.save_path" label="保存目录" placeholder="留空使用下载器默认目录" />
                  </VCol>
                </VRow>
                <div class="editor-switches">
                  <VSwitch v-model="localTask.enabled" label="启用任务" color="primary" hide-details inset />
                  <VSwitch v-model="localTask.notify" label="发送通知" color="primary" hide-details inset />
                </div>
              </section>

              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">刷新计划</div>
                    <div class="text-body-2 text-medium-emphasis">刷流刷新和下载状态检查分别调度</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model.number="localTask.brush_interval"
                      type="number"
                      min="1"
                      max="1440"
                      label="刷流刷新周期（分钟）"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model.number="localTask.check_interval"
                      type="number"
                      min="1"
                      max="1440"
                      label="状态检查周期（分钟）"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model="localTask.cron" label="CRON 表达式" placeholder="留空使用固定刷新周期" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model="localTask.active_time_range"
                      label="开启时间段"
                      placeholder="如 00:00-08:00"
                    />
                  </VCol>
                </VRow>
              </section>

              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">站点分享率控制</div>
                    <div class="text-body-2 text-medium-emphasis">根据最新站点统计自动等待或恢复刷流</div>
                  </div>
                </header>
                <VSwitch
                  v-model="localTask.site_ratio_control"
                  label="启用站点分享率控制"
                  color="primary"
                  hide-details
                  inset
                />
                <VRow v-if="localTask.site_ratio_control">
                  <VCol cols="12" md="6">
                    <VTextField
                      v-model.number="localTask.site_ratio_target"
                      type="number"
                      min="0.01"
                      step="0.01"
                      label="目标分享率"
                      :rules="[value => Number(value) > 0 || '请输入大于 0 的目标分享率']"
                    />
                  </VCol>
                </VRow>
              </section>
            </VWindowItem>

            <VWindowItem value="selection">
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">来源与促销</div>
                    <div class="text-body-2 text-medium-emphasis">沿用站点列表页或 RSS 获取链路</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VSelect
                      v-model="localTask.freeleech"
                      label="促销"
                      :items="[
                        { title: '全部（包括普通）', value: '' },
                        { title: '免费', value: 'free' },
                        { title: '2X 免费', value: '2xfree' },
                      ]"
                    />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VSelect
                      v-model="localTask.hr"
                      label="排除 H&R"
                      :items="[
                        { title: '是', value: 'yes' },
                        { title: '否', value: 'no' },
                      ]"
                    />
                  </VCol>
                </VRow>
                <div class="editor-switches">
                  <VSwitch v-model="localTask.rss_support" label="使用 RSS" color="primary" hide-details inset />
                  <VSwitch v-model="localTask.except_subscribe" label="排除订阅" color="primary" hide-details inset />
                </div>
              </section>

              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">下载器判重</div>
                    <div class="text-body-2 text-medium-emphasis">任一命中即跳过，仅检查本下载器，包含辅种。</div>
                  </div>
                </header>
                <div class="editor-switches">
                  <VSwitch v-model="localTask.dedupe_hash" label="跳过已有相同 Hash" color="primary" hide-details inset />
                  <VSwitch v-model="localTask.dedupe_site_id" label="跳过已有同站点种子 ID" color="primary" hide-details inset />
                </div>
                <div class="text-body-2 text-medium-emphasis mt-3">添加前核实 Hash，站点 ID 从备注详情链接提取，查询失败则暂缓新增，不按标题判重。</div>
              </section>

              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">候选过滤</div>
                    <div class="text-body-2 text-medium-emphasis">范围字段支持单值或“最小值-最大值”</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="4">
                    <VTextField v-model="localTask.size" label="种子大小（GB）" placeholder="10-80" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model="localTask.seeder" label="做种人数" placeholder="1-10" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model="localTask.pubtime" label="发布时间（分钟）" placeholder="5-120" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.timezone_offset" type="number" label="站点时区偏移（小时）" />
                  </VCol>
                  <VCol cols="12" md="8">
                    <VTextField v-model="localTask.include" label="包含规则" placeholder="支持正则表达式" />
                  </VCol>
                  <VCol cols="12">
                    <VTextField v-model="localTask.exclude" label="排除规则" placeholder="支持正则表达式" />
                  </VCol>
                </VRow>
              </section>
            </VWindowItem>

            <VWindowItem value="limits">
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">新增任务上限</div>
                    <div class="text-body-2 text-medium-emphasis">达到任一上限后停止为当前任务新增种子</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.disksize" type="number" min="0" label="保种体积（GB）" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.maxdlcount" type="number" min="0" label="同时下载任务数" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.maxupspeed" type="number" min="0" label="总上传带宽（KB/s）" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.maxdlspeed" type="number" min="0" label="总下载带宽（KB/s）" />
                  </VCol>
                </VRow>
              </section>
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">单种限速</div>
                    <div class="text-body-2 text-medium-emphasis">只作用于当前任务新添加的种子</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.up_speed" type="number" min="0" label="上传限速（KB/s）" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.dl_speed" type="number" min="0" label="下载限速（KB/s）" />
                  </VCol>
                </VRow>
              </section>
            </VWindowItem>

            <VWindowItem value="delete">
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">
                      {{ globalDynamicDelete ? '全局删种托管' : '删除模式' }}
                    </div>
                    <div class="text-body-2 text-medium-emphasis">
                      {{ globalDynamicDelete ? '选择此任务是否参加全局容量清理' : '动态模式会在超过体积阈值后按现有算法托管删种' }}
                    </div>
                  </div>
                </header>
                <VBtnToggle class="delete-mode-buttons" v-model="localTask.proxy_delete" mandatory color="primary" divided>
                  <VBtn :value="false">{{ globalDynamicDelete ? '不参与托管' : '按条件删除' }}</VBtn>
                  <VBtn :value="true">{{ globalDynamicDelete ? '参与全局托管' : '动态删种' }}</VBtn>
                </VBtnToggle>
                <div class="mt-4 policy-preview">
                  <div class="policy-preview__header d-flex align-center justify-space-between flex-wrap ga-2">
                    <div class="text-subtitle-1 font-weight-medium">全局删除配置（只读）</div>
                    <VBtn variant="text" size="small" prepend-icon="mdi-cog-outline" @click="emit('open-global')">打开全局配置</VBtn>
                  </div>
                  <VRow class="policy-preview__fields">
                    <VCol v-for="[key, label] in globalFields" :key="key" cols="12" sm="6" md="4">
                      <VTextField :model-value="globalValue(key)" :label="label" disabled density="compact" hide-details />
                    </VCol>
                    <VCol cols="12" sm="6">
                      <TagInput :model-value="globalSettings.global_delete_except_tags || ''" label="永久排除标签" disabled density="compact" hide-details />
                    </VCol>
                    <VCol cols="12" sm="6">
                      <VTextField :model-value="globalSettings.global_del_no_free ? '开启' : '关闭'" label="促销过期清理（未完成下载）" disabled density="compact" hide-details />
                    </VCol>
                  </VRow>
                  <div class="text-body-2 text-medium-emphasis mt-3">同类条件满足任一项即可（或），共享文件的每个任务须分别达标（且），H&R 保护标签未解除时保留。</div>
                </div>
                <VRow v-if="localTask.proxy_delete && !globalDynamicDelete">
                  <VCol cols="12">
                    <VTextField
                      v-model="localTask.delete_size_range"
                      label="动态删种阈值（GB）"
                      placeholder="如 350-500"
                    />
                  </VCol>
                </VRow>
              </section>
              <section class="editor-section">
                <VAlert v-if="globalDynamicDelete" type="info" variant="tonal" class="mb-4">下方字段留空继承全局，填写则覆盖，参与托管后按全局容量清理。</VAlert>

                <VTextField
                  v-model.number="localTask.min_seed_hours"
                  type="number"
                  min="0.01"
                  clearable
                  label="累计做种目标（小时）"
                  hint="留空继承全局，与同类条件为或关系。"
                  persistent-hint
                />
              </section>
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">触发条件</div>
                    <div class="text-body-2 text-medium-emphasis">已完成任务的做种时间、未活动和分享率等条件为或，保护标签、容量及共享文件检查为且。下载超时和促销过期仅针对未完成任务，H&R 标签由助手达标后解除。</div>
                  </div>
                </header>
                <VRow>
                  <VCol v-if="!globalDynamicDelete" cols="12" md="4">
                    <VTextField v-model.number="localTask.seed_time" type="number" min="0" clearable hint="留空继承全局，填写后覆盖" persistent-hint label="做种时间（小时）" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.seed_ratio" type="number" min="0" clearable hint="留空继承全局，填写后覆盖" persistent-hint label="分享率" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.seed_size" type="number" min="0" clearable hint="留空继承全局，填写后覆盖" persistent-hint label="上传量（GB）" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.download_time" type="number" min="0" clearable hint="留空继承全局，填写后覆盖" persistent-hint label="下载超时（小时）" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.seed_inactivetime" type="number" min="0" clearable hint="与同类条件为或，留空继承全局" persistent-hint label="未活动时间（分钟）" />
                  </VCol>
                  <VCol cols="12" md="4">
                    <VTextField v-model.number="localTask.seed_avgspeed" type="number" min="0" clearable hint="留空继承全局，填写后覆盖" persistent-hint label="平均上传速度（KB/s）" />
                  </VCol>
                  <VCol cols="12" md="8">
                    <TagInput v-model="localTask.delete_except_tags" clearable label="删除排除标签（永久保护）" hint="留空继承全局，回车添加，逗号或换行分隔，命中任一标签即保留" persistent-hint />
                  </VCol>
                </VRow>
                <VSelect
                  v-model="localTask.del_no_free"
                  :items="[{ title: '继承全局', value: null }, { title: '启用', value: true }, { title: '关闭', value: false }]"
                  label="删除促销过期的未完成下载"
                  hint="选择启用或关闭覆盖全局，继承全局使用全局开关"
                  persistent-hint
                />
              </section>
            </VWindowItem>

            <VWindowItem value="advanced">
              <section class="editor-section">
                <header class="editor-section__head">
                  <div>
                    <div class="text-subtitle-1 font-weight-medium">下载器适配</div>
                    <div class="text-body-2 text-medium-emphasis">保留原有分类、提示跳过和自动归档能力</div>
                  </div>
                </header>
                <VRow>
                  <VCol cols="12" md="6">
                    <VTextField v-model="localTask.qb_category" label="qBittorrent 分类" />
                  </VCol>
                  <VCol cols="12" md="6">
                    <VTextField v-model.number="localTask.auto_archive_days" type="number" min="0" label="自动归档天数" />
                  </VCol>
                </VRow>
                <div class="editor-switches">
                  <VSwitch v-model="localTask.site_skip_tips" label="自动跳过下载提示" color="primary" hide-details inset />
                </div>
              </section>
            </VWindowItem>
          </VWindow>

          <VSheet tag="aside" class="brushflow-editor__summary">
            <div class="text-subtitle-1 font-weight-medium">配置摘要</div>
            <dl>
              <div><dt>站点</dt><dd>{{ siteName }}</dd></div>
              <div><dt>下载器</dt><dd>{{ localTask.downloader || '未选择' }}</dd></div>
              <div><dt>刷新</dt><dd>{{ scheduleText }}</dd></div>
              <div><dt>检查</dt><dd>每 {{ localTask.check_interval || 5 }} 分钟</dd></div>
              <div><dt>时段</dt><dd>{{ localTask.active_time_range || '全天' }}</dd></div>
              <div><dt>目标分享率</dt><dd>{{ localTask.site_ratio_control ? localTask.site_ratio_target || '未设置' : '关闭' }}</dd></div>
              <div><dt>促销</dt><dd>{{ localTask.freeleech === '2xfree' ? '2X 免费' : localTask.freeleech === 'free' ? '免费' : '全部' }}</dd></div>
              <div><dt>保种上限</dt><dd>{{ localTask.disksize ? `${localTask.disksize} GB` : '不限' }}</dd></div>
              <div><dt>删除</dt><dd>{{ localTask.proxy_delete ? '动态删种' : '按条件删除' }}</dd></div>
            </dl>
          </VSheet>
        </VForm>
      </VCardText>
    </VCard>
  </VDialog>
</template>

<style scoped>
.brushflow-editor {
  max-block-size: min(90dvh, 58rem);
}

.brushflow-editor__toolbar {
  flex: 0 0 auto;
  padding-inline: 8px;
  z-index: 4;
  backdrop-filter: blur(var(--transparent-blur, 0px));
  background-color: rgba(var(--v-theme-surface), var(--transparent-opacity-heavy, 1));
}

.brushflow-editor__body {
  padding: 0;
}

.brushflow-editor__form {
  display: grid;
  grid-template-columns: 12rem auto minmax(0, 1fr) minmax(12rem, 0.34fr);
  min-block-size: 34rem;
}

.brushflow-editor__tabs {
  padding: 12px 8px;
}

.brushflow-editor__window {
  min-inline-size: 0;
  padding: 20px;
}

.editor-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.editor-section + .editor-section {
  margin-block-start: 28px;
  padding-block-start: 24px;
  border-block-start: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}

.editor-section__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.editor-switches {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 24px;
  padding-block: 8px;
}

.delete-mode-buttons {
  margin-block: 8px 16px;
  align-self: flex-start;
}

.policy-preview__header {
  padding-block: 8px;
  margin-block-end: 20px;
}

.policy-preview__fields :deep(.v-field--disabled) {
  background: rgba(var(--v-theme-on-surface), 0.05);
}

.brushflow-editor__summary {
  padding: 20px 16px;
  border-inline-start: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}

.brushflow-editor__summary dl {
  display: grid;
  gap: 12px;
  margin: 18px 0 0;
}

.brushflow-editor__summary dl > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.brushflow-editor__summary dt {
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
}

.brushflow-editor__summary dd {
  margin: 0;
  text-align: end;
  overflow-wrap: anywhere;
}

@media (max-width: 959px) {
  .brushflow-editor {
    max-block-size: none;
  }

  .brushflow-editor__form {
    grid-template-columns: 1fr;
    min-block-size: 0;
  }

  .brushflow-editor__tabs {
    max-inline-size: 100%;
    padding-block: 0;
    overflow-x: auto;
  }

  .brushflow-editor__window {
    padding: 16px;
  }

  .brushflow-editor__summary {
    border-inline-start: 0;
    border-block-start: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  }
}

@media (max-width: 599px) {
  .brushflow-editor__toolbar :deep(.v-toolbar-title) {
    font-size: 1rem;
  }

  .brushflow-editor__toolbar :deep(.v-btn__content) {
    white-space: normal;
  }

}
</style>
