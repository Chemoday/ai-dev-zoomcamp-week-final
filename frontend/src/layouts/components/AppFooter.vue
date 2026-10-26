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
        <div class="flex items-center gap-space-sm">
          <p class="font-label-md text-label-md text-on-surface-variant">
            {{ session.t('footer.platformTagline') }}
          </p>
          <span class="text-outline-variant/40" aria-hidden="true">·</span>
          <RouterLink
            to="/theme-lab"
            :title="session.t('footer.themeLab')"
            class="inline-flex items-center gap-1 font-label-md text-label-md text-on-surface-variant/45 hover:text-on-surface transition-opacity opacity-50 hover:opacity-100"
          >
            <svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path
                fill-rule="evenodd"
                d="M10 2a8 8 0 00-8 8c0 4.418 3.582 8 8 8 1.24 0 2.25-1.01 2.25-2.25 0-.56-.21-1.08-.56-1.48-.33-.38-.52-.87-.52-1.39 0-1.17.95-2.13 2.13-2.13H15a3 3 0 003-3c0-3.866-3.582-5.75-8-5.75zm-4.5 7a1.25 1.25 0 112.5 0 1.25 1.25 0 01-2.5 0zm3-3a1.25 1.25 0 112.5 0 1.25 1.25 0 01-2.5 0zm4 0a1.25 1.25 0 112.5 0 1.25 1.25 0 01-2.5 0zm2 3a1.25 1.25 0 112.5 0 1.25 1.25 0 01-2.5 0z"
                clip-rule="evenodd"
              />
            </svg>
            <span>{{ session.t('footer.themeLab') }}</span>
          </RouterLink>
        </div>
      </div>
    </div>
  </footer>
</template>
