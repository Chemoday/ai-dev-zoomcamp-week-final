<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'

const route = useRoute()
const session = useSessionStore()
const menuOpen = ref(false)
const saving = ref(false)
const rootRef = ref(null)

const membership = computed(() => session.activeMembership)

const modes = ['APARTMENT_ONLY', 'INITIALS', 'FULL_NAME']

const badgePreviewText = computed(() => {
  if (!membership.value) {
    const role = session.me?.building_role || 'USER'
    return session.t(`buildingRole.${role}`)
  }
  const b = membership.value.preview_badge
  return b.display_label
})

async function selectPrivacyMode(mode) {
  if (!membership.value || saving.value) return
  saving.value = true
  try {
    await session.changePrivacyMode(membership.value.membership_id, mode)
    menuOpen.value = false
  } finally {
    saving.value = false
  }
}

function handleDocumentClick(e) {
  if (menuOpen.value && rootRef.value && !rootRef.value.contains(e.target)) {
    menuOpen.value = false
  }
}

watch(
  () => route.fullPath,
  () => {
    menuOpen.value = false
  },
)

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<template>
  <div v-if="membership" ref="rootRef" class="relative shrink-0">
    <button
      type="button"
      :aria-expanded="menuOpen"
      aria-label="Privacy display mode selector"
      class="hidden lg:inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface font-label-md text-xs font-medium transition-colors whitespace-nowrap shrink-0"
      @click="menuOpen = !menuOpen"
    >
      <span class="material-symbols-outlined text-[16px] text-secondary shrink-0">verified_user</span>
      <span class="max-w-[88px] 2xl:max-w-[150px] truncate">{{ badgePreviewText }}</span>
      <span class="material-symbols-outlined text-[16px] text-on-surface-variant shrink-0">expand_more</span>
    </button>

    <div
      v-if="menuOpen"
      class="absolute right-0 mt-1.5 w-72 rounded-xl bg-surface-container-lowest shadow-lg border border-surface-container p-3 z-50"
    >
      <div class="flex items-center justify-between pb-2 mb-2 border-b border-surface-container">
        <span class="font-label-md text-label-md text-on-surface font-bold">
          {{ session.t('header.neighborDisplayBadge') }}
        </span>
        <span class="text-[11px] text-secondary font-semibold">
          Apt {{ membership.apartment_number }}
        </span>
      </div>

      <div v-if="session.memberships.length > 1" class="mb-3">
        <label class="block text-[11px] font-semibold text-on-surface-variant mb-1">
          {{ session.t('header.activeApartment') }}
        </label>
        <div class="flex gap-1">
          <button
            v-for="m in session.memberships"
            :key="m.membership_id"
            type="button"
            class="px-2.5 py-1 rounded text-xs font-semibold transition-colors"
            :class="
              m.apartment_id === membership.apartment_id
                ? 'bg-primary text-on-primary'
                : 'bg-surface-container-low text-on-surface-variant hover:text-on-surface'
            "
            @click="session.selectApartment(m.apartment_id)"
          >
            Apt {{ m.apartment_number }}
          </button>
        </div>
      </div>

      <div class="flex flex-col gap-1">
        <button
          v-for="mode in modes"
          :key="mode"
          type="button"
          :disabled="saving"
          class="w-full px-3 py-2 rounded-lg text-left text-xs flex items-center justify-between transition-colors"
          :class="
            membership.privacy_display_mode === mode
              ? 'bg-secondary-fixed/30 text-on-secondary-fixed-variant font-semibold'
              : 'hover:bg-surface-container-low text-on-surface'
          "
          @click="selectPrivacyMode(mode)"
        >
          <span>{{ session.t(`privacy.${mode}`) }}</span>
          <span
            v-if="membership.privacy_display_mode === mode"
            class="material-symbols-outlined text-[16px] text-secondary"
          >
            check_circle
          </span>
        </button>
      </div>

      <div class="mt-2.5 pt-2 border-t border-surface-container text-[11px] text-on-surface-variant flex items-center justify-between">
        <span>{{ session.t('header.forumBadgePreview') }}</span>
        <strong class="text-on-surface">
          {{ membership.preview_badge.display_label }} • {{ membership.preview_badge.role_badge }}
        </strong>
      </div>
    </div>
  </div>
</template>
