<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import {
  changePeriodStatus,
  estimateMissingReadings,
  exportPeriodCsv,
  getPeriodMatrix,
  listPeriods,
} from '@/api/endpoints/meters.js'
import { formatPeriodLabel } from '@/utils/dates.js'
import { localizeError } from '@/locales/index.js'
import PeriodToolbar from '@/views/readings/components/PeriodToolbar.vue'
import MasterMeterKpi from '@/views/readings/components/MasterMeterKpi.vue'
import ReadingsMatrix from '@/views/readings/components/ReadingsMatrix.vue'
import ReadingHistoryModal from '@/views/readings/components/ReadingHistoryModal.vue'
import AccountingBalanceTab from './components/AccountingBalanceTab.vue'
import BaseSkeleton from '@/components/base/BaseSkeleton.vue'
import ErrorCard from '@/components/domain/ErrorCard.vue'
import StatusChip from '@/components/domain/StatusChip.vue'

const session = useSessionStore()

const periodCode = ref('2026-10')
const periodsList = ref([])
const periodCodes = computed(() =>
  periodsList.value.length > 0
    ? periodsList.value.map((p) => p.period_code)
    : ['2026-10', '2026-09', '2026-08', '2026-07'],
)
const activeTab = ref('matrix') // 'matrix' | 'balance'
const matrix = ref(null)
const loading = ref(true)
const error = ref(null)

const busyAction = ref('')
const actionBanner = ref(null) // { tone: 'success' | 'error', text: string }
const csvSupported = ref(true)

const historyReadingId = ref('')
const historyTitle = ref('')

async function loadMatrix() {
  if (!session.actingUserId) return
  loading.value = true
  error.value = null
  try {
    if (!periodsList.value.length) {
      const fetchedPeriods = await listPeriods().catch(() => [])
      if (Array.isArray(fetchedPeriods) && fetchedPeriods.length > 0) {
        periodsList.value = fetchedPeriods
        if (!fetchedPeriods.some((p) => p.period_code === periodCode.value)) {
          periodCode.value = fetchedPeriods[0].period_code
        }
      }
    }
    const data = await getPeriodMatrix(periodCode.value)
    matrix.value = data
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

onMounted(loadMatrix)

watch(
  () => [session.actingUserId, session.sessionRevision, periodCode.value],
  () => {
    matrix.value = null
    actionBanner.value = null
    loadMatrix()
  },
)

const apartments = computed(() => matrix.value?.apartments || [])
const balances = computed(() => matrix.value?.balances || [])
const masterMeters = computed(() => matrix.value?.master_meters || [])
const period = computed(() => matrix.value?.period || null)

const missingCount = computed(
  () => apartments.value.filter((a) => a.status === 'MISSING').length,
)

const reportedUnitsCount = computed(
  () => apartments.value.filter((a) => a.status !== 'MISSING').length,
)

async function handleStatusChange(newStatus) {
  if (busyAction.value) return
  busyAction.value = 'status'
  actionBanner.value = null
  try {
    await changePeriodStatus(periodCode.value, newStatus)
    await loadMatrix()
    actionBanner.value = {
      tone: 'success',
      text: session.t('accounting.statusUpdatedBanner', { status: newStatus }),
    }
  } catch (err) {
    actionBanner.value = {
      tone: 'error',
      text: localizeError(err.errorCode, err.payload, session.lang),
    }
  } finally {
    busyAction.value = ''
  }
}

async function handleEstimateMissing() {
  if (busyAction.value) return
  busyAction.value = 'estimate'
  actionBanner.value = null
  try {
    const res = await estimateMissingReadings(periodCode.value)
    await loadMatrix()
    actionBanner.value = {
      tone: 'success',
      text: session.t('accounting.estimatedBanner', { count: res.estimated_count }),
    }
  } catch (err) {
    actionBanner.value = {
      tone: 'error',
      text: localizeError(err.errorCode, err.payload, session.lang),
    }
  } finally {
    busyAction.value = ''
  }
}

async function handleExportCsv() {
  if (busyAction.value) return
  busyAction.value = 'csv'
  actionBanner.value = null
  try {
    const response = await exportPeriodCsv(periodCode.value)
    const blob = await response.blob()
    const disposition = response.headers.get('Content-Disposition') || ''
    const match = disposition.match(/filename="?([^";]+)"?/i)
    const filename = match ? match[1] : `readings-${periodCode.value}.csv`

    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)

    actionBanner.value = {
      tone: 'success',
      text: session.t('accounting.csvDownloadedBanner', { filename }),
    }
  } catch (err) {
    if (err.errorCode === 'EXPORTER_NOT_ENABLED') {
      csvSupported.value = false
    }
    actionBanner.value = {
      tone: 'error',
      text: localizeError(err.errorCode, err.payload, session.lang),
    }
  } finally {
    busyAction.value = ''
  }
}

function openHistory(readingId, label) {
  historyReadingId.value = readingId
  historyTitle.value = label
}
</script>

