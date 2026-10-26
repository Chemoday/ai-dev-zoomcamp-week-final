<script setup>
defineProps({
  lines: {
    type: Number,
    default: 3,
  },
})
</script>

<template>
  <div class="animate-pulse space-y-2.5 py-2" aria-busy="true">
    <div
      v-for="idx in lines"
      :key="idx"
      class="h-4 bg-surface-container rounded"
      :class="idx === lines ? 'w-2/3' : 'w-full'"
    />
  </div>
</template>
