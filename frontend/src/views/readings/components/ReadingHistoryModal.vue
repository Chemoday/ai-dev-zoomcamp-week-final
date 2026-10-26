<script setup>
import { watch } from 'vue'
import { getReadingHistory } from '@/api/endpoints/meters.js'
import { useApiResource } from '@/composables/useApiResource.js'
import { useSessionStore } from '@/stores/session.js'
import { formatDateTime } from '@/utils/dates.js'
import BaseModal from '@/components/base/BaseModal.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseSkeleton from '@/components/base/BaseSkeleton.vue'
import AuthorBadge from '@/components/domain/AuthorBadge.vue'
import ErrorCard from '@/components/domain/ErrorCard.vue'

const props = defineProps({
  readingId: {
    type: String,
    default: '',
  },
  titleLabel: {
    type: String,
    default: '',
  },
})

defineEmits(['close'])

const session = useSessionStore()
const { data: history, loading, error, execute, reset } = useApiResource(getReadingHistory)

watch(
  () => props.readingId,
  (id) => {
    if (id) {
      execute(id)
    } else {
      reset()
    }
  },
  { immediate: true },
)
</script>

<template>
  <BaseModal
    :open="Boolean(readingId)"
    :title="
      session.t('historyModal.title', {
        label: titleLabel || session.t('historyModal.defaultLabel'),
      })
    "
    :subtitle="
      history
        ? `Period ${history.period_code} • Reading ID: ${history.reading_id.slice(0, 8)}`
        : 'Immutable Ledger Log'
    "
    max-width="max-w-2xl"
    @close="$emit('close')"
  >
    <template #icon>
      <div class="w-9 h-9 rounded-lg bg-surface-container-high text-on-surface flex items-center justify-center shrink-0">
        <span class="material-symbols-outlined text-[20px]">verified_user</span>
      </div>
    </template>

    <div v-if="loading" class="py-4">
      <BaseSkeleton :lines="4" />
    </div>

    <ErrorCard
      v-else-if="error"
      :error="error"
      @retry="execute(readingId)"
    />

    <div v-else-if="history && history.entries.length > 0" class="space-y-space-md">
      <div class="relative pl-6 space-y-space-lg before:absolute before:left-2 before:top-2 before:bottom-2 before:w-0.5 before:bg-surface-container-high">
        <div
          v-for="(entry, idx) in history.entries"
          :key="`${entry.changed_at}-${idx}`"
          class="relative flex flex-col gap-1"
        >
          <span
            class="absolute -left-6 top-1 w-3.5 h-3.5 rounded-full border-2 border-surface-container-lowest"
            :class="
              entry.source === 'ADMIN_OVERRIDE'
                ? 'bg-primary'
                : entry.source === 'AUTO_ESTIMATED'
                  ? 'bg-surface-tint'
                  : 'bg-secondary'
            "
          />

          <div class="flex flex-wrap items-baseline justify-between gap-2">
            <span class="font-label-lg text-label-lg font-bold text-on-surface font-mono tabular-nums">
              <template v-if="entry.old_value">
                {{ entry.old_value }} → {{ entry.new_value }}
              </template>
              <template v-else>
                {{ session.t('historyModal.recordedInitial') }} {{ entry.new_value }}
              </template>
            </span>
            <span class="font-label-md text-label-md text-on-surface-variant">
              {{ formatDateTime(entry.changed_at, session.lang) }}
            </span>
          </div>

          <div class="flex flex-wrap items-center gap-2 text-xs text-on-surface-variant">
            <span class="px-2 py-0.5 rounded bg-surface-container font-semibold text-on-surface">
              {{ entry.source }}
            </span>
            <template v-if="entry.changed_by">
              <span>•</span>
              <AuthorBadge :badge="entry.changed_by" />
            </template>
            <template v-else>
              <span>•</span>
              <span class="italic">{{ session.t('historyModal.systemAutomated') }}</span>
            </template>
          </div>

          <p v-if="entry.reason" class="font-body-sm text-body-sm text-on-surface-variant mt-1 bg-surface-container-low p-2.5 rounded-lg">
            <strong>{{ session.t('historyModal.reasonPrefix') }}</strong> {{ entry.reason }}
          </p>
        </div>
      </div>

      <div class="p-space-md rounded-xl bg-surface-container-low flex flex-col sm:flex-row items-start sm:items-center justify-between gap-space-sm">
        <div>
          <span class="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
            {{ session.t('historyModal.ledgerIntegrity') }}
          </span>
          <span class="font-label-lg text-label-lg text-on-surface block font-bold">
            KÜ Sõpruse 12 Audit Log
          </span>
        </div>
        <div class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-container-lowest shadow-2xs text-secondary font-label-md text-label-md font-semibold">
          <span class="material-symbols-outlined text-[18px]">verified</span>
          <span>{{ session.t('historyModal.digitallyCertified') }}</span>
        </div>
      </div>
    </div>

    <template #footer>
      <BaseButton variant="primary" @click="$emit('close')">
        {{ session.t('common.closeWindow') }}
      </BaseButton>
    </template>
  </BaseModal>
</template>
