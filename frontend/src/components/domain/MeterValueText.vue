<script setup>
import { computed } from 'vue'

const props = defineProps({
  value: {
    type: String,
    default: null,
  },
  unit: {
    type: String,
    required: true, // 'm3' | 'kWh' | 'MWh'
  },
  size: {
    type: String,
    default: 'md', // 'sm' | 'md' | 'lg'
  },
})

const displayUnit = computed(() => (props.unit === 'm3' ? 'm³' : props.unit))
</script>

<template>
  <span class="inline-flex items-baseline gap-1 font-numeric-display tabular-nums">
    <span
      :class="[
        size === 'lg'
          ? 'text-[1.65rem] font-bold text-on-surface'
          : size === 'sm'
            ? 'text-xs font-semibold text-on-surface'
            : 'text-[15px] font-semibold text-on-surface',
      ]"
    >
      {{ value !== null && value !== undefined ? value : '—' }}
    </span>
    <span class="text-on-surface-variant font-label-md text-xs">{{ displayUnit }}</span>
  </span>
</template>
