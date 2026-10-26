<script setup>
import { onMounted, onUnmounted } from 'vue'

const props = defineProps({
  open: {
    type: Boolean,
    required: true,
  },
  title: {
    type: String,
    required: true,
  },
  subtitle: {
    type: String,
    default: '',
  },
  maxWidth: {
    type: String,
    default: 'max-w-lg',
  },
})

const emit = defineEmits(['close'])

function handleKeydown(event) {
  if (event.key === 'Escape' && props.open) {
    emit('close')
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 bg-on-surface/40 backdrop-blur-xs flex items-center justify-center p-space-md"
      role="dialog"
      aria-modal="true"
      :aria-label="title"
      @click.self="$emit('close')"
    >
      <div
        class="w-full rounded-xl bg-surface-container-lowest shadow-2xl border border-surface-container overflow-hidden flex flex-col max-h-[90vh]"
        :class="maxWidth"
      >
        <div class="px-space-lg py-space-md bg-surface-container-low flex items-center justify-between gap-space-sm border-b border-surface-container">
          <div class="flex items-center gap-space-sm min-w-0">
            <slot name="icon" />
            <div class="min-w-0">
              <h3 class="font-headline-md text-headline-md text-on-surface font-bold truncate">
                {{ title }}
              </h3>
              <p v-if="subtitle" class="font-body-sm text-body-sm text-on-surface-variant truncate">
                {{ subtitle }}
              </p>
            </div>
          </div>
          <button
            type="button"
            aria-label="Close modal"
            class="p-1.5 rounded-lg hover:bg-surface-container text-on-surface-variant hover:text-on-surface transition-colors"
            @click="$emit('close')"
          >
            <span class="material-symbols-outlined text-[20px]">close</span>
          </button>
        </div>

        <div class="p-space-lg overflow-y-auto flex-1">
          <slot />
        </div>

        <div
          v-if="$slots.footer"
          class="px-space-lg py-space-md bg-surface-container-low border-t border-surface-container flex items-center justify-end gap-space-sm"
        >
          <slot name="footer" />
        </div>
      </div>
    </div>
  </Teleport>
</template>
