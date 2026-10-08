<script setup>
import { computed, ref, watch } from 'vue'
import { globalDeleteRules } from '../utils'
const props = defineProps({
  modelValue: Boolean,
  draft: { type: Object, default: () => ({}) },
  saving: Boolean,
})
const emit = defineEmits(['update:modelValue', 'save'])
const local = ref({})
const low = ref(null)
const high = ref(null)
const error = ref('')
watch(() => props.modelValue, open => {
  if (!open) return
  local.value = { ...props.draft }
  const values = String(local.value.global_delete_size_range || '').split('-').map(Number)
  low.value = values[0] || null
  high.value = values[1] || null
  error.value = ''
})
const ruleFields = [
  { key: 'global_seed_ratio', label: '分享率', hint: '分享率达到此值即可成为清理候选。' },
  { key: 'global_seed_size', label: '上传量（GB）', hint: '累计上传量达到此值即可成为清理候选。' },
  { key: 'global_download_time', label: '下载超时（小时）', hint: '仅针对尚未完成的下载，按添加后的经过时间判断。' },
  { key: 'global_seed_avgspeed', label: '平均上传速度（KB/s）', hint: '累计上传量除以添加后的经过时间，做种至少 30 分钟后判断是否低于此值。' },
  { key: 'global_hr_seed_time', label: 'H&R 做种时间（小时）', hint: 'H&R 单独使用累计做种时间或分享率条件，保护标签仍优先。' },
]
const summary = computed(() => globalDeleteRules(local.value))
function save() {
  if (local.value.global_proxy_delete) {
    if (!(Number(low.value) > 0 && Number(high.value) > Number(low.value))) {
      error.value = '开始清理容量必须大于停止清理容量，且两者均大于零。'
      return
    }
    if (!(Number(local.value.global_min_seed_hours) > 0 || Number(local.value.global_inactive_hours) > 0
      || ruleFields.some(field => Number(local.value[field.key]) > 0) || local.value.global_del_no_free)) {
      error.value = '请至少填写一项全局清理条件。'
      return
    }
  }
  error.value = ''
  emit('save', { ...local.value, global_delete_size_range: `${low.value}-${high.value}` })
}
</script>

<template>
  <VDialog :model-value="modelValue" fullscreen scrollable @update:model-value="emit('update:modelValue', $event)">
    <VCard>
      <VToolbar>
        <VBtn icon="mdi-arrow-left" aria-label="返回刷流任务" :disabled="saving" @click="emit('update:modelValue', false)" />
        <VToolbarTitle>全局删除策略</VToolbarTitle>
        <VBtn color="primary" variant="flat" :loading="saving" class="mr-4" @click="save">保存策略</VBtn>
      </VToolbar>
      <VCardText>
        <div class="delete-policy-content">
          <VAlert v-if="error" type="error" variant="tonal" class="mb-4">{{ error }}</VAlert>
          <VSwitch v-model="local.global_proxy_delete" label="启用全局托管清理" color="primary" inset />
          <p class="mb-6">统一管理参与托管的站点任务，容量达到阈值后，按种子状态判断清理条件，满足任意一项适用条件即可参与清理。</p>
          <h2 class="text-h6 mb-4">容量控制</h2>
          <VRow>
            <VCol cols="12" md="4"><VTextField v-model.number="local.global_disksize" type="number" min="1" label="新增任务容量上限（GB）" hint="到达上限后限制新增下载。" persistent-hint /></VCol>
            <VCol cols="12" md="4"><VTextField v-model.number="high" type="number" min="1" label="开始清理容量（GB）" hint="达到此容量才启动清理。" persistent-hint /></VCol>
            <VCol cols="12" md="4"><VTextField v-model.number="low" type="number" min="1" label="停止清理容量（GB）" hint="清理至此容量或以下停止。" persistent-hint /></VCol>
          </VRow>
          <h2 class="text-h6 mt-6 mb-4">清理条件，满足任意一项</h2>
          <VRow>
            <VCol cols="12" md="6"><VTextField v-model.number="local.global_min_seed_hours" type="number" min="0.01" clearable label="累计做种目标（小时）" hint="168 小时为 7 天，按下载器累计做种时长判断。任务可单独覆盖此目标。" persistent-hint /></VCol>
            <VCol cols="12" md="6"><VTextField v-model.number="local.global_inactive_hours" type="number" min="0.01" clearable label="连续未活动时间（小时）" hint="48 小时为 2 天，按最后传输时间判断，当前有传输时不算未活动。" persistent-hint /></VCol>
          </VRow>
          <VRow>
            <VCol v-for="field in ruleFields" :key="field.key" cols="12" md="6">
              <VTextField v-model.number="local[field.key]" type="number" min="0.01" clearable :label="field.label" :hint="field.hint" persistent-hint />
            </VCol>
          </VRow>
          <VSwitch v-model="local.global_del_no_free" label="删除促销过期的未完成下载" color="primary" inset hint="仅在有明确促销截止时间且已经过期时生效。" persistent-hint />
          <VTextField v-model="local.global_delete_except_tags" label="全局删除排除标签" hint="多个标签用逗号分隔，任务留空继承，填写后覆盖此项。" persistent-hint class="mt-4" />
          <VAlert type="info" variant="tonal" class="mt-6">{{ summary }}。普通种子的适用条件为或关系，空值表示不启用该项。</VAlert>
          <h2 class="text-h6 mt-6 mb-4">共用文件和保护规则</h2>
          <p>共用文件的关联种子各自满足任意一项条件后，才可整组删除。不同种子可以满足不同条件。保护标签优先。H&amp;R 单独判断，未完成下载仅使用下载超时或促销过期条件。</p>
          <p class="mt-3">没有合格种子时停止清理，不强制删除。当前支持同一个 qBittorrent 下载器。任务内普通删种条件不参与全局托管。</p>
        </div>
      </VCardText>
    </VCard>
  </VDialog>
</template>

<style scoped>
.delete-policy-content { max-width: 1000px; margin: 24px auto; }
</style>
