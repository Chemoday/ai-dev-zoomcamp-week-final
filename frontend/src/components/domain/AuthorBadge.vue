<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { useTranslations } from '@/locales/index.js'

const props = defineProps({
  badge: {
    type: Object, // { display_label: string, role_badge: 'OWNER' | 'RESIDENT' | 'BOARD' | 'ACCOUNTANT' }
    required: true,
  },
  isMine: {
    type: Boolean,
    default: false,
  },
})

const session = useSessionStore()
const t = computed(() => useTranslations(session.lang))

const localizedRole = computed(() => {
  const role = props.badge?.role_badge || 'RESIDENT'
  return t.value.roleBadge[role] || role
})
</script>

<template>
  <span class="inline-flex items-center gap-1.5 text-xs text-on-surface-variant">
    <span class="font-semibold text-on-surface">{{ badge.display_label }}</span>
    <span aria-hidden="true">•</span>
    <span
      class="font-label-md text-[11px] font-semibold uppercase tracking-wider"
      :class="badge.role_badge === 'BOARD' ? 'text-secondary' : 'text-on-surface-variant'"
    >
      {{ localizedRole }}
    </span>
    <span
      v-if="isMine"
      class="text-[11px] text-secondary font-semibold"
    >
      ({{ session.t('common.you') }})
    </span>
  </span>
</template>
