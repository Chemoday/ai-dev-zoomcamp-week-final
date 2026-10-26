<script setup>
import { computed, ref, watch } from 'vue'
import { submitReading } from '@/api/endpoints/meters.js'
import {
  formatSignedDelta,
  normalizeMeterInput,
  subtractMeterValues,
} from '@/utils/meterValue.js'
import {
  createAnomalyFlowState,
  prepareReadingSubmission,
  resetAnomalyOnEdit,
  transitionOnReadingError,
} from '@/utils/anomalyFlow.js'
import { localizeError } from '@/locales/index.js'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import StatusChip from '@/components/domain/StatusChip.vue'
import MeterCardAnomalyModal from './MeterCardAnomalyModal.vue'

const props = defineProps({
  meter: {
    type: Object, // DashboardMeter or MasterMeterRow
    required: true,
  },
  periodCode: {
    type: String,
    required: true,
  },
  periodStatus: {
    type: String,
    default: 'OPEN_FOR_RESIDENTS',
  },
  canSubmit: {
    type: Boolean,
    default: true,
  },
})

const emit = defineEmits(['saved', 'open-history', 'draft-change'])

const session = useSessionStore()
const { canSubmitAnyMeter } = useRole()

const input = ref(props.meter.reading?.value || '')
const reason = ref('')
const submitting = ref(false)
const flow = ref(createAnomalyFlowState())

watch(
  () => props.meter.reading?.value,
  (newVal) => {
    if (newVal) {
      input.value = newVal
    }
  },
)

const displayUnit = computed(() => (props.meter.unit === 'm3' ? 'm³' : props.meter.unit))

const resourceLabel = computed(() => {
  switch (props.meter.resource_type) {
    case 'COLD_WATER':
      return session.t('readings.meterCard.coldWater')
    case 'HOT_WATER':
      return session.t('readings.meterCard.hotWater')
    case 'ELECTRICITY':
      return session.t('readings.meterCard.electricity')
    case 'HEAT':
      return session.t('readings.meterCard.thermalEnergy')
    default:
      return props.meter.resource_type
  }
})

const resourceIcon = computed(() => {
  switch (props.meter.resource_type) {
    case 'COLD_WATER':
      return 'water_drop'
    case 'HOT_WATER':
      return 'mode_heat'
    case 'ELECTRICITY':
      return 'bolt'
    default:
      return 'speed'
  }
})

const resourceIconClass = computed(() => {
  switch (props.meter.resource_type) {
    case 'COLD_WATER':
      return 'text-blue-600'
    case 'HOT_WATER':
      return 'text-amber-600'
    case 'ELECTRICITY':
      return 'text-amber-500'
    default:
      return 'text-secondary'
  }
})

// Live consumption delta computed via scaled BigInt
const liveDelta = computed(() => {
  const normalized = normalizeMeterInput(input.value)
  if (normalized !== null && props.meter.previous_value) {
    return subtractMeterValues(normalized, props.meter.previous_value)
  }
  return props.meter.reading?.consumption_delta || null
})

// Board/Accountant changing an existing reading must provide a reason (REASON_REQUIRED)
const needsReasonField = computed(
  () => Boolean(props.meter.reading) && canSubmitAnyMeter.value,
)

const isLockedByBoardForResident = computed(
  () =>
    props.meter.reading?.source === 'ADMIN_OVERRIDE' && !canSubmitAnyMeter.value,
)

const isInputDisabled = computed(() => {
  if (!props.canSubmit) return true
  if (props.periodStatus === 'LOCKED') return true
  if (props.periodStatus !== 'OPEN_FOR_RESIDENTS' && !canSubmitAnyMeter.value) return true
  if (isLockedByBoardForResident.value) return true
  return false
})

const errorMessage = computed(() => {
  if (!flow.value.fieldError) return ''
  return localizeError(
    flow.value.fieldError,
    {
      ...flow.value.apiError?.payload,
      previous_value:
        flow.value.apiError?.payload?.previous_value ||
        `${props.meter.previous_value} ${displayUnit.value}`,
    },
    session.lang,
  )
})

const meterStatus = computed(() => {
  if (!props.meter.reading) return 'MISSING'
  if (props.meter.reading.is_anomaly_confirmed) return 'ANOMALY_CONFIRMED'
  if (props.meter.reading.source === 'ADMIN_OVERRIDE') return 'ADMIN_OVERRIDE'
  if (props.meter.reading.source === 'AUTO_ESTIMATED') return 'AUTO_ESTIMATED'
  return 'SUBMITTED'
})

