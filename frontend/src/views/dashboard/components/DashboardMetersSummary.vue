<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { sumMeterValues } from '@/utils/meterValue.js'
import { formatPeriodLabel } from '@/utils/dates.js'
import { useSessionStore } from '@/stores/session.js'

const props = defineProps({
  period: {
    type: Object,
    default: null,
  },
  apartments: {
    type: Array,
    default: () => [],
  },
  forumCount: {
    type: Number,
    default: 4,
  },
})

const router = useRouter()
const session = useSessionStore()

const activeApartment = computed(() => {
  if (!props.apartments.length) return null
  return (
    props.apartments.find((a) => a.apartment_id === session.selectedApartmentId) ||
    props.apartments[0]
  )
})

const waterConsumptionM3 = computed(() => {
  if (!activeApartment.value) return '0.000'
  const deltas = activeApartment.value.meters
    .filter((m) => m.unit === 'm3' && m.reading?.consumption_delta)
    .map((m) => m.reading.consumption_delta)
  return deltas.length ? sumMeterValues(deltas) : '0.000'
})

const allMetersSubmitted = computed(() => {
  if (!activeApartment.value || !activeApartment.value.meters.length) return false
  return activeApartment.value.meters.every((m) => m.reading !== null)
})

const submittedCount = computed(() => {
  if (!activeApartment.value) return 0
  return activeApartment.value.meters.filter((m) => m.reading !== null).length
})

const totalMeterCount = computed(() => activeApartment.value?.meters?.length || 0)

const periodName = computed(() =>
  props.period ? formatPeriodLabel(props.period.period_code, session.lang) : 'October 2026',
)
</script>

