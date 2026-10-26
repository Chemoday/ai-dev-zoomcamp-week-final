<script setup>
import { computed, ref } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import { sumMeterValues } from '@/utils/meterValue.js'
import BaseModal from '@/components/base/BaseModal.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import ReadingsMatrixRow from './ReadingsMatrixRow.vue'
import MeterCard from './MeterCard.vue'

const props = defineProps({
  apartments: {
    type: Array,
    default: () => [],
  },
  periodCode: {
    type: String,
    required: true,
  },
  periodStatus: {
    type: String,
    default: 'OPEN_FOR_RESIDENTS',
  },
})

const emit = defineEmits(['open-history', 'reading-updated'])

const session = useSessionStore()
const { canSubmitAnyMeter } = useRole()

const entranceFilter = ref('ALL') // 'ALL' | 1 | 2
const statusFilter = ref('ALL') // 'ALL' | 'MISSING' | 'ANOMALY_CONFIRMED'
const searchQuery = ref('')
const currentPage = ref(1)
const pageSize = 12

const editingApartment = ref(null)

const myApartmentIds = computed(() =>
  new Set((session.me?.memberships || []).map((m) => m.apartment_id)),
)

const entrances = computed(() => {
  const set = new Set(props.apartments.map((a) => a.entrance))
  return Array.from(set).sort((a, b) => a - b)
})

const filteredApartments = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return props.apartments.filter((apt) => {
    if (entranceFilter.value !== 'ALL' && apt.entrance !== entranceFilter.value) {
      return false
    }
    if (statusFilter.value !== 'ALL' && apt.status !== statusFilter.value) {
      return false
    }
    if (q) {
      const matchNum = `apt ${apt.apartment_number}`.toLowerCase().includes(q)
      const matchStatus = apt.status.toLowerCase().includes(q)
      return matchNum || matchStatus
    }
    return true
  })
})

const totalPages = computed(() =>
  Math.max(1, Math.ceil(filteredApartments.value.length / pageSize)),
)

const pagedApartments = computed(() => {
  const start = (currentPage.value - 1) * pageSize
  return filteredApartments.value.slice(start, start + pageSize)
})

const summaryCounts = computed(() => {
  let verified = 0
  let estimated = 0
  let anomaly = 0
  let missing = 0
  for (const apt of props.apartments) {
    if (apt.status === 'SUBMITTED') verified += 1
    else if (apt.status === 'AUTO_ESTIMATED') estimated += 1
    else if (apt.status === 'ANOMALY_CONFIRMED') anomaly += 1
    else if (apt.status === 'MISSING') missing += 1
  }
  return { verified, estimated, anomaly, missing }
})

const coldWaterSubtotal = computed(() => {
  const deltas = []
  for (const apt of props.apartments) {
    for (const m of apt.meters) {
      if (m.resource_type === 'COLD_WATER' && m.reading?.consumption_delta) {
        deltas.push(m.reading.consumption_delta)
      }
    }
  }
  return sumMeterValues(deltas)
})

const hotWaterSubtotal = computed(() => {
  const deltas = []
  for (const apt of props.apartments) {
    for (const m of apt.meters) {
      if (m.resource_type === 'HOT_WATER' && m.reading?.consumption_delta) {
        deltas.push(m.reading.consumption_delta)
      }
    }
  }
  return sumMeterValues(deltas)
})

function setEntrance(val) {
  entranceFilter.value = val
  currentPage.value = 1
}

function handleOpenEdit(apt) {
  editingApartment.value = apt
}

function handleSavedInModal(reading) {
  emit('reading-updated', reading)
}
</script>

