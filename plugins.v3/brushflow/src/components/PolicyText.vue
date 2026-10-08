<script setup>
defineProps({ short: { type: String, default: '' }, full: { type: String, default: '' } })
</script>

<template>
  <span class="policy-text">
    <VTooltip :text="full" location="top" max-width="520">
      <template #activator="{ props }">
        <span v-bind="props" class="policy-text__short" tabindex="0">{{ short }}</span>
      </template>
    </VTooltip>
    <span class="policy-text__full">{{ full }}</span>
  </span>
</template>

<style scoped>
.policy-text { overflow-wrap: anywhere; }
.policy-text__short { cursor: help; }
.policy-text__full { display: none; }
@media (max-width: 699px) {
  .policy-text__short { display: none; }
  .policy-text__full { display: inline; }
}
</style>
