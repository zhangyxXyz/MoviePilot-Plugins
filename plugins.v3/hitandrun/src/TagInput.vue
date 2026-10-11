<script setup>
import { computed, ref } from 'vue'
const props = defineProps({ modelValue: { type: String, default: '' }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const search = ref('')
const split = value => [...new Set((Array.isArray(value) ? value : [value]).flatMap(v => String(v || '').split(/[,，\n]/)).map(v => v.trim()).filter(Boolean))]
const values = computed(() => split(props.modelValue))
function update(value) { emit('update:modelValue', split(value).join(',')) }
function paste(event) {
  if (props.disabled) return
  const text = event.clipboardData?.getData('text') || ''
  if (!/[,，\n]/.test(text)) return
  event.preventDefault()
  update([...values.value, ...split(text)])
  search.value = ''
}
</script>
<template>
  <VCombobox :model-value="values" v-model:search="search" :disabled="disabled" multiple chips :closable-chips="!disabled" @update:model-value="update" @paste="paste" />
</template>
