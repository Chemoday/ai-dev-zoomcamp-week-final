<script setup>
import { computed, ref, watch } from 'vue'
import { submitReading } from '@/api/endpoints/meters.js'
import {
  compareMeterValues,
  formatSignedDelta,
  normalizeMeterInput,
  stepMeterValue,
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

// Whether the current input matches the previous reading (resulting in 0.000 consumption)
const isSameAsPrevious = computed(() => {
  const normalized = normalizeMeterInput(input.value)
  if (normalized === null || !props.meter.previous_value) return false
  return compareMeterValues(normalized, props.meter.previous_value) === 0
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

function updateInputValue(nextVal) {
  input.value = nextVal
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

function handleInputEdit(event) {
  updateInputValue(event.target.value)
}

function applyPreviousReading() {
  if (isInputDisabled.value || submitting.value || !props.meter.previous_value) return
  updateInputValue(props.meter.previous_value)
}

function stepReadingByTenths(stepTenths) {
  if (isInputDisabled.value || submitting.value) return
  const fallbackBase = props.meter.previous_value || props.meter.reading?.value || '0.000'
  const nextVal = stepMeterValue(input.value, fallbackBase, stepTenths)
  updateInputValue(nextVal)
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
      <div class="flex items-center justify-between gap-2 mb-0.5">
        <label
          :for="`meter-input-${meter.meter_id}`"
          class="font-label-md text-label-md text-on-surface font-semibold"
        >
          {{ session.t('readings.meterCard.newReading') }}
        </label>

        <button
          v-if="!isInputDisabled && meter.previous_value"
          type="button"
          :disabled="submitting"
          :aria-pressed="isSameAsPrevious"
          :title="session.t('readings.meterCard.sameAsPreviousTooltip', { value: meter.previous_value, unit: displayUnit })"
          class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] leading-none transition-colors border cursor-pointer select-none disabled:opacity-50"
          :class="
            isSameAsPrevious
              ? 'bg-secondary-container/70 text-on-secondary-container border-secondary/40 font-semibold'
              : 'bg-surface-container-lowest text-on-surface-variant border-outline-variant/60 hover:text-on-surface hover:border-outline hover:bg-surface-container-high/50 font-medium'
          "
          @click="applyPreviousReading"
        >
          <svg
            v-if="isSameAsPrevious"
            class="w-2.5 h-2.5 text-secondary shrink-0"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M2.5 6.5L5 9L9.5 3.5" />
          </svg>
          <svg
            v-else
            class="w-2.5 h-2.5 text-on-surface-variant shrink-0"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <rect x="2" y="2" width="8" height="8" rx="1.5" />
          </svg>
          <span>{{ session.t('readings.meterCard.sameAsPreviousBtn', { unit: displayUnit }) }}</span>
        </button>
        <span v-else class="text-on-surface-variant text-[11px] font-normal">{{ displayUnit }}</span>
      </div>

      <div class="flex items-center gap-2">
        <div class="relative flex-1">
          <input
            :id="`meter-input-${meter.meter_id}`"
            :value="input"
            type="text"
            inputmode="decimal"
            :disabled="isInputDisabled || submitting"
            :placeholder="meter.previous_value"
            class="w-full h-11 px-3 pr-16 bg-surface-container-lowest border rounded-lg font-numeric-display tabular-nums text-lg text-on-surface font-semibold focus:outline-none focus:ring-2 focus:ring-primary shadow-2xs disabled:opacity-60 disabled:bg-surface-container"
            :class="flow.fieldError ? 'border-error focus:ring-error' : 'border-outline-variant'"
            @input="handleInputEdit"
            @keydown.enter.prevent="submit(0)"
            @keydown.up.prevent="stepReadingByTenths(1)"
            @keydown.down.prevent="stepReadingByTenths(-1)"
          />
          <div class="absolute right-2 inset-y-0 flex items-center gap-1">
            <span class="text-xs text-on-surface-variant font-medium pointer-events-none select-none">
              {{ displayUnit }}
            </span>
            <div
              v-if="!isInputDisabled"
              class="flex flex-col items-center justify-center w-4 h-6 rounded border border-outline-variant/50 bg-surface-container-low/80 overflow-hidden divide-y divide-outline-variant/40 shrink-0"
            >
              <button
                type="button"
                :disabled="submitting"
                :title="session.t('readings.meterCard.stepUpTooltip')"
                :aria-label="session.t('readings.meterCard.stepUpTooltip')"
                class="w-full h-3 flex items-center justify-center text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high active:bg-primary/15 transition-colors disabled:opacity-40 cursor-pointer"
                @click="stepReadingByTenths(1)"
              >
                <svg class="w-2.5 h-2.5" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M2.5 6.25L5 3.75L7.5 6.25" />
                </svg>
              </button>
              <button
                type="button"
                :disabled="submitting"
                :title="session.t('readings.meterCard.stepDownTooltip')"
                :aria-label="session.t('readings.meterCard.stepDownTooltip')"
                class="w-full h-3 flex items-center justify-center text-on-surface-variant hover:text-on-surface hover:bg-surface-container-high active:bg-primary/15 transition-colors disabled:opacity-40 cursor-pointer"
                @click="stepReadingByTenths(-1)"
              >
                <svg class="w-2.5 h-2.5" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M2.5 3.75L5 6.25L7.5 3.75" />
                </svg>
              </button>
            </div>
          </div>
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
