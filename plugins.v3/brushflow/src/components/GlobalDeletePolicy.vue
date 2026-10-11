<script setup>
import TagInput from './TagInput.vue'
import { ref, watch } from 'vue'
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
  { key: 'global_seed_avgspeed', label: '平均上传速度（KB/s）', hint: '做种满 30 分钟后判断低于阈值。' },
]
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
          <p class="mb-6">达到容量上限后，清理符合条件的任务。</p>
          <h2 class="text-h6 mb-4">容量控制</h2>
          <VRow>
            <VCol cols="12" md="4"><VTextField v-model.number="local.global_disksize" type="number" min="1" label="新增任务容量上限（GB）" hint="到达上限后限制新增下载。" persistent-hint /></VCol>
            <VCol cols="12" md="4"><VTextField v-model.number="high" type="number" min="1" label="开始清理容量（GB）" hint="达到此容量才启动清理。" persistent-hint /></VCol>
            <VCol cols="12" md="4"><VTextField v-model.number="low" type="number" min="1" label="停止清理容量（GB）" hint="清理至此容量或以下停止。" persistent-hint /></VCol>
          </VRow>
          <h2 class="text-h6 mt-6 mb-4">清理触发条件（任一满足）</h2>
          <VRow>
            <VCol cols="12" md="6"><VTextField v-model.number="local.global_min_seed_hours" type="number" min="0.01" clearable label="累计做种目标（小时）" hint="任务内可覆盖。" persistent-hint /></VCol>
            <VCol cols="12" md="6"><VTextField v-model.number="local.global_inactive_hours" type="number" min="0.01" clearable label="连续未活动时间（小时）" hint="须同时无传输，与累计做种目标为或" persistent-hint /></VCol>
          </VRow>
          <VRow>
            <VCol v-for="field in ruleFields" :key="field.key" cols="12" md="6">
              <VTextField v-model.number="local[field.key]" type="number" min="0.01" clearable :label="field.label" :hint="field.hint" persistent-hint />
            </VCol>
          </VRow>
          <VSwitch v-model="local.global_del_no_free" label="删除促销过期的未完成下载" color="primary" inset hint="仅在有明确促销截止时间且已经过期时生效。" persistent-hint />
          <TagInput v-model="local.global_delete_except_tags" label="全局删除排除标签（永久保护）" hint="回车添加，逗号或换行分隔，命中任一标签即保留，任务内可覆盖" persistent-hint class="mt-4" />
          <VAlert type="info" variant="tonal" class="mt-6">同类触发条件为或，保护标签、容量和共享文件检查须全部通过（且）。</VAlert>
          <VAlert type="info" variant="tonal" class="mt-4">H&R 助手维护保护标签，达标解除后再参与清理，标签名称和目标在助手中设置。</VAlert>
          <h2 class="text-h6 mt-6 mb-4">共享文件</h2>
          <p>每个关联任务分别达标（且），不合并做种时间或上传量。任一受保护或未达标，整组保留。</p>
          <p class="mt-3">同一 qBittorrent 内，关联辅种没有独立配置时继承全局策略。</p>
        </div>
      </VCardText>
    </VCard>
  </VDialog>
</template>

<style scoped>
.delete-policy-content { max-width: 1000px; margin: 32px auto; }
.delete-policy-content > :deep(.v-switch) { margin-block: 16px 24px; }
</style>
