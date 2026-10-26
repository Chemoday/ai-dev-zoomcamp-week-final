<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { localizeError } from '@/locales/index.js'

const props = defineProps({
  error: {
    type: Object,
    required: true,
  },
  title: {
    type: String,
    default: '',
  },
})

defineEmits(['retry'])

const session = useSessionStore()

const errorCode = computed(() => props.error?.errorCode || 'INTERNAL_SERVER_ERROR')
const traceId = computed(() => props.error?.traceId || props.error?.payload?.trace_id || '')

const localizedMessage = computed(() =>
  localizeError(errorCode.value, { ...props.error?.payload, trace_id: traceId.value }, session.lang),
)
</script>

<template>
  <div
    class="bg-surface-container-lowest border border-error/30 rounded-xl p-space-md shadow-xs"
    role="alert"
  >
    <div class="flex items-start gap-3">
      <div class="w-8 h-8 rounded-full bg-error-container text-on-error-container flex items-center justify-center shrink-0">
        <span class="material-symbols-outlined text-[18px]">error</span>
      </div>
      <div class="flex-1 min-w-0">
        <h4 class="font-label-lg text-label-lg font-bold text-error">
          {{ title || session.t('common.couldNotCompleteRequest') }}
        </h4>
        <p class="font-body-sm text-body-sm text-on-surface-variant mt-1 leading-relaxed">
          {{ localizedMessage }}
        </p>
        <p v-if="traceId" class="mt-1 font-mono text-[11px] text-on-surface-variant">
          Trace ID: <code class="bg-error-container/50 text-on-error-container px-1.5 py-0.5 rounded">{{ traceId }}</code>
        </p>
        <div class="mt-3 flex items-center gap-2">
          <button
            type="button"
            class="bg-primary text-on-primary hover:bg-primary-container text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors"
            @click="$emit('retry')"
          >
            {{ session.t('common.tryAgain') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