<template>
  <div>
    <!-- Filter & Search Bar -->
    <div class="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-space-sm mb-space-md bg-surface-container-lowest p-2.5 rounded-xl border border-surface-container shadow-2xs">
      <div class="flex flex-wrap items-center gap-space-sm">
        <div class="inline-flex p-1 bg-surface-container-low rounded-lg">
          <button
            type="button"
            class="px-3 py-1 rounded-md font-label-md text-label-md transition-all"
            :class="
              entranceFilter === 'ALL'
                ? 'bg-surface-container-lowest text-on-surface font-semibold shadow-2xs'
                : 'text-on-surface-variant hover:text-on-surface'
            "
            @click="setEntrance('ALL')"
          >
            {{ session.t('matrix.allFilter', { count: apartments.length }) }}
          </button>
          <button
            v-for="ent in entrances"
            :key="ent"
            type="button"
            class="px-3 py-1 rounded-md font-label-md text-label-md transition-all"
            :class="
              entranceFilter === ent
                ? 'bg-surface-container-lowest text-on-surface font-semibold shadow-2xs'
                : 'text-on-surface-variant hover:text-on-surface'
            "
            @click="setEntrance(ent)"
          >
            {{ session.t('matrix.entranceFilter', { entrance: ent }) }}
          </button>
        </div>

        <!-- Status Filter -->
        <div class="inline-flex p-1 bg-surface-container-low rounded-lg">
          <button
            type="button"
            class="px-2.5 py-1 rounded-md font-label-md text-xs transition-all"
            :class="
              statusFilter === 'ALL'
                ? 'bg-surface-container-lowest text-on-surface font-semibold shadow-2xs'
                : 'text-on-surface-variant hover:text-on-surface'
            "
            @click="statusFilter = 'ALL'; currentPage = 1"
          >
            {{ session.t('matrix.allStatuses') }}
          </button>
          <button
            type="button"
            class="px-2.5 py-1 rounded-md font-label-md text-xs transition-all"
            :class="
              statusFilter === 'ANOMALY_CONFIRMED'
                ? 'bg-error text-on-error font-semibold shadow-2xs'
                : 'text-on-surface-variant hover:text-on-surface'
            "
            @click="statusFilter = 'ANOMALY_CONFIRMED'; currentPage = 1"
          >
            {{ session.t('matrix.anomaliesFilter', { count: summaryCounts.anomaly }) }}
          </button>
          <button
            type="button"
            class="px-2.5 py-1 rounded-md font-label-md text-xs transition-all"
            :class="
              statusFilter === 'MISSING'
                ? 'bg-surface-container-lowest text-on-surface font-semibold shadow-2xs'
                : 'text-on-surface-variant hover:text-on-surface'
            "
            @click="statusFilter = 'MISSING'; currentPage = 1"
          >
            {{ session.t('matrix.missingFilter', { count: summaryCounts.missing }) }}
          </button>
        </div>
      </div>

      <div class="relative min-w-[240px]">
        <span class="material-symbols-outlined absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-variant text-[18px]">
          search
        </span>
        <input
          v-model="searchQuery"
          type="search"
          :placeholder="session.t('matrix.searchPlaceholder')"
          class="w-full pl-8 pr-space-sm py-1.5 bg-surface-container-low rounded-lg text-body-sm placeholder:text-on-surface-variant/70 focus:outline-none focus:ring-1 focus:ring-primary text-on-surface"
          @input="currentPage = 1"
        />
      </div>
    </div>

    <!-- APARTMENT CONSUMPTION LEDGER TABLE -->
    <div class="rounded-xl bg-surface-container-lowest shadow-2xs border border-surface-container overflow-hidden mb-space-xl">
      <div class="px-space-lg py-3 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-space-sm bg-surface-container-low/80 border-b border-surface-container">
        <div class="flex items-center gap-space-sm">
          <span class="font-label-lg text-label-lg font-bold text-on-surface">
            {{ session.t('matrix.ledgerTitle') }}
          </span>
          <span class="text-on-surface-variant font-body-sm text-[12px]">
            • {{ periodCode }} ({{ apartments.length }} units)
          </span>
        </div>

        <div class="flex items-center flex-wrap gap-space-md font-label-md text-label-md">
          <span class="text-secondary font-semibold">
            {{ session.t('matrix.verifiedCount', { count: summaryCounts.verified }) }}
          </span>
          <span class="text-on-surface-variant">•</span>
          <span class="text-on-surface-variant">
            {{ session.t('matrix.estimatedCount', { count: summaryCounts.estimated }) }}
          </span>
          <span class="text-on-surface-variant">•</span>
          <span class="text-error font-semibold">
            {{ session.t('matrix.anomalyCount', { count: summaryCounts.anomaly }) }}
          </span>
        </div>
      </div>

      <div v-if="pagedApartments.length > 0" class="overflow-x-auto">
        <table class="w-full text-left font-body-sm text-body-sm">
          <thead class="bg-surface-container/60 text-on-surface-variant font-label-md text-label-md uppercase tracking-wider border-b border-surface-container">
            <tr>
              <th class="py-space-sm px-space-md">{{ session.t('matrix.colUnit') }}</th>
              <th class="py-space-sm px-space-md">{{ session.t('matrix.colLedgerMode') }}</th>
              <th class="py-space-sm px-space-md whitespace-nowrap">{{ session.t('matrix.colColdWater') }}</th>
              <th class="py-space-sm px-space-md whitespace-nowrap">{{ session.t('matrix.colHotWater') }}</th>
              <th class="py-space-sm px-space-md text-right whitespace-nowrap">{{ session.t('matrix.colTotal') }}</th>
              <th class="py-space-sm px-space-md whitespace-nowrap">{{ session.t('matrix.colStatus') }}</th>
              <th class="py-space-sm px-space-md text-right whitespace-nowrap">{{ session.t('matrix.colActions') }}</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-surface-container">
            <ReadingsMatrixRow
              v-for="apt in pagedApartments"
              :key="apt.apartment_id"
              :apartment="apt"
              :is-my-apartment="myApartmentIds.has(apt.apartment_id)"
              @open-history="(id, label) => $emit('open-history', id, label)"
              @edit-apartment="handleOpenEdit"
            />
          </tbody>
        </table>
      </div>

      <div v-else class="p-space-lg">
        <BaseEmptyState
          icon="search_off"
          :title="session.t('matrix.noMatchingUnits')"
          :description="session.t('matrix.noMatchingUnitsDesc')"
        />
      </div>

      <!-- Table Footer with Subtotals & Pagination -->
      <div class="p-space-md bg-surface-container-low flex flex-col md:flex-row items-center justify-between gap-space-sm text-on-surface font-label-md text-label-md border-t border-surface-container">
        <div class="flex items-center flex-wrap gap-space-md">
          <span>
            {{
              session.t('matrix.showingUnits', {
                shown: pagedApartments.length,
                total: filteredApartments.length,
              })
            }}
          </span>
          <span class="text-on-surface-variant">•</span>
          <span class="text-on-surface-variant font-mono tabular-nums">
            Cold Water Subtotal: <strong class="text-on-surface">{{ coldWaterSubtotal }} m³</strong>
          </span>
          <span class="text-on-surface-variant">•</span>
          <span class="text-on-surface-variant font-mono tabular-nums">
            Hot Water Subtotal: <strong class="text-on-surface">{{ hotWaterSubtotal }} m³</strong>
          </span>
        </div>

        <div v-if="totalPages > 1" class="flex items-center gap-1">
          <button
            type="button"
            :disabled="currentPage <= 1"
            class="px-2.5 py-1 rounded bg-surface-container hover:bg-surface-container-high transition-colors text-on-surface disabled:opacity-40"
            @click="currentPage -= 1"
          >
            {{ session.t('common.previous') }}
          </button>
          <button
            v-for="p in totalPages"
            :key="p"
            type="button"
            class="px-2.5 py-1 rounded font-bold transition-colors"
            :class="
              p === currentPage
                ? 'bg-primary text-on-primary'
                : 'bg-surface-container hover:bg-surface-container-high text-on-surface'
            "
            @click="currentPage = p"
          >
            {{ p }}
          </button>
          <button
            type="button"
            :disabled="currentPage >= totalPages"
            class="px-2.5 py-1 rounded bg-surface-container hover:bg-surface-container-high transition-colors text-on-surface disabled:opacity-40"
            @click="currentPage += 1"
          >
            {{ session.t('common.next') }}
          </button>
        </div>
      </div>
    </div>

    <!-- Modal for Chairman / Accountant to edit an apartment's meters -->
    <BaseModal
      :open="Boolean(editingApartment)"
      :title="
        editingApartment
          ? session.t('matrix.editModalTitle', { number: editingApartment.apartment_number })
          : ''
      "
      :subtitle="
        editingApartment
          ? `Entrance ${editingApartment.entrance} • Floor ${editingApartment.floor} • Period ${periodCode}`
          : ''
      "
      max-width="max-w-2xl"
      @close="editingApartment = null"
    >
      <div v-if="editingApartment" class="grid grid-cols-1 gap-space-md">
        <MeterCard
          v-for="m in editingApartment.meters"
          :key="m.meter_id"
          :meter="{
            ...m,
            serial_number: `APT${editingApartment.apartment_number}-${m.resource_type.slice(0, 2)}`,
            previous_value: m.previous_value || '100.000',
          }"
          :period-code="periodCode"
          :period-status="periodStatus"
          :can-submit="canSubmitAnyMeter"
          @saved="handleSavedInModal"
          @open-history="(id, label) => $emit('open-history', id, label)"
        />
      </div>
    </BaseModal>
  </div>
</template>
