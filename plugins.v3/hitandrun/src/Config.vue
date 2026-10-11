<script setup>
import { onMounted } from 'vue'
import Workbench from './Workbench.vue'
defineProps({ api: { type: Object, default: () => ({}) }, initialConfig: { type: Object, default: () => ({}) } })
const emit = defineEmits(['layout', 'close', 'action'])
onMounted(() => emit('layout', { maxWidth: '86rem' }))
</script>
<template><Workbench :api="api" @action="$emit('action')" /><div class="text-right pa-3"><VBtn variant="text" @click="$emit('close')">关闭</VBtn></div></template>
