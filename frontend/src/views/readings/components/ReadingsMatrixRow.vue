<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import { formatSignedDelta, sumMeterValues } from '@/utils/meterValue.js'
import StatusChip from '@/components/domain/StatusChip.vue'

const props = defineProps({
  apartment: {
    type: Object, // MatrixApartment
    required: true,
  },
  isMyApartment: {
    type: Boolean,
    default: false,
  },
})

defineEmits(['open-history', 'edit-apartment'])

const session = useSessionStore()
const { canSubmitAnyMeter } = useRole()

// Sum cold water meters in this apartment
const coldMeters = computed(() =>
  props.apartment.meters.filter((m) => m.resource_type === 'COLD_WATER'),
)

const hotMeters = computed(() =>
  props.apartment.meters.filter((m) => m.resource_type === 'HOT_WATER'),
)

const coldValue = computed(() => {
  const vals = coldMeters.value.filter((m) => m.reading?.value).map((m) => m.reading.value)
  return vals.length ? sumMeterValues(vals) : null
})

const coldDelta = computed(() => {
  const deltas = coldMeters.value
    .filter((m) => m.reading?.consumption_delta)
    .map((m) => m.reading.consumption_delta)
  return deltas.length ? sumMeterValues(deltas) : null
})

const hotValue = computed(() => {
  const vals = hotMeters.value.filter((m) => m.reading?.value).map((m) => m.reading.value)
  return vals.length ? sumMeterValues(vals) : null
})

const hotDelta = computed(() => {
  const deltas = hotMeters.value
    .filter((m) => m.reading?.consumption_delta)
    .map((m) => m.reading.consumption_delta)
  return deltas.length ? sumMeterValues(deltas) : null
})

const totalWaterDelta = computed(() => {
  const allWaterDeltas = props.apartment.meters
    .filter((m) => m.unit === 'm3' && m.reading?.consumption_delta)
    .map((m) => m.reading.consumption_delta)
  return allWaterDeltas.length ? sumMeterValues(allWaterDeltas) : '0.000'
})

const firstReadingId = computed(() => {
  const withReading = props.apartment.meters.find((m) => m.reading?.reading_id)
  return withReading?.reading?.reading_id || ''
})

const isAnomaly = computed(() => props.apartment.status === 'ANOMALY_CONFIRMED')
</script>

