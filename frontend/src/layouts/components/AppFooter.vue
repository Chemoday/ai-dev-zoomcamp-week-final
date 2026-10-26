<script setup>
import { RouterLink } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'

const session = useSessionStore()
</script>

<template>
  <footer class="w-full bg-surface-container-lowest border-t border-outline-variant/30 py-space-xl mt-space-xl pb-24 md:pb-space-xl">
    <div class="w-full max-w-[1280px] mx-auto px-gutter">
      <div class="grid grid-cols-1 md:grid-cols-4 gap-space-lg mb-space-lg">
        <div class="flex flex-col gap-space-xs">
          <span class="font-headline-md text-headline-md font-bold text-on-surface">{{ session.buildingName }}</span>
          <p class="font-body-sm text-body-sm text-on-surface-variant">
            Tallinn 13415, Harjumaa<br />
            {{ session.t('footer.registryCode', { code: session.buildingRegistryCode }) }}<br />
            {{ session.t('footer.unionMember') }}
          </p>
        </div>

        <div class="flex flex-col gap-space-xs">
          <span class="font-label-lg text-label-lg font-semibold text-on-surface uppercase tracking-wider">
            {{ session.t('footer.legalAndBylaws') }}
          </span>
          <RouterLink
            to="/documents"
            class="font-body-sm text-body-sm text-on-surface-variant hover:text-on-surface transition-colors"
          >
            {{ session.t('footer.houseRules') }}
          </RouterLink>
          <RouterLink
            to="/documents"
            class="font-body-sm text-body-sm text-on-surface-variant hover:text-on-surface transition-colors"
          >
            {{ session.t('footer.buildingRegistry') }}
          </RouterLink>
          <RouterLink
            to="/documents"
            class="font-body-sm text-body-sm text-on-surface-variant hover:text-on-surface transition-colors"
          >
            {{ session.t('footer.dataPrivacy') }}
          </RouterLink>
        </div>

        <div class="flex flex-col gap-space-xs">
          <span class="font-label-lg text-label-lg font-semibold text-on-surface uppercase tracking-wider">
            {{ session.t('footer.operationalContacts') }}
          </span>
          <p class="font-body-sm text-body-sm text-on-surface-variant">
            <strong class="text-on-surface">
              {{ session.t('footer.emergencyDispatch') }}
            </strong>
            +372 654 3210<br />
            <strong class="text-on-surface">
              {{ session.t('footer.propertyManagement') }}
            </strong>
            Kristiine Kinnisvarahaldus OÜ<br />
            <strong class="text-on-surface">
              {{ session.t('footer.boardEmail') }}
            </strong>
            juhatus@sopruse12.ee
          </p>
        </div>

        <div class="flex flex-col gap-space-xs">
          <span class="font-label-lg text-label-lg font-semibold text-on-surface uppercase tracking-wider">
            {{ session.t('footer.securityAndSurveillance') }}
          </span>
          <div class="p-space-sm rounded-lg bg-surface-container text-on-surface flex items-center gap-space-sm">
            <span class="material-symbols-outlined text-secondary">shield</span>
            <div class="flex flex-col">
              <span class="font-label-md text-label-md font-bold">
                {{ session.t('footer.securityPartner') }}
              </span>
              <span class="font-body-sm text-body-sm text-on-surface-variant">
                {{ session.t('footer.siteCode') }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div class="pt-space-md border-t border-outline-variant/20 flex flex-col sm:flex-row items-center justify-between gap-space-sm">
        <p class="font-body-sm text-body-sm text-on-surface-variant">
          © 2026 Apartment Association Sõpruse 12. All rights reserved.
        </p>
        <p class="font-label-md text-label-md text-on-surface-variant">
          {{ session.t('footer.platformTagline') }}
        </p>
      </div>
    </div>
  </footer>
</template>
