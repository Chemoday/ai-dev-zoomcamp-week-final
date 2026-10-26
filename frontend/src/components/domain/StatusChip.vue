<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { useTranslations } from '@/locales/index.js'

const props = defineProps({
  status: {
    type: String,
    required: true, // SUBMITTED | MISSING | AUTO_ESTIMATED | ANOMALY_CONFIRMED | ADMIN_OVERRIDE | OPEN_FOR_RESIDENTS | REVIEW_BY_BOARD | LOCKED
  },
})

const session = useSessionStore()
const t = computed(() => useTranslations(session.lang))

const label = computed(() => t.value.status[props.status] || props.status)

const classes = computed(() => {
  switch (props.status) {
    case 'SUBMITTED':
    case 'OPEN_FOR_RESIDENTS':
      return 'bg-secondary-fixed/30 text-on-secondary-fixed-variant'
    case 'ANOMALY_CONFIRMED':
    case 'MISSING':
      return 'bg-error-container text-on-error-container'
    case 'ADMIN_OVERRIDE':
      return 'bg-primary-container text-on-primary'
    case 'REVIEW_BY_BOARD':
      return 'bg-secondary-container/50 text-on-secondary-container'
    default:
      return 'bg-surface-container-high text-on-surface'
  }
})

const icon = computed(() => {
  switch (props.status) {
    case 'SUBMITTED':
    case 'OPEN_FOR_RESIDENTS':
      return 'check_circle'
    case 'ANOMALY_CONFIRMED':
      return 'priority_high'
    case 'MISSING':
      return 'schedule'
    case 'AUTO_ESTIMATED':
      return 'calculate'
    case 'ADMIN_OVERRIDE':
      return 'shield'
    case 'LOCKED':
      return 'lock'
    default:
      return 'verified'
  }
})
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full font-label-md text-label-md font-semibold whitespace-nowrap shrink-0"
    :class="classes"
  >
    <span class="material-symbols-outlined text-[14px]">{{ icon }}</span>
    <span>{{ label }}</span>
  </span>
</template>
