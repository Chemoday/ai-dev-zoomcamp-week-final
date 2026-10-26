<script setup>
defineProps({
  variant: {
    type: String,
    default: 'primary', // 'primary' | 'secondary' | 'danger' | 'ghost'
  },
  size: {
    type: String,
    default: 'md', // 'sm' | 'md' | 'lg'
  },
  type: {
    type: String,
    default: 'button',
  },
  disabled: {
    type: Boolean,
    default: false,
  },
  loading: {
    type: Boolean,
    default: false,
  },
})

defineEmits(['click'])
</script>

<template>
  <button
    :type="type"
    :disabled="disabled || loading"
    class="inline-flex items-center justify-center gap-1.5 rounded-lg font-label-md text-label-md transition-colors whitespace-nowrap shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
    :class="[
      size === 'sm' ? 'px-3 py-1.5 text-xs' : size === 'lg' ? 'px-5 py-3 text-sm font-semibold' : 'px-4 py-2 text-xs font-semibold',
      variant === 'primary'
        ? 'bg-primary text-on-primary hover:bg-primary-container shadow-xs'
        : variant === 'danger'
          ? 'bg-error text-on-error hover:opacity-90 shadow-xs'
          : variant === 'ghost'
            ? 'bg-transparent text-on-surface-variant hover:bg-surface-container hover:text-on-surface'
            : 'bg-surface-container-lowest border border-outline-variant/60 text-on-surface hover:bg-surface-container-low',
    ]"
    @click="$emit('click', $event)"
  >
    <span v-if="loading" class="material-symbols-outlined text-[16px] animate-spin">sync</span>
    <slot />
  </button>
</template>
