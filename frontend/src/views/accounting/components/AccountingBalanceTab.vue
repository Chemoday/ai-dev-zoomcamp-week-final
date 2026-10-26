<script setup>
import { useSessionStore } from '@/stores/session.js'
import MeterValueText from '@/components/domain/MeterValueText.vue'

defineProps({
  balances: {
    type: Array,
    default: () => [],
  },
  masterMeters: {
    type: Array,
    default: () => [],
  },
  periodCode: {
    type: String,
    required: true,
  },
})

const session = useSessionStore()
</script>

<template>
  <div class="space-y-space-lg">
    <!-- Real API Resource Balances Table -->
    <div class="rounded-xl bg-surface-container-lowest border border-surface-container shadow-2xs overflow-hidden">
      <div class="px-space-lg py-3 bg-surface-container-low/80 border-b border-surface-container flex items-center justify-between">
        <div>
          <h3 class="font-label-lg text-label-lg font-bold text-on-surface">
            {{ session.t('accounting.balanceTableTitle') }}
          </h3>
          <p class="text-xs text-on-surface-variant">
            {{ session.t('accounting.balanceTableSub', { period: periodCode }) }}
          </p>
        </div>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-left font-body-sm text-body-sm">
          <thead class="bg-surface-container/60 text-on-surface-variant font-label-md text-label-md uppercase tracking-wider border-b border-surface-container">
            <tr>
              <th class="py-3 px-space-md">{{ session.t('accounting.colResource') }}</th>
              <th class="py-3 px-space-md text-right">{{ session.t('accounting.colMasterTotal') }}</th>
              <th class="py-3 px-space-md text-right">{{ session.t('accounting.colApartmentsSum') }}</th>
              <th class="py-3 px-space-md text-right">{{ session.t('accounting.colUnallocatedLoss') }}</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-surface-container">
            <tr v-for="b in balances" :key="b.resource_type" class="hover:bg-surface-container-low/50">
              <td class="py-3.5 px-space-md font-semibold text-on-surface">
                {{ b.resource_type }}
              </td>
              <td class="py-3.5 px-space-md text-right">
                <MeterValueText :value="b.master_total" :unit="b.unit" />
              </td>
              <td class="py-3.5 px-space-md text-right">
                <MeterValueText :value="b.apartments_sum" :unit="b.unit" />
              </td>
              <td class="py-3.5 px-space-md text-right">
                <span
                  v-if="b.unallocated_loss !== null"
                  class="font-mono tabular-nums font-bold"
                  :class="b.unallocated_loss.startsWith('-') ? 'text-error' : 'text-on-surface'"
                >
                  {{ b.unallocated_loss }} {{ b.unit === 'm3' ? 'm³' : b.unit }}
                </span>
                <span v-else class="text-xs text-on-surface-variant italic">
                  {{ session.t('accounting.awaitingMasterReading') }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Clearly Labelled Phase 2 Billing Demo Content (per FRONTEND_CONVENTIONS.md Section 4) -->
    <div class="rounded-xl bg-surface-container-lowest border border-surface-container p-space-lg shadow-2xs">
      <div class="flex items-center justify-between mb-space-md">
        <div class="flex items-center gap-2">
          <span class="material-symbols-outlined text-secondary">receipt_long</span>
          <h3 class="font-headline-md text-headline-md font-bold text-on-surface">
            {{ session.t('accounting.tariffsPreviewTitle') }}
          </h3>
          <span class="px-2 py-0.5 rounded bg-surface-container-high text-on-surface-variant font-label-md text-[10px] uppercase font-bold tracking-wider">
            Phase 2 Demo
          </span>
        </div>
      </div>

      <p class="font-body-sm text-body-sm text-on-surface-variant mb-space-md">
        {{ session.t('accounting.tariffsPreviewDesc') }}
      </p>

      <div class="grid grid-cols-1 md:grid-cols-3 gap-space-md">
        <div class="p-space-md rounded-lg bg-surface-container-low border border-surface-container">
          <span class="font-label-md text-xs text-on-surface-variant uppercase">Tallinna Vesi Tariff (Demo)</span>
          <p class="font-numeric-display tabular-nums text-lg font-bold text-on-surface mt-1">€2.14 / m³</p>
          <span class="text-xs text-on-surface-variant">Cold water + sewerage</span>
        </div>
        <div class="p-space-md rounded-lg bg-surface-container-low border border-surface-container">
          <span class="font-label-md text-xs text-on-surface-variant uppercase">Utilitas District Heat (Demo)</span>
          <p class="font-numeric-display tabular-nums text-lg font-bold text-on-surface mt-1">€84.50 / MWh</p>
          <span class="text-xs text-on-surface-variant">Water heating + radiators</span>
        </div>
        <div class="p-space-md rounded-lg bg-surface-container-low border border-surface-container">
          <span class="font-label-md text-xs text-on-surface-variant uppercase">Loss Allocation Rule (Demo)</span>
          <p class="font-numeric-display tabular-nums text-lg font-bold text-on-surface mt-1">Proportional m²</p>
          <span class="text-xs text-on-surface-variant">2,480 m² total heated area</span>
        </div>
      </div>
    </div>
  </div>
</template>
