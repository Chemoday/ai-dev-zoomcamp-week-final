<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useEmergencies } from '@/composables/useEmergencies.js'
import { useRole } from '@/composables/useRole.js'
import { useSessionStore } from '@/stores/session.js'

const router = useRouter()
const session = useSessionStore()
const { canPublishOfficial } = useRole()
const { unacknowledgedEmergencies, acknowledge, resolve } = useEmergencies()

const busyId = ref('')

const primaryEmergency = computed(() => unacknowledgedEmergencies.value[0] || null)

const targetLabel = computed(() => {
  const item = primaryEmergency.value
  if (!item) return ''
  if (item.target_scope === 'ENTRANCE' && item.target_entrance) {
    return session.t('emergency.urgentEntrance', { entrance: item.target_entrance })
  }
  return session.t('emergency.urgent')
})

async function handleAcknowledge(id) {
  if (busyId.value) return
  busyId.value = id
  try {
    await acknowledge(id)
  } finally {
    busyId.value = ''
  }
}

async function handleResolve(id) {
  if (busyId.value) return
  busyId.value = id
  try {
    await resolve(id)
  } finally {
    busyId.value = ''
  }
}

function viewDetails(id) {
  router.push(`/forum/${id}`)
}
</script>

<template>
  <div
    v-if="primaryEmergency"
    role="alert"
    aria-live="polite"
    class="w-full bg-error-container text-on-error-container border-b border-error/20 px-gutter py-2.5"
  >
    <div class="w-full max-w-[1280px] mx-auto flex flex-wrap items-center justify-between gap-space-sm">
      <div class="flex items-center gap-space-sm min-w-0">
        <span class="material-symbols-outlined text-[20px] text-error shrink-0">warning</span>
        <p class="font-body-sm text-body-sm text-on-error-container">
          <span class="font-label-lg text-label-lg font-bold uppercase tracking-wide text-error mr-1.5">
            {{ targetLabel }}
          </span>
          <span class="font-semibold">{{ primaryEmergency.title }}</span>
          <span class="hidden md:inline"> — {{ primaryEmergency.body }}</span>
        </p>
      </div>

      <div class="flex flex-wrap items-center gap-2 shrink-0 ml-auto">
        <button
          type="button"
          class="font-label-md text-label-md underline underline-offset-4 text-on-error-container hover:text-on-surface transition-colors"
          @click="viewDetails(primaryEmergency.id)"
        >
          {{ session.t('emergency.viewDetails') }}
        </button>

        <button
          type="button"
          :disabled="busyId === primaryEmergency.id"
          class="px-3 py-1 bg-surface-container-lowest text-on-surface font-label-md text-label-md rounded-full shadow-xs hover:bg-surface-container-high transition-colors inline-flex items-center gap-1 font-semibold disabled:opacity-50"
          @click="handleAcknowledge(primaryEmergency.id)"
        >
          <span class="material-symbols-outlined text-[16px] text-secondary">check</span>
          <span>{{ session.t('emergency.acknowledge') }}</span>
        </button>

        <button
          v-if="canPublishOfficial"
          type="button"
          :disabled="busyId === primaryEmergency.id"
          class="px-3 py-1 bg-error text-on-error font-label-md text-label-md rounded-full shadow-xs hover:opacity-90 transition-opacity inline-flex items-center gap-1 font-semibold disabled:opacity-50"
          @click="handleResolve(primaryEmergency.id)"
        >
          <span class="material-symbols-outlined text-[15px]">task_alt</span>
          <span>{{ session.t('emergency.resolve') }}</span>
        </button>
      </div>
    </div>
  </div>
</template>
