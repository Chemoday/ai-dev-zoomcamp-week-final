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
  <nav
    aria-label="Mobile Navigation"
    class="md:hidden fixed bottom-0 left-0 right-0 z-40 bg-surface-container-lowest/95 backdrop-blur-md border-t border-surface-container px-2 py-1.5 flex items-center justify-around"
  >
    <RouterLink
      v-for="item in visibleNavRoutes"
      :key="item.name"
      :to="item.path"
      class="flex flex-col items-center justify-center py-1 px-2 rounded-lg text-[11px] font-label-md transition-colors min-w-[56px]"
      :class="
        isRouteActive(item)
          ? 'text-primary font-bold bg-surface-container-high/60'
          : 'text-on-surface-variant hover:text-on-surface'
      "
    >
      <span class="material-symbols-outlined text-[20px]">{{ item.meta.icon }}</span>
      <span class="truncate max-w-[68px]">{{ t.nav[item.meta.navKey] }}</span>
    </RouterLink>
  </nav>
</template>
