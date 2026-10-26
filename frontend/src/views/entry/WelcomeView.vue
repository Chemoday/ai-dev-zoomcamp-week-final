<script setup>
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'
import DemoRoleCard from './components/DemoRoleCard.vue'

const router = useRouter()
const session = useSessionStore()

async function handleSelectUser(userId) {
  await session.switchUser(userId)
  router.push({ name: 'dashboard' })
}
</script>

<template>
  <div class="max-w-5xl mx-auto px-4 sm:px-6 py-12 space-y-8">
    <div class="space-y-2">
      <span class="text-xs font-bold uppercase tracking-widest text-primary">
        KÜ Sõpruse 12 • Tallinn
      </span>
      <h1 class="text-3xl font-bold text-on-surface tracking-tight">
        {{ session.t('welcome.title') }}
      </h1>
      <p class="text-sm text-on-surface-variant max-w-2xl">
        {{ session.t('welcome.subtitle') }}
      </p>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
      <DemoRoleCard
        v-for="u in session.demoUsers"
        :key="u.user_id"
        :user="u"
        :active="u.user_id === session.actingUserId"
        @select="handleSelectUser"
      />
    </div>
  </div>
</template>