<template>
  <div class="w-full max-w-[1280px] mx-auto px-gutter py-space-lg">
    <!-- Header & Context Area matching chairman_view_streamlined_meter_matrix_accounting -->
    <div class="flex flex-col gap-space-md mb-space-lg">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-space-sm pb-1">
        <div class="flex flex-col gap-0.5">
          <div class="flex items-center gap-space-xs text-on-surface-variant font-label-md text-[11px] tracking-wider uppercase font-medium">
            <span>{{ session.t('accounting.breadcrumb') }}</span>
            <span>/</span>
            <span>{{ session.buildingName }} • Reg: {{ session.buildingRegistryCode }}</span>
          </div>
          <h1 class="font-headline-lg text-headline-lg text-on-surface tracking-tight font-bold">
            {{ session.t('accounting.title') }}
          </h1>
          <p class="text-on-surface-variant font-body-sm text-body-sm">
            {{ session.t('accounting.billingPeriod') }}
            {{ formatPeriodLabel(periodCode, session.lang) }} •
            {{ apartments.length || session.buildingApartmentsCount }} {{ session.t('accounting.unitsTotal') }}
          </p>
        </div>

        <div v-if="period" class="flex items-center gap-space-sm self-start sm:self-auto">
          <StatusChip :status="period.status" />
        </div>
      </div>

      <!-- Sub-Nav Bar -->
      <div class="border-b border-surface-container-high">
        <nav aria-label="Accounting Views" class="flex items-center gap-space-xs overflow-x-auto pb-px">
          <button
            type="button"
            class="flex items-center gap-2 px-space-md py-2.5 border-b-2 font-label-lg text-label-lg whitespace-nowrap rounded-t-lg transition-colors"
            :class="
              activeTab === 'matrix'
                ? 'border-primary text-on-surface bg-surface-container-lowest/50 font-bold'
                : 'border-transparent text-on-surface-variant hover:text-on-surface'
            "
            @click="activeTab = 'matrix'"
          >
            <span class="material-symbols-outlined text-[19px]">table_view</span>
            <span>{{ session.t('accounting.tabMatrix') }}</span>
            <span class="px-2 py-0.5 rounded-full bg-secondary/15 text-secondary text-label-md font-semibold font-mono tabular-nums">
              {{ reportedUnitsCount }}/{{ apartments.length || 36 }}
            </span>
          </button>

          <button
            type="button"
            class="flex items-center gap-2 px-space-md py-2.5 border-b-2 font-label-lg text-label-lg whitespace-nowrap rounded-t-lg transition-colors"
            :class="
              activeTab === 'balance'
                ? 'border-primary text-on-surface bg-surface-container-lowest/50 font-bold'
                : 'border-transparent text-on-surface-variant hover:text-on-surface'
            "
            @click="activeTab = 'balance'"
          >
            <span class="material-symbols-outlined text-[19px]">balance</span>
            <span>{{ session.t('accounting.tabBalance') }}</span>
          </button>

          <button
            v-if="csvSupported"
            type="button"
            class="flex items-center gap-2 px-space-md py-2.5 border-b-2 border-transparent text-on-surface-variant hover:text-on-surface font-label-lg text-label-lg whitespace-nowrap transition-colors"
            @click="handleExportCsv"
          >
            <span class="material-symbols-outlined text-[19px]">file_download</span>
            <span>{{ session.t('accounting.exportCsvTab') }}</span>
          </button>
        </nav>
      </div>
    </div>

    <!-- Period & Action Toolbar -->
    <PeriodToolbar
      class="mb-space-md"
      :period-code="periodCode"
      :period-status="period?.status || 'OPEN_FOR_RESIDENTS'"
      :periods="periodCodes"
      :missing-count="missingCount"
      :busy-action="busyAction"
      :csv-supported="csvSupported"
      @change-period="(code) => (periodCode = code)"
      @change-status="handleStatusChange"
      @estimate-missing="handleEstimateMissing"
      @export-csv="handleExportCsv"
    />

    <!-- Action Feedback Banner -->
    <div
      v-if="actionBanner"
      role="status"
      class="mb-space-md p-3 rounded-xl font-label-md text-xs font-semibold flex items-center justify-between"
      :class="
        actionBanner.tone === 'error'
          ? 'bg-error-container text-on-error-container'
          : 'bg-secondary-container/50 text-on-secondary-container'
      "
    >
      <div class="flex items-center gap-2">
        <span class="material-symbols-outlined text-[18px]">
          {{ actionBanner.tone === 'error' ? 'error' : 'check_circle' }}
        </span>
        <span>{{ actionBanner.text }}</span>
      </div>
      <button type="button" class="underline text-xs" @click="actionBanner = null">
        {{ session.t('common.dismiss') }}
      </button>
    </div>

    <ErrorCard v-if="error" :error="error" @retry="loadMatrix" />

    <div v-else-if="loading" class="py-8">
      <BaseSkeleton :lines="6" />
    </div>

    <template v-else-if="matrix">
      <!-- 4 KPI Strip + Master Meter Submission Modal -->
      <MasterMeterKpi
        :balances="balances"
        :master-meters="masterMeters"
        :apartments="apartments"
        :period-code="periodCode"
        :period-status="period?.status || 'OPEN_FOR_RESIDENTS'"
        @master-saved="loadMatrix"
        @open-history="openHistory"
      />

      <!-- Tab 1: Readings Matrix Table -->
      <ReadingsMatrix
        v-if="activeTab === 'matrix'"
        :apartments="apartments"
        :period-code="periodCode"
        :period-status="period?.status || 'OPEN_FOR_RESIDENTS'"
        @open-history="openHistory"
        @reading-updated="loadMatrix"
      />

      <!-- Tab 2: Building Balance & Phase 2 Demo Preview -->
      <AccountingBalanceTab
        v-else
        :balances="balances"
        :master-meters="masterMeters"
        :period-code="periodCode"
      />
    </template>

    <!-- Audit History Modal -->
    <ReadingHistoryModal
      :reading-id="historyReadingId"
      :title-label="historyTitle"
      @close="historyReadingId = ''"
    />
  </div>
</template>