<template>
  <tr
    class="transition-colors"
    :class="[
      isAnomaly
        ? 'bg-error-container/20 hover:bg-error-container/30'
        : isMyApartment
          ? 'bg-secondary-fixed/10 hover:bg-surface-container-low/60'
          : 'hover:bg-surface-container-low/60',
    ]"
  >
    <!-- Unit -->
    <td class="py-space-md px-space-md">
      <div class="flex items-center gap-2.5">
        <span
          class="w-7 h-7 rounded flex items-center justify-center font-bold font-label-md text-label-md shrink-0"
          :class="
            isAnomaly
              ? 'bg-error text-on-error'
              : isMyApartment
                ? 'bg-primary text-on-primary'
                : 'bg-surface-container text-on-surface'
          "
        >
          {{ apartment.apartment_number }}
        </span>
        <div>
          <div class="flex items-center gap-1.5">
            <span class="font-label-lg text-label-lg text-on-surface block leading-tight font-semibold">
              Apt {{ apartment.apartment_number }}
            </span>
            <span
              v-if="isMyApartment"
              class="px-1.5 py-0.5 rounded bg-surface-container font-label-md text-[10px] uppercase font-bold text-on-surface"
            >
              {{ session.t('matrix.myUnitBadge') }}
            </span>
          </div>
          <span class="text-on-surface-variant font-label-md text-[11px]">
            {{ session.t('common.floorEntrance', { floor: apartment.floor, entrance: apartment.entrance }) }}
          </span>
        </div>
      </div>
    </td>

    <!-- Privacy-compliant Status / Source Descriptor (No resident names in matrix per FRONTEND_GUIDE.md Section 7) -->
    <td class="py-space-md px-space-md">
      <span class="font-label-md text-label-md text-on-surface-variant italic block">
        {{ session.t('matrix.privacyMasked') }}
      </span>
      <span
        class="font-label-md text-[11px]"
        :class="isAnomaly ? 'text-error font-bold' : 'text-on-surface-variant'"
      >
        {{
          isAnomaly
            ? session.t('matrix.confirmedHighDelta')
            : apartment.status === 'AUTO_ESTIMATED'
              ? session.t('matrix.movingAverage')
              : apartment.status === 'MISSING'
                ? session.t('matrix.awaitingSubmission')
                : session.t('matrix.verifiedUnitLedger')
        }}
      </span>
    </td>

    <!-- Cold Water (m³) -->
    <td class="py-space-md px-space-md whitespace-nowrap font-mono tabular-nums">
      <template v-if="coldValue">
        <span
          class="font-semibold text-[15px]"
          :class="isAnomaly ? 'text-error font-bold' : 'text-on-surface'"
        >
          {{ coldValue }}
        </span>
        <span
          v-if="coldDelta"
          class="font-label-md text-[12px] ml-1"
          :class="
            isAnomaly
              ? 'text-error font-extrabold bg-error-container px-1.5 py-0.5 rounded'
              : 'text-secondary font-semibold'
          "
        >
          ({{ formatSignedDelta(coldDelta) }})
        </span>
      </template>
      <span v-else class="text-on-surface-variant text-xs">—</span>
    </td>

    <!-- Hot Water (m³) -->
    <td class="py-space-md px-space-md whitespace-nowrap font-mono tabular-nums">
      <template v-if="hotValue">
        <span class="font-semibold text-on-surface text-[15px]">{{ hotValue }}</span>
        <span v-if="hotDelta" class="text-secondary font-label-md text-[12px] font-semibold ml-1">
          ({{ formatSignedDelta(hotDelta) }})
        </span>
      </template>
      <span v-else class="text-on-surface-variant text-xs">—</span>
    </td>

    <!-- Total Consumption Delta (m³) -->
    <td
      class="py-space-md px-space-md text-right font-numeric-display tabular-nums text-headline-md whitespace-nowrap"
      :class="isAnomaly ? 'text-error font-extrabold' : 'text-on-surface'"
    >
      {{ totalWaterDelta }}
    </td>

    <!-- Status -->
    <td class="py-space-md px-space-md whitespace-nowrap">
      <button
        v-if="isAnomaly && firstReadingId"
        type="button"
        class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full bg-error text-on-error font-label-md text-label-md font-bold shadow-2xs hover:opacity-90 transition-opacity"
        @click="$emit('open-history', firstReadingId, `Apt ${apartment.apartment_number} Consumption Review`)"
      >
        <span class="material-symbols-outlined text-[14px]">priority_high</span>
        <span>{{ session.t('status.ANOMALY_CONFIRMED') }}</span>
      </button>
      <StatusChip v-else :status="apartment.status" />
    </td>

    <!-- Actions -->
    <td class="py-space-md px-space-md text-right whitespace-nowrap">
      <div class="inline-flex items-center gap-1">
        <button
          v-if="firstReadingId"
          type="button"
          :aria-label="`View history for Apt ${apartment.apartment_number}`"
          class="p-1.5 rounded-lg transition-colors"
          :class="
            isAnomaly
              ? 'bg-surface-container-lowest text-error hover:bg-error-container shadow-2xs'
              : 'text-on-surface-variant hover:bg-surface-container hover:text-on-surface'
          "
          @click="$emit('open-history', firstReadingId, `Apt ${apartment.apartment_number}`)"
        >
          <span class="material-symbols-outlined text-[18px]">
            {{ isAnomaly ? 'receipt_long' : 'history' }}
          </span>
        </button>

        <button
          v-if="canSubmitAnyMeter"
          type="button"
          :aria-label="`Edit readings for Apt ${apartment.apartment_number}`"
          class="p-1.5 rounded-lg text-on-surface-variant hover:bg-surface-container hover:text-on-surface transition-colors"
          @click="$emit('edit-apartment', apartment)"
        >
          <span class="material-symbols-outlined text-[18px]">edit</span>
        </button>
      </div>
    </td>
  </tr>
</template>
