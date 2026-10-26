<script setup>
defineProps({
  padding: {
    type: String,
    default: 'p-space-lg',
  },
})
</script>

<template>
  <div
    class="bg-surface-container-lowest rounded-xl border border-surface-container shadow-xs"
    :class="padding"
  >
    <slot />
  </div>
</template>
