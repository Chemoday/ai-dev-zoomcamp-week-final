<script setup>
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import { APP_ROUTES } from '@/router/routes.js'

const route = useRoute()
const session = useSessionStore()
const { buildingRole } = useRole()

const visibleNavRoutes = computed(() =>
  APP_ROUTES.filter((r) => {
    if (!r.meta?.navKey) return false
    const allowed = r.meta?.roles
    if (!allowed || allowed.length === 0) return true
    return allowed.includes(buildingRole.value)
  }),
)

function isRouteActive(item) {
  if (item.path === '/') return route.path === '/'
  return route.path.startsWith(item.path)
}
</script>

<template>
  <!-- Unified primary navigation bar for desktop and tablet viewports (md+) -->
  <nav
    aria-label="Main Navigation"
    class="hidden md:block sticky top-16 sm:top-20 z-30 w-full bg-surface-container-lowest/95 backdrop-blur-md border-b border-surface-container"
  >
    <div class="w-full max-w-[1280px] mx-auto px-3 sm:px-gutter py-2 flex items-center gap-2 overflow-x-auto">
      <RouterLink
        v-for="item in visibleNavRoutes"
        :key="item.name"
        :to="item.path"
        class="px-3 py-1.5 rounded-lg font-label-md text-xs transition-colors whitespace-nowrap shrink-0 inline-flex items-center gap-1.5"
        :class="
          isRouteActive(item)
            ? 'bg-primary-container text-on-primary font-semibold'
            : 'text-on-surface-variant hover:bg-surface-container hover:text-on-surface'
        "
      >
        <span class="material-symbols-outlined text-[16px]">{{ item.meta.icon }}</span>
        <span>{{ session.t(`nav.${item.meta.navKey}`) }}</span>
        <span
          v-if="item.meta.navKey === 'accounting'"
          class="inline-flex items-center px-1.5 py-0.5 rounded-full bg-secondary-fixed text-on-secondary-fixed font-label-md text-[10px] uppercase font-bold"
        >
          {{ session.t('nav.newBadge') }}
        </span>
      </RouterLink>
    </div>
  </nav>
</template>