function handleInputEdit(event) {
  input.value = event.target.value
  // Editing the input while a popup is open cancels it; the next submit starts again at level 0
  if (flow.value.anomaly || flow.value.fieldError) {
    flow.value = resetAnomalyOnEdit(flow.value)
  }
  emit('draft-change', {
    meterId: props.meter.meter_id,
    value: input.value,
    delta: liveDelta.value,
  })
}

async function submit(level = 0) {
  if (submitting.value || isInputDisabled.value) return

  const prep = prepareReadingSubmission(flow.value, input.value, level)
  flow.value = prep.nextState
  if (!prep.ok) return

  if (needsReasonField.value && !reason.value.trim()) {
    flow.value = {
      ...flow.value,
      fieldError: 'REASON_REQUIRED',
    }
    return
  }

  submitting.value = true
  try {
    const savedReading = await submitReading({
      meter_id: props.meter.meter_id,
      period_code: props.periodCode,
      value: prep.value,
      confirmation_level: prep.confirmationLevel,
      reason: reason.value.trim() || null,
    })
    flow.value = createAnomalyFlowState()
    input.value = savedReading.value
    emit('saved', savedReading)
  } catch (error) {
    flow.value = transitionOnReadingError(flow.value, error)
  } finally {
    submitting.value = false
  }
}

function handleCancelAnomaly() {
  flow.value = resetAnomalyOnEdit(flow.value)
}

defineExpose({
  submit,
})
</script>

