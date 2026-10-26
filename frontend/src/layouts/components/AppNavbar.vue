<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import AppNavbarRoleSwitcher from './AppNavbarRoleSwitcher.vue'
import AppNavbarPrivacySelector from './AppNavbarPrivacySelector.vue'

const session = useSessionStore()
const { buildingRole } = useRole()

const avatarInitials = computed(() => {
  const label =
    session.activeMembership?.preview_badge?.display_label ||
    session.currentDemoUser?.label ||
    'KÜ'
  const parts = label
    .replace(/[^A-Za-z0-9\s]/g, '')
    .trim()
    .split(/\s+/)
  if (parts.length >= 2) return `${parts[0][0]}${parts[1][0]}`.toUpperCase()
  return label.slice(0, 2).toUpperCase()
})
</script>

<template>
  <div class="h-16 sm:h-20 w-full max-w-[1280px] mx-auto px-3 sm:px-gutter flex items-center justify-between gap-2 sm:gap-4">
    <!-- Brand -->
    <div class="flex items-center gap-4 min-w-0 shrink-0">
      <RouterLink to="/" class="flex flex-col shrink-0 group">
        <div class="flex items-center gap-1.5">
          <span class="font-headline-md text-base sm:text-headline-md font-bold tracking-tight text-on-surface whitespace-nowrap">
            {{ session.buildingName }}
          </span>
          <span class="hidden sm:inline-flex items-center px-2 py-0.5 rounded-full bg-secondary-container text-on-secondary-container font-label-md text-[11px] font-semibold">
            {{ session.building?.district || 'Tallinn' }}
          </span>
        </div>
        <span class="hidden sm:block font-label-md text-[11px] text-on-surface-variant whitespace-nowrap">
          {{ session.buildingAddress }} • Reg {{ session.buildingRegistryCode }}
        </span>
      </RouterLink>
    </div>

    <!-- Right Controls: Role Switcher, Privacy Mode, Language Switch, Profile -->
    <div class="flex items-center gap-1.5 sm:gap-2 shrink-0">
      <AppNavbarRoleSwitcher />
      <AppNavbarPrivacySelector />

      <!-- EN / ET Language Switcher -->
      <button
        type="button"
        :aria-label="session.lang === 'en' ? session.t('header.switchToEt') : session.t('header.switchToEn')"
        class="px-2.5 py-1.5 rounded-lg bg-surface-container-low hover:bg-surface-container text-on-surface font-label-md text-xs font-semibold transition-colors uppercase shrink-0"
        @click="session.setLang(session.lang === 'en' ? 'et' : 'en')"
      >
        {{ session.lang === 'en' ? 'ET' : 'EN' }}
      </button>

      <!-- User badge -->
      <div class="hidden sm:flex items-center gap-2 pl-0.5 shrink-0">
        <div
          class="w-8 h-8 rounded-full bg-primary text-on-primary flex items-center justify-center font-label-md text-xs font-bold shrink-0"
          :title="session.activeMembership?.preview_badge?.display_label || session.currentDemoUser?.label || 'User'"
        >
          {{ avatarInitials }}
        </div>
        <div class="hidden 2xl:flex flex-col text-left">
          <span class="font-label-md text-xs font-semibold leading-tight text-on-surface whitespace-nowrap">
            {{ session.activeMembership?.preview_badge?.display_label || session.currentDemoUser?.label || 'User' }}
          </span>
          <span class="text-[11px] leading-tight text-on-surface-variant whitespace-nowrap">
            {{ session.activeMembership ? `Apt ${session.activeMembership.apartment_number}` : buildingRole }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