<template>
  <section class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-space-md mb-space-lg">
    <!-- 1. Monthly Utilities (Real API consumption sum if user has apartment, else building period status) -->
    <div
      v-if="activeApartment"
      class="bg-surface-container-lowest rounded-xl p-space-md shadow-xs border border-surface-container flex flex-col justify-between group hover:shadow-md transition-shadow"
    >
      <div class="flex items-start justify-between">
        <div class="flex flex-col">
          <span class="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant font-semibold">
            {{ session.t('dashboard.summary.monthlyUtilities') }}
          </span>
          <span class="font-headline-lg text-headline-lg font-bold text-on-surface mt-1 font-numeric-display tabular-nums">
            {{ waterConsumptionM3 }}
            <span class="font-body-md text-body-md text-on-surface-variant font-normal">m³</span>
          </span>
        </div>
        <div class="w-10 h-10 rounded-lg bg-surface-container flex items-center justify-center text-primary">
          <span class="material-symbols-outlined text-[20px]">water_drop</span>
        </div>
      </div>
      <div class="mt-space-md pt-space-sm bg-surface-container-low/50 -mx-space-md -mb-space-md px-space-md py-space-sm rounded-b-xl flex items-center justify-between">
        <div class="flex items-center gap-1.5" :class="allMetersSubmitted ? 'text-secondary' : 'text-amber-700'">
          <span class="material-symbols-outlined text-[16px]">
            {{ allMetersSubmitted ? 'check_circle' : 'schedule' }}
          </span>
          <span class="font-label-md text-label-md font-semibold">
            {{ submittedCount }}/{{ totalMeterCount }} {{ session.t('dashboard.summary.metersReported') }}
          </span>
        </div>
        <button
          type="button"
          class="font-label-md text-label-md text-primary font-semibold hover:underline"
          @click="router.push('/readings')"
        >
          {{ session.t('dashboard.summary.metersLink') }}
        </button>
      </div>
    </div>

    <div
      v-else
      class="bg-surface-container-lowest rounded-xl p-space-md shadow-xs border border-surface-container flex flex-col justify-between group hover:shadow-md transition-shadow"
    >
      <div class="flex items-start justify-between">
        <div class="flex flex-col">
          <span class="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant font-semibold">
            {{ session.t('dashboard.summary.activePeriod') }}
          </span>
          <span class="font-headline-lg text-headline-lg font-bold text-on-surface mt-1">
            {{ periodName }}
          </span>
        </div>
        <div class="w-10 h-10 rounded-lg bg-surface-container flex items-center justify-center text-primary">
          <span class="material-symbols-outlined text-[20px]">table_view</span>
        </div>
      </div>
      <div class="mt-space-md pt-space-sm bg-surface-container-low/50 -mx-space-md -mb-space-md px-space-md py-space-sm rounded-b-xl flex items-center justify-between">
        <span class="font-label-md text-label-md text-secondary font-semibold">
          {{ period?.status || 'OPEN_FOR_RESIDENTS' }}
        </span>
        <button
          type="button"
          class="font-label-md text-label-md text-primary font-semibold hover:underline"
          @click="router.push('/accounting')"
        >
          {{ session.t('dashboard.summary.openMatrixLink') }}
        </button>
      </div>
    </div>

    <!-- 2. Maintenance & Work Orders -->
    <div class="bg-surface-container-lowest rounded-xl p-space-md shadow-xs border border-surface-container flex flex-col justify-between group hover:shadow-md transition-shadow">
      <div class="flex items-start justify-between">
        <div class="flex flex-col">
          <span class="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant font-semibold">
            {{ session.t('dashboard.summary.maintenance') }}
          </span>
          <div class="flex items-baseline gap-2 mt-1">
            <span class="font-headline-lg text-headline-lg font-bold text-on-surface font-numeric-display">1</span>
            <span class="font-label-md text-label-md text-on-surface-variant">
              {{ session.t('dashboard.summary.activeOrder') }}
            </span>
          </div>
        </div>
        <div class="w-10 h-10 rounded-lg bg-surface-container flex items-center justify-center text-primary">
          <span class="material-symbols-outlined text-[20px]">build</span>
        </div>
      </div>
      <div class="mt-space-sm">
        <p class="font-body-sm text-body-sm text-on-surface line-clamp-1 font-medium">
          {{ session.t('dashboard.summary.elevatorInspection') }}
        </p>
      </div>
      <div class="mt-space-sm pt-space-xs flex items-center justify-between">
        <button
          type="button"
          class="font-label-md text-label-md text-primary font-semibold hover:underline flex items-center gap-1"
          @click="router.push('/forum')"
        >
          <span>{{ session.t('dashboard.summary.reportNewIssue') }}</span>
          <span class="material-symbols-outlined text-[14px]">add</span>
        </button>
      </div>
    </div>

    <!-- 3. Community Board -->
    <div class="bg-surface-container-lowest rounded-xl p-space-md shadow-xs border border-surface-container flex flex-col justify-between group hover:shadow-md transition-shadow">
      <div class="flex items-start justify-between">
        <div class="flex flex-col">
          <span class="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant font-semibold">
            {{ session.t('dashboard.summary.communityBoard') }}
          </span>
          <div class="flex items-baseline gap-2 mt-1">
            <span class="font-headline-lg text-headline-lg font-bold text-on-surface font-numeric-display">
              {{ forumCount }}
            </span>
            <span class="font-label-md text-label-md text-on-surface-variant">
              {{ session.t('dashboard.summary.activeTopics') }}
            </span>
          </div>
        </div>
        <div class="w-10 h-10 rounded-lg bg-surface-container flex items-center justify-center text-primary">
          <span class="material-symbols-outlined text-[20px]">forum</span>
        </div>
      </div>
      <div class="mt-space-sm">
        <p class="font-body-sm text-body-sm text-on-surface-variant line-clamp-1">
          {{ session.t('dashboard.summary.yardDayTopic') }}
        </p>
      </div>
      <div class="mt-space-sm pt-space-xs flex items-center justify-between">
        <button
          type="button"
          class="font-label-md text-label-md text-primary font-semibold hover:underline flex items-center gap-1"
          @click="router.push('/forum')"
        >
          <span>{{ session.t('dashboard.summary.openForumDiscussions') }}</span>
          <span class="material-symbols-outlined text-[14px]">arrow_outward</span>
        </button>
        <span class="font-label-md text-label-md text-secondary font-bold">
          {{ session.t('common.new') }}
        </span>
      </div>
    </div>

    <!-- 4. Building Treasury / Reserve (Phase 2 billing demo indicator) -->
    <div class="bg-surface-container-lowest rounded-xl p-space-md shadow-xs border border-surface-container flex flex-col justify-between group hover:shadow-md transition-shadow">
      <div class="flex items-start justify-between">
        <div class="flex flex-col">
          <span class="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant font-semibold">
            {{ session.t('dashboard.summary.reserveFundDemo') }}
          </span>
          <span class="font-headline-lg text-headline-lg font-bold text-on-surface mt-1 font-numeric-display tabular-nums">
            €48,250<span class="text-[1.125rem] font-normal text-on-surface-variant">.00</span>
          </span>
        </div>
        <div class="w-10 h-10 rounded-lg bg-surface-container flex items-center justify-center text-primary">
          <span class="material-symbols-outlined text-[20px]">account_balance</span>
        </div>
      </div>
      <div class="mt-space-md pt-space-sm bg-surface-container-low/50 -mx-space-md -mb-space-md px-space-md py-space-sm rounded-b-xl flex items-center justify-between">
        <span class="font-label-md text-label-md text-secondary font-semibold flex items-center gap-1">
          <span class="material-symbols-outlined text-[14px]">verified</span>
          <span>
            {{
              activeApartment
                ? session.t('dashboard.summary.aptArrearsZero', { number: activeApartment.apartment_number })
                : session.t('dashboard.summary.unitsBalanced')
            }}
          </span>
        </span>
        <span class="font-label-md text-label-md text-on-surface-variant">100%</span>
      </div>
    </div>
  </section>
</template>