<template>
  <div class="bg-surface-container-low p-space-md rounded-lg flex flex-col justify-between gap-space-sm border border-surface-container">
    <!-- Top Info Row -->
    <div>
      <div class="flex items-start justify-between text-on-surface-variant mb-2 gap-2">
        <div class="min-w-0">
          <div class="flex items-center gap-1.5 font-label-md text-label-md text-on-surface font-semibold">
            <span class="material-symbols-outlined text-[18px] shrink-0" :class="resourceIconClass">
              {{ resourceIcon }}
            </span>
            <span class="truncate">{{ resourceLabel }}</span>
          </div>
          <div class="flex items-center gap-1.5 text-xs text-on-surface-variant mt-0.5 pl-6">
            <span v-if="meter.location_label" class="truncate">{{ meter.location_label }}</span>
            <span v-if="meter.serial_number" class="font-mono text-[11px] whitespace-nowrap shrink-0">
              • #{{ meter.serial_number }}
            </span>
          </div>
        </div>
        <div class="shrink-0">
          <StatusChip :status="meterStatus" />
        </div>
      </div>

      <div class="flex items-baseline justify-between py-1 text-on-surface-variant font-label-md text-label-md">
        <span>{{ session.t('readings.meterCard.previousReading') }}</span>
        <span class="font-mono tabular-nums text-on-surface font-medium">
          {{ meter.previous_value }} {{ displayUnit }}
        </span>
      </div>

      <div
        v-if="meter.previous_delta || meter.average_delta"
        class="flex flex-wrap items-center justify-between gap-2 pb-1 text-[11px] text-on-surface-variant border-b border-surface-container/60"
      >
        <span v-if="meter.previous_delta" class="font-mono tabular-nums">
          {{ session.t('readings.meterCard.lastMonth') }}
          <strong class="text-on-surface">+{{ meter.previous_delta }} {{ displayUnit }}</strong>
        </span>
        <span v-if="meter.average_delta" class="font-mono tabular-nums">
          {{ session.t('readings.meterCard.threeMoAvg') }}
          <strong class="text-on-surface">{{ meter.average_delta }} {{ displayUnit }}</strong>
        </span>
      </div>
    </div>

    <!-- Input Field -->
    <div class="flex flex-col gap-1">
      <label
        :for="`meter-input-${meter.meter_id}`"
        class="font-label-md text-label-md text-on-surface font-semibold flex justify-between"
      >
        <span>{{ session.t('readings.meterCard.newReading') }}</span>
        <span class="text-on-surface-variant text-[11px] font-normal">{{ displayUnit }}</span>
      </label>
      <div class="flex items-center gap-2">
        <div class="relative flex-1">
          <input
            :id="`meter-input-${meter.meter_id}`"
            :value="input"
            type="text"
            inputmode="decimal"
            :disabled="isInputDisabled || submitting"
            :placeholder="meter.previous_value"
            class="w-full h-11 px-3 pr-12 bg-surface-container-lowest border rounded-lg font-numeric-display tabular-nums text-lg text-on-surface font-semibold focus:outline-none focus:ring-2 focus:ring-primary shadow-2xs disabled:opacity-60 disabled:bg-surface-container"
            :class="flow.fieldError ? 'border-error focus:ring-error' : 'border-outline-variant'"
            @input="handleInputEdit"
            @keydown.enter.prevent="submit(0)"
          />
          <span class="absolute right-3 top-2.5 text-xs text-on-surface-variant font-medium pointer-events-none">
            {{ displayUnit }}
          </span>
        </div>

        <button
          v-if="!isInputDisabled"
          type="button"
          :disabled="submitting"
          class="h-11 px-3.5 rounded-lg bg-primary text-on-primary hover:bg-primary-container font-label-md text-xs font-semibold transition-colors shrink-0 inline-flex items-center gap-1 disabled:opacity-50"
          @click="submit(0)"
        >
          <span v-if="submitting" class="material-symbols-outlined text-[16px] animate-spin">sync</span>
          <span v-else class="material-symbols-outlined text-[16px]">check</span>
          <span>{{ session.t('common.save') }}</span>
        </button>
      </div>

      <!-- Optional/Required Reason field when Board/Accountant overrides an existing reading -->
      <div v-if="needsReasonField && !isInputDisabled" class="mt-1.5">
        <label
          :for="`meter-reason-${meter.meter_id}`"
          class="block font-label-md text-[11px] text-on-surface-variant mb-0.5"
        >
          {{ session.t('readings.meterCard.reasonLabel') }}
        </label>
        <input
          :id="`meter-reason-${meter.meter_id}`"
          v-model="reason"
          type="text"
          maxlength="500"
          :placeholder="session.t('readings.meterCard.reasonPlaceholder')"
          class="w-full h-9 px-2.5 bg-surface-container-lowest border border-outline-variant rounded-md text-xs text-on-surface focus:outline-none focus:ring-1 focus:ring-primary"
        />
      </div>

      <!-- Field / API Error Message -->
      <p
        v-if="errorMessage"
        role="alert"
        class="text-xs text-error font-semibold mt-1 flex items-center gap-1"
      >
        <span class="material-symbols-outlined text-[15px] shrink-0">error</span>
        <span>{{ errorMessage }}</span>
      </p>

      <p
        v-else-if="isLockedByBoardForResident"
        class="text-[11px] text-on-surface-variant mt-1 flex items-center gap-1"
      >
        <span class="material-symbols-outlined text-[14px] text-secondary">shield</span>
        <span>
          {{ session.t('readings.meterCard.verifiedByBoard') }}
        </span>
      </p>
    </div>

    <!-- Consumption Delta & Audit History Trigger -->
    <div class="pt-1 flex items-center justify-between font-label-md text-label-md border-t border-surface-container/60">
      <div class="flex items-center gap-1.5">
        <span class="text-on-surface-variant">
          {{ session.t('readings.meterCard.consumption') }}
        </span>
        <span
          v-if="liveDelta !== null"
          class="font-bold font-mono tabular-nums flex items-center gap-0.5"
          :class="
            liveDelta.startsWith('-')
              ? 'text-error'
              : meter.reading?.is_anomaly_confirmed
                ? 'text-error'
                : 'text-secondary'
          "
        >
          {{ formatSignedDelta(liveDelta) }} {{ displayUnit }}
        </span>
        <span v-else class="text-on-surface-variant">—</span>
      </div>

      <button
        v-if="meter.reading?.reading_id"
        type="button"
        class="text-xs text-on-surface-variant hover:text-on-surface inline-flex items-center gap-1 font-medium transition-colors"
        @click="$emit('open-history', meter.reading.reading_id, `${resourceLabel} (${meter.location_label})`)"
      >
        <span class="material-symbols-outlined text-[15px]">history</span>
        <span>{{ session.t('readings.meterCard.history') }}</span>
      </button>
    </div>

    <!-- Two-Popup Anomaly Confirmation Modal (child of MeterCard only) -->
    <MeterCardAnomalyModal
      :anomaly="flow.anomaly"
      :pending-value="flow.pendingValue || ''"
      :unit="meter.unit"
      :meter-label="`${resourceLabel} • ${meter.location_label || meter.serial_number || ''}`"
      :submitting="submitting"
      @confirm="(step) => submit(step)"
      @cancel="handleCancelAnomaly"
    />
  </div>
</template>
