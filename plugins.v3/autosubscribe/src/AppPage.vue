<script setup>
import Page from './Page.vue'
defineProps({ api: { type: Object, default: () => ({}) }, pluginId: { type: String, default: 'AutoSubscribe' } })
defineEmits(['action', 'close'])
</script>
<template><Page :api="api" :plugin-id="pluginId" @action="$emit('action')" @close="$emit('close')" /></template>
