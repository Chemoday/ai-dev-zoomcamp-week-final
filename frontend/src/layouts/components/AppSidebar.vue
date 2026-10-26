<script setup>
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import { useTranslations } from '@/locales/index.js'
import { APP_ROUTES } from '@/router/routes.js'

const route = useRoute()
const session = useSessionStore()
const { buildingRole } = useRole()
const t = computed(() => useTranslations(session.lang))

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
  <!-- Compact secondary navigation bar for tablet sizes where xl top nav is hidden -->
  <nav
    aria-label="Tablet Navigation"
    class="hidden md:flex xl:hidden w-full bg-surface-container-lowest border-b border-surface-container px-gutter py-2 items-center gap-2 overflow-x-auto"
  >
    <RouterLink
      v-for="item in visibleNavRoutes"
      :key="item.name"
      :to="item.path"
      class="px-3 py-1.5 rounded-lg font-label-md text-xs transition-colors whitespace-nowrap shrink-0 flex items-center gap-1.5"
      :class="
        isRouteActive(item)
          ? 'bg-primary-container text-on-primary font-semibold'
          : 'text-on-surface-variant hover:bg-surface-container hover:text-on-surface'
      "
    >
      <span class="material-symbols-outlined text-[16px]">{{ item.meta.icon }}</span>
      <span>{{ t.nav[item.meta.navKey] }}</span>
    </RouterLink>
  </nav>
</template>
