<script setup>
import { computed } from 'vue'
import BaseModal from '@/components/base/BaseModal.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import { useSessionStore } from '@/stores/session.js'

const props = defineProps({
  anomaly: {
    type: Object, // null | { step: 1 | 2, delta: string, average?: string | null, limit?: string | null }
    default: null,
  },
  pendingValue: {
    type: String,
    default: '',
  },
  unit: {
    type: String,
    required: true,
  },
  meterLabel: {
    type: String,
    default: '',
  },
  submitting: {
    type: Boolean,
    default: false,
  },
})

defineEmits(['confirm', 'cancel'])

const session = useSessionStore()
const displayUnit = computed(() => (props.unit === 'm3' ? 'm³' : props.unit))

const modalTitle = computed(() => {
  if (!props.anomaly) return ''
  if (props.anomaly.step === 1) {
    return session.t('anomalyModal.step1Title')
  }
  return session.t('anomalyModal.step2Title')
})
</script>

<template>
  <BaseModal
    :open="Boolean(anomaly)"
    :title="modalTitle"
    :subtitle="meterLabel"
    max-width="max-w-md"
    @close="$emit('cancel')"
  >
    <template #icon>
      <div
        class="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
        :class="
          anomaly?.step === 2
            ? 'bg-error text-on-error'
            : 'bg-error-container text-on-error-container'
        "
      >
        <span class="material-symbols-outlined text-[20px]">warning</span>
      </div>
    </template>

    <div v-if="anomaly" class="space-y-space-md">
      <!-- Step 1 Content -->
      <template v-if="anomaly.step === 1">
        <p class="font-body-sm text-body-sm text-on-surface leading-relaxed">
          {{ session.t('anomalyModal.step1Intro') }}
        </p>

        <div class="p-space-md rounded-xl bg-surface-container-low border border-surface-container space-y-2 text-xs">
          <div class="flex items-center justify-between">
            <span class="text-on-surface-variant">
              {{ session.t('anomalyModal.enteredReading') }}
            </span>
            <strong class="font-mono text-on-surface text-sm">{{ pendingValue }} {{ displayUnit }}</strong>
          </div>
          <div class="flex items-center justify-between">
            <span class="text-on-surface-variant">
              {{ session.t('anomalyModal.consumptionDelta') }}
            </span>
            <strong class="font-mono text-error text-sm font-bold">
              +{{ anomaly.delta }} {{ displayUnit }}
            </strong>
          </div>
          <div v-if="anomaly.average" class="flex items-center justify-between">
            <span class="text-on-surface-variant">
              {{ session.t('anomalyModal.threeMonthAvg') }}
            </span>
            <span class="font-mono text-on-surface">{{ anomaly.average }} {{ displayUnit }}</span>
          </div>
          <div v-if="anomaly.limit" class="flex items-center justify-between">
            <span class="text-on-surface-variant">
              {{ session.t('anomalyModal.reasonableThreshold') }}
            </span>
            <span class="font-mono text-on-surface">{{ anomaly.limit }} {{ displayUnit }}</span>
          </div>
        </div>

        <p class="text-xs text-on-surface-variant">
          {{ session.t('anomalyModal.areYouSure') }}
        </p>
      </template>

      <!-- Step 2 Content -->
      <template v-else>
        <div class="p-space-md rounded-xl bg-error-container/40 border border-error/30 space-y-2">
          <p class="font-label-md text-xs font-bold text-error uppercase tracking-wider">
            {{ session.t('anomalyModal.step2WarningHeading') }}
          </p>
          <p class="font-body-sm text-body-sm text-on-surface leading-relaxed">
            {{
              session.t('anomalyModal.step2WarningBody', {
                delta: anomaly.delta,
                unit: displayUnit,
                value: pendingValue,
              })
            }}
          </p>
        </div>
      </template>
    </div>

    <template #footer>
      <BaseButton variant="secondary" :disabled="submitting" @click="$emit('cancel')">
        {{ session.t('anomalyModal.cancelAndEdit') }}
      </BaseButton>
      <BaseButton
        variant="danger"
        :loading="submitting"
        @click="$emit('confirm', anomaly.step)"
      >
        {{
          anomaly?.step === 1
            ? session.t('anomalyModal.confirmStep1')
            : session.t('anomalyModal.confirmStep2')
        }}
      </BaseButton>
    </template>
  </BaseModal>
</template>
