<script setup>
import { ref } from 'vue'
import { useSessionStore } from '@/stores/session.js'

const session = useSessionStore()
const dropdownOpen = ref(false)

async function handleSelectUser(userId) {
  dropdownOpen.value = false
  if (userId && userId !== session.actingUserId) {
    await session.switchUser(userId)
  }
}
</script>

<template>
  <div v-if="session.demoUsers.length > 0" class="relative shrink-0">
    <button
      type="button"
      aria-label="Switch seeded demo user"
      :aria-expanded="dropdownOpen"
      class="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface font-label-md text-xs font-semibold transition-colors whitespace-nowrap shrink-0"
      @click="dropdownOpen = !dropdownOpen"
    >
      <span class="w-2 h-2 rounded-full bg-secondary shrink-0" />
      <span class="max-w-[130px] sm:max-w-[165px] truncate">
        {{ session.currentDemoUser?.label || session.t('header.selectRole') }}
      </span>
      <span class="material-symbols-outlined text-[16px] text-on-surface-variant shrink-0">expand_more</span>
    </button>

    <div
      v-if="dropdownOpen"
      class="absolute right-0 mt-1.5 w-64 rounded-xl bg-surface-container-lowest shadow-lg border border-surface-container py-1.5 z-50"
    >
      <div class="px-3 py-1.5 border-b border-surface-container text-[11px] font-label-md uppercase tracking-wider text-on-surface-variant">
        {{ session.t('header.actAsDemoUser') }}
      </div>
      <button
        v-for="u in session.demoUsers"
        :key="u.user_id"
        type="button"
        class="w-full px-3 py-2 text-left text-xs flex items-center justify-between hover:bg-surface-container-low transition-colors"
        :class="
          u.user_id === session.actingUserId
            ? 'bg-secondary-fixed/20 font-semibold text-on-surface'
            : 'text-on-surface-variant'
        "
        @click="handleSelectUser(u.user_id)"
      >
        <div class="flex flex-col min-w-0">
          <span class="text-on-surface truncate">{{ u.label }}</span>
          <span class="text-[11px] text-on-surface-variant">
            {{ u.building_role }}{{ u.apartment_number ? ` • Apt ${u.apartment_number}` : '' }}
          </span>
        </div>
        <span
          v-if="u.user_id === session.actingUserId"
          class="material-symbols-outlined text-[16px] text-secondary"
        >
          check
        </span>
      </button>
    </div>
  </div>
</template>
