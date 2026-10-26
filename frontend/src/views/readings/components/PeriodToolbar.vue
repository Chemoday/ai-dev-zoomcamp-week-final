<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import { formatPeriodLabel } from '@/utils/dates.js'
import StatusChip from '@/components/domain/StatusChip.vue'

const props = defineProps({
  periodCode: {
    type: String,
    required: true,
  },
  periodStatus: {
    type: String,
    default: 'OPEN_FOR_RESIDENTS',
  },
  periods: {
    type: Array,
    default: () => ['2026-10', '2026-09', '2026-08', '2026-07'],
  },
  missingCount: {
    type: Number,
    default: 0,
  },
  busyAction: {
    type: String,
    default: '',
  },
  csvSupported: {
    type: Boolean,
    default: true,
  },
})

defineEmits(['change-period', 'change-status', 'estimate-missing', 'export-csv'])

const session = useSessionStore()
const { canManagePeriods, canViewMatrix } = useRole()

// Period status transitions: OPEN_FOR_RESIDENTS <-> REVIEW_BY_BOARD <-> LOCKED, one step at a time
const allowedNextStatuses = computed(() => {
  switch (props.periodStatus) {
    case 'OPEN_FOR_RESIDENTS':
      return [{ status: 'REVIEW_BY_BOARD', label: session.t('periodToolbar.moveToReview') }]
    case 'REVIEW_BY_BOARD':
      return [
        { status: 'OPEN_FOR_RESIDENTS', label: session.t('periodToolbar.reopenForResidents') },
        { status: 'LOCKED', label: session.t('periodToolbar.lockForBilling') },
      ]
    case 'LOCKED':
      return [{ status: 'REVIEW_BY_BOARD', label: session.t('periodToolbar.unlockToReview') }]
    default:
      return []
  }
})
</script>

<template>
  <div class="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-space-sm bg-surface-container-lowest p-3 rounded-xl border border-surface-container shadow-2xs">
    <div class="flex flex-wrap items-center gap-space-sm">
      <!-- Period Selector -->
      <div class="flex items-center gap-1.5">
        <label for="period-selector" class="font-label-md text-xs text-on-surface-variant font-semibold">
          {{ session.t('common.period') }}
        </label>
        <select
          id="period-selector"
          :value="periodCode"
          class="h-9 px-3 rounded-lg bg-surface-container-low text-on-surface font-label-md text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-primary"
          @change="$emit('change-period', $event.target.value)"
        >
          <option v-for="code in periods" :key="code" :value="code">
            {{ formatPeriodLabel(code, session.lang) }} ({{ code }})
          </option>
        </select>
      </div>

      <StatusChip :status="periodStatus" />
    </div>

    <!-- Board / Chairman / Accountant Controls -->
    <div v-if="canViewMatrix" class="flex flex-wrap items-center gap-2 lg:ml-auto">
      <template v-if="canManagePeriods">
        <button
          v-for="next in allowedNextStatuses"
          :key="next.status"
          type="button"
          :disabled="Boolean(busyAction)"
          class="px-3 py-1.5 rounded-lg font-label-md text-xs font-semibold transition-colors inline-flex items-center gap-1.5 disabled:opacity-50"
          :class="
            next.status === 'LOCKED'
              ? 'bg-primary text-on-primary hover:bg-primary-container'
              : 'bg-surface-container hover:bg-surface-container-high text-on-surface'
          "
          @click="$emit('change-status', next.status)"
        >
          <span class="material-symbols-outlined text-[16px]">
            {{ next.status === 'LOCKED' ? 'lock' : 'sync_alt' }}
          </span>
          <span>{{ next.label }}</span>
        </button>

        <button
          v-if="periodStatus === 'REVIEW_BY_BOARD'"
          type="button"
          :disabled="Boolean(busyAction)"
          class="px-3 py-1.5 rounded-lg bg-secondary text-on-secondary hover:opacity-90 font-label-md text-xs font-semibold transition-opacity inline-flex items-center gap-1.5 disabled:opacity-50"
          @click="$emit('estimate-missing')"
        >
          <span
            class="material-symbols-outlined text-[16px]"
            :class="{ 'animate-spin': busyAction === 'estimate' }"
          >
            {{ busyAction === 'estimate' ? 'sync' : 'calculate' }}
          </span>
          <span>
            {{ session.t('periodToolbar.autoFillEstimated', { count: missingCount }) }}
          </span>
        </button>
      </template>

      <button
        v-if="csvSupported"
        type="button"
        :disabled="Boolean(busyAction)"
        class="px-3 py-1.5 rounded-lg bg-surface-container-low hover:bg-surface-container border border-surface-container text-on-surface font-label-md text-xs font-semibold transition-colors inline-flex items-center gap-1.5 disabled:opacity-50"
        @click="$emit('export-csv')"
      >
        <span class="material-symbols-outlined text-[16px]">download</span>
        <span>{{ session.t('common.exportCsv') }}</span>
      </button>
    </div>
  </div>
</template>
