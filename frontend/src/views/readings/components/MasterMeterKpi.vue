<script setup>
import { computed, ref } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import { toScaledBigInt } from '@/utils/meterValue.js'
import BaseModal from '@/components/base/BaseModal.vue'
import MeterCard from './MeterCard.vue'

const props = defineProps({
  balances: {
    type: Array,
    default: () => [],
  },
  masterMeters: {
    type: Array,
    default: () => [],
  },
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

const emit = defineEmits(['master-saved', 'open-history'])

const session = useSessionStore()
const { canSubmitAnyMeter } = useRole()
const masterModalOpen = ref(false)

// Aggregate Cold + Hot water balance or pick primary water balance
const waterBalance = computed(() => {
  const cold = props.balances.find((b) => b.resource_type === 'COLD_WATER')
  const hot = props.balances.find((b) => b.resource_type === 'HOT_WATER')
  if (!cold && !hot) return props.balances[0] || null

  const masterCold = toScaledBigInt(cold?.master_total)
  const masterHot = toScaledBigInt(hot?.master_total)
  const aptCold = toScaledBigInt(cold?.apartments_sum) || 0n
  const aptHot = toScaledBigInt(hot?.apartments_sum) || 0n

  const hasMaster = masterCold !== null || masterHot !== null
  const masterSum = (masterCold || 0n) + (masterHot || 0n)
  const aptSum = aptCold + aptHot
  const loss = hasMaster ? masterSum - aptSum : null

  const format = (val) => {
    if (val === null) return null
    const neg = val < 0n
    const abs = neg ? -val : val
    return `${neg ? '-' : ''}${(abs / 1000n).toString()}.${String(abs % 1000n).padStart(3, '0')}`
  }

  let pct = '0.00%'
  if (hasMaster && masterSum > 0n && loss !== null) {
    const basisPoints = (loss * 10000n) / masterSum
    const whole = basisPoints / 100n
    const rem = (basisPoints < 0n ? -basisPoints : basisPoints) % 100n
    pct = `${whole.toString()}.${String(rem).padStart(2, '0')}%`
  }

  return {
    unit: 'm³',
    master_total: hasMaster ? format(masterSum) : null,
    apartments_sum: format(aptSum),
    unallocated_loss: format(loss),
    lossPercentage: pct,
    isNegativeLoss: loss !== null && loss < 0n,
  }
})

const counts = computed(() => {
  const total = props.apartments.length
  let submitted = 0
  let missing = 0
  let estimated = 0
  let anomaly = 0

  for (const apt of props.apartments) {
    if (apt.status === 'SUBMITTED') submitted += 1
    else if (apt.status === 'MISSING') missing += 1
    else if (apt.status === 'AUTO_ESTIMATED') estimated += 1
    else if (apt.status === 'ANOMALY_CONFIRMED') anomaly += 1
  }

  const reportedPct = total > 0 ? Math.round(((total - missing) / total) * 1000) / 10 : 100

  return {
    total,
    submitted,
    missing,
    estimated,
    anomaly,
    attention: missing + estimated + anomaly,
    reportedPct,
  }
})

function handleMasterSaved(reading) {
  emit('master-saved', reading)
}
</script>

<template>
  <div>
    <!-- CLEAN KPI STRIP (4 cards in one row matching chairman_view_streamlined_meter_matrix_accounting) -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-space-md mb-space-lg">
      <!-- 1. Basement Intake (Main) -->
      <div class="p-space-md rounded-xl bg-surface-container-lowest border border-surface-container shadow-2xs flex flex-col justify-between">
        <div class="flex items-center justify-between text-on-surface-variant">
          <span class="font-label-md text-label-md uppercase tracking-wider font-semibold">
            {{ session.t('masterKpi.basementIntake') }}
          </span>
          <span class="material-symbols-outlined text-[20px] text-surface-tint">water_drop</span>
        </div>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="font-numeric-display tabular-nums text-[1.65rem] font-bold text-on-surface">
            {{ waterBalance?.master_total ?? '—' }}
          </span>
          <span class="text-on-surface-variant font-label-md text-[13px]">
            {{ waterBalance?.unit || 'm³' }}
          </span>
        </div>
        <div class="mt-1 flex items-center justify-between text-[12px]">
          <span class="text-on-surface-variant font-label-md">
            {{ session.t('masterKpi.masterMetersCount', { count: masterMeters.length }) }}
          </span>
          <button
            v-if="canSubmitAnyMeter && masterMeters.length > 0"
            type="button"
            class="text-secondary font-semibold hover:underline"
            @click="masterModalOpen = true"
          >
            {{ session.t('masterKpi.editMasterLink') }}
          </button>
        </div>
      </div>

      <!-- 2. Subtotal Submitted -->
      <div class="p-space-md rounded-xl bg-surface-container-lowest border border-surface-container shadow-2xs flex flex-col justify-between">
        <div class="flex items-center justify-between text-on-surface-variant">
          <span class="font-label-md text-label-md uppercase tracking-wider font-semibold">
            {{ session.t('masterKpi.subtotalSubmitted') }}
          </span>
          <span class="material-symbols-outlined text-[20px] text-secondary">fact_check</span>
        </div>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="font-numeric-display tabular-nums text-[1.65rem] font-bold text-on-surface">
            {{ waterBalance?.apartments_sum ?? '0.000' }}
          </span>
          <span class="text-on-surface-variant font-label-md text-[13px]">
            {{ waterBalance?.unit || 'm³' }}
          </span>
        </div>
        <span class="mt-1 text-secondary font-label-md text-[12px] font-medium">
          {{ session.t('masterKpi.reportedPct', { pct: counts.reportedPct }) }} •
          {{ session.t('masterKpi.pendingCount', { count: counts.missing }) }}
        </span>
      </div>

      <!-- 3. Building Delta (Üldvesi) -->
      <div class="p-space-md rounded-xl bg-surface-container-lowest border border-surface-container shadow-2xs flex flex-col justify-between">
        <div class="flex items-center justify-between text-on-surface-variant">
          <span class="font-label-md text-label-md uppercase tracking-wider font-semibold">
            {{ session.t('masterKpi.buildingDelta') }}
          </span>
          <span class="material-symbols-outlined text-[20px] text-surface-tint">difference</span>
        </div>
        <div class="mt-2 flex items-baseline gap-2">
          <span
            class="font-numeric-display tabular-nums text-[1.65rem] font-bold"
            :class="waterBalance?.isNegativeLoss ? 'text-error' : 'text-on-surface'"
          >
            {{ waterBalance?.unallocated_loss ?? '—' }}
          </span>
          <span class="text-on-surface-variant font-label-md text-[13px]">
            {{ waterBalance?.unit || 'm³' }}
          </span>
        </div>
        <span class="mt-1 text-on-surface-variant font-label-md text-[12px]">
          <strong class="text-on-surface">{{ waterBalance?.lossPercentage || '—' }}</strong> •
          {{
            waterBalance?.isNegativeLoss
              ? session.t('masterKpi.negativeLossWarning')
              : session.t('masterKpi.withinNorm')
          }}
        </span>
      </div>

      <!-- 4. Needs Attention -->
      <div class="p-space-md rounded-xl bg-surface-container-lowest border border-surface-container shadow-2xs flex flex-col justify-between">
        <div class="flex items-center justify-between text-on-surface-variant">
          <span class="font-label-md text-label-md uppercase tracking-wider font-semibold">
            {{ session.t('masterKpi.needsAttention') }}
          </span>
          <span class="material-symbols-outlined text-[20px] text-error">notification_important</span>
        </div>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="font-numeric-display tabular-nums text-[1.65rem] font-bold text-error">
            {{ session.t('masterKpi.unitsCount', { count: counts.attention }) }}
          </span>
        </div>
        <span class="mt-1 text-on-surface-variant font-label-md text-[12px]">
          <span class="text-error font-semibold">{{ counts.anomaly }} Anomaly</span> •
          {{ counts.estimated }} Estimated • {{ counts.missing }} Missing
        </span>
      </div>
    </div>

    <!-- Modal to submit/override Building Master Meters -->
    <BaseModal
      :open="masterModalOpen"
      :title="session.t('masterKpi.masterModalTitle')"
      :subtitle="`Period ${periodCode}`"
      max-width="max-w-2xl"
      @close="masterModalOpen = false"
    >
      <div class="grid grid-cols-1 gap-space-md">
        <MeterCard
          v-for="mm in masterMeters"
          :key="mm.meter_id"
          :meter="mm"
          :period-code="periodCode"
          :period-status="periodStatus"
          :can-submit="canSubmitAnyMeter"
          @saved="handleMasterSaved"
          @open-history="(id, label) => $emit('open-history', id, label)"
        />
      </div>
    </BaseModal>
  </div>
</template>
