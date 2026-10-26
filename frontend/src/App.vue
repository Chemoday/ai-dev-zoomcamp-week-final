<script setup>
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'
import AppLayout from '@/layouts/AppLayout.vue'
import PublicLayout from '@/layouts/PublicLayout.vue'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const layoutComponent = computed(() =>
  route.meta?.layout === 'PublicLayout' ? PublicLayout : AppLayout,
)

watch(
  () => session.me?.building_role,
  (newRole) => {
    const role = newRole || 'USER'
    const allowedRoles = route.meta?.roles
    if (allowedRoles && Array.isArray(allowedRoles) && !allowedRoles.includes(role)) {
      router.push({ name: 'dashboard' })
    }
  },
)

onMounted(async () => {
  await session.initializeSession()
})
</script>

<template>
  <component :is="layoutComponent">
    <RouterView />
  </component>
</template>
