<script setup>
import AppFooter from './components/AppFooter.vue'
</script>

<template>
  <div class="min-h-screen flex flex-col bg-background text-on-surface">
    <main id="main" class="flex-1 flex flex-col justify-center">
      <slot />
    </main>
    <AppFooter />
  </div>
</template>
