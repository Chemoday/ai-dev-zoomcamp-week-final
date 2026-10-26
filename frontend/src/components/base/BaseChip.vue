<script setup>
defineProps({
  tone: {
    type: String,
    default: 'neutral', // 'neutral' | 'success' | 'danger' | 'warning' | 'dark'
  },
})
</script>

<template>
  <span
    class="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full font-label-md text-label-md whitespace-nowrap shrink-0"
    :class="[
      tone === 'success'
        ? 'bg-secondary-fixed/30 text-on-secondary-fixed-variant'
        : tone === 'danger'
          ? 'bg-error-container text-on-error-container'
          : tone === 'warning'
            ? 'bg-amber-100 text-amber-900'
            : tone === 'dark'
              ? 'bg-primary-container text-on-primary'
              : 'bg-surface-container-high text-on-surface-variant',
    ]"
  >
    <slot />
  </span>
</template>
