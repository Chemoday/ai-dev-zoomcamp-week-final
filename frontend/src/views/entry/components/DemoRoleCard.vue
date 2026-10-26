<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import BaseCard from '@/components/base/BaseCard.vue'
import BaseButton from '@/components/base/BaseButton.vue'

const props = defineProps({
  user: {
    type: Object,
    required: true,
  },
  active: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['select'])
const session = useSessionStore()

const roleIcon = computed(() => {
  const role = props.user.building_role || 'USER'
  if (role === 'CHAIRMAN' || role === 'BOARD') return 'shield_person'
  if (role === 'ACCOUNTANT') return 'calculate'
  return 'home_pin'
})
</script>

<template>
  <BaseCard
    class="flex flex-col justify-between gap-4 transition-all"
    :class="active ? 'ring-2 ring-primary' : ''"
  >
    <div class="space-y-3">
      <div class="flex items-center justify-between">
        <span class="w-10 h-10 rounded-xl bg-primary/10 text-primary inline-flex items-center justify-center">
          <span class="material-symbols-outlined text-[22px]">{{ roleIcon }}</span>
        </span>
        <span class="px-2.5 py-0.5 rounded bg-surface-container-high text-xs font-mono font-semibold text-on-surface-variant">
          {{ user.building_role }}
        </span>
      </div>

      <div>
        <h3 class="text-base font-bold text-on-surface">
          {{ user.label }}
        </h3>
        <p class="text-xs text-on-surface-variant font-mono mt-0.5">
          {{ user.apartment_number ? `Apartment ${user.apartment_number} (${user.relation_type})` : session.t('welcome.wholeBuildingAccess') }}
        </p>
      </div>
    </div>

    <BaseButton
      :variant="active ? 'primary' : 'secondary'"
      size="sm"
      @click="emit('select', user.user_id)"
    >
      <span class="material-symbols-outlined text-[16px]">login</span>
      <span>{{ session.t('welcome.continueAs', { label: user.label }) }}</span>
    </BaseButton>
  </BaseCard>
</template>
