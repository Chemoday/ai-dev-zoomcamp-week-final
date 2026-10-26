<script setup>
import AppHeader from './components/AppHeader.vue'
import AppEmergencyBanner from './components/AppEmergencyBanner.vue'
import AppSidebar from './components/AppSidebar.vue'
import AppBottomNav from './components/AppBottomNav.vue'
import AppFooter from './components/AppFooter.vue'
</script>

<template>
  <div class="min-h-screen flex flex-col bg-background text-on-surface">
    <AppHeader />
    <AppEmergencyBanner />
    <AppSidebar />
    <main id="main" class="flex-1 w-full">
      <slot />
    </main>
    <AppBottomNav />
    <AppFooter />
  </div>
</template>
