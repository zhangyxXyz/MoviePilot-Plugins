<script setup>
import Workbench from './Workbench.vue'
defineProps({ api: { type: Object, default: () => ({}) }, pluginId: { type: String, default: 'AutoSubscribe' } })
defineEmits(['action', 'close'])
</script>
<template><Workbench :api="api" :plugin-id="pluginId" @action="$emit('action')" /></template>
