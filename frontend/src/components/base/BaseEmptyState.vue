<script setup>
defineProps({
  icon: {
    type: String,
    default: 'inbox',
  },
  title: {
    type: String,
    required: true,
  },
  description: {
    type: String,
    default: '',
  },
})
</script>

<template>
  <div class="p-space-xl rounded-xl bg-surface-container-lowest border border-surface-container text-center flex flex-col items-center justify-center gap-2">
    <div class="w-11 h-11 rounded-full bg-surface-container-low text-on-surface-variant flex items-center justify-center">
      <span class="material-symbols-outlined text-[24px]">{{ icon }}</span>
    </div>
    <h3 class="font-headline-md text-headline-md text-on-surface font-semibold">{{ title }}</h3>
    <p v-if="description" class="font-body-sm text-body-sm text-on-surface-variant max-w-md">
      {{ description }}
    </p>
    <div v-if="$slots.default" class="mt-2">
      <slot />
    </div>
  </div>
</template>
