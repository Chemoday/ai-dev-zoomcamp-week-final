<script setup>
import { ref } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { exportPeriodCsv } from '@/api/endpoints/meters.js'
import { localizeError } from '@/locales/index.js'
import { formatPeriodLabel } from '@/utils/dates.js'
import BaseCard from '@/components/base/BaseCard.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import StatusChip from '@/components/domain/StatusChip.vue'

defineProps({
  periods: {
    type: Array,
    default: () => [],
  },
})

const session = useSessionStore()
const downloadingCode = ref(null)
const errorMsg = ref('')

async function handleExport(periodCode) {
  errorMsg.value = ''
  downloadingCode.value = periodCode
  try {
    const response = await exportPeriodCsv(periodCode)
    const blob = await response.blob()
    const disposition = response.headers.get('Content-Disposition') || ''
    const match = disposition.match(/filename="?([^";]+)"?/i)
    const filename = match ? match[1] : `readings_${periodCode}.csv`

    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  } catch (err) {
    errorMsg.value = localizeError(err.errorCode, err.payload, session.lang)
  } finally {
    downloadingCode.value = null
  }
}
</script>

<template>
  <BaseCard>
    <div class="flex items-center justify-between gap-2 mb-4">
      <div class="flex items-center gap-2.5">
        <span class="w-8 h-8 rounded-lg bg-secondary/15 text-primary inline-flex items-center justify-center">
          <span class="material-symbols-outlined text-[18px]">table_view</span>
        </span>
        <div>
          <h2 class="text-base font-bold text-on-surface">
            {{ session.t('documents.csvExportsTitle') }}
          </h2>
          <p class="text-xs text-on-surface-variant">
            {{ session.t('documents.csvExportsSub') }}
          </p>
        </div>
      </div>
    </div>

    <p v-if="errorMsg" class="mb-3 text-xs text-error font-medium">
      {{ errorMsg }}
    </p>

    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
      <div
        v-for="p in periods"
        :key="p.code"
        class="p-4 rounded-xl bg-surface-container-low/70 border border-surface-container-high flex flex-col justify-between gap-3"
      >
        <div>
          <div class="flex items-center justify-between gap-2 mb-1">
            <span class="font-mono text-xs font-bold text-primary">{{ p.code }}</span>
            <StatusChip :status="p.status" />
          </div>
          <div class="text-sm font-bold text-on-surface">
            {{ formatPeriodLabel(p.code, session.lang) }}
          </div>
          <div class="text-[11px] font-mono text-on-surface-variant mt-0.5">
            readings_{{ p.code }}.csv
          </div>
        </div>

        <BaseButton
          variant="secondary"
          size="sm"
          :loading="downloadingCode === p.code"
          @click="handleExport(p.code)"
        >
          <span class="material-symbols-outlined text-[16px]">download</span>
          <span>{{ session.t('common.exportCsv') }}</span>
        </BaseButton>
      </div>
    </div>
  </BaseCard>
</template>
