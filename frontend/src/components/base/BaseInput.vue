<script setup>
const model = defineModel({ type: String, default: '' })

defineProps({
  id: {
    type: String,
    required: true,
  },
  label: {
    type: String,
    default: '',
  },
  placeholder: {
    type: String,
    default: '',
  },
  suffix: {
    type: String,
    default: '',
  },
  error: {
    type: String,
    default: '',
  },
  disabled: {
    type: Boolean,
    default: false,
  },
  type: {
    type: String,
    default: 'text',
  },
})
</script>

<template>
  <div class="flex flex-col gap-1">
    <label
      v-if="label"
      :for="id"
      class="font-label-md text-label-md text-on-surface font-semibold flex items-center justify-between"
    >
      <span>{{ label }}</span>
      <span v-if="suffix" class="text-on-surface-variant text-[11px] font-normal">{{ suffix }}</span>
    </label>
    <div class="relative">
      <input
        :id="id"
        v-model="model"
        :type="type"
        :placeholder="placeholder"
        :disabled="disabled"
        class="w-full h-11 px-3 bg-surface-container-lowest border rounded-lg font-body-sm text-body-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary transition-colors disabled:bg-surface-container-low disabled:text-on-surface-variant"
        :class="error ? 'border-error focus:ring-error' : 'border-outline-variant'"
      />
      <span
        v-if="suffix"
        class="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-on-surface-variant font-medium pointer-events-none"
      >
        {{ suffix }}
      </span>
    </div>
    <p v-if="error" class="text-xs text-error font-medium mt-0.5">{{ error }}</p>
  </div>
</template>
