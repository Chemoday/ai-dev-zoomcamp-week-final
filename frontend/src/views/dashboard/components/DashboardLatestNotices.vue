<script setup>
import { RouterLink, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'
import { formatDate } from '@/utils/dates.js'
import AuthorBadge from '@/components/domain/AuthorBadge.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

defineProps({
  notices: {
    type: Array,
    default: () => [],
  },
})

const router = useRouter()
const session = useSessionStore()
</script>

<template>
  <div class="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-surface-container">
    <div class="flex items-center justify-between mb-space-md pb-space-sm border-b border-surface-container">
      <div class="flex items-center gap-space-xs">
        <span class="material-symbols-outlined text-primary text-[20px]">campaign</span>
        <h2 class="font-headline-md text-headline-md font-bold text-on-surface">
          {{ session.t('dashboard.notices.title') }}
        </h2>
      </div>
      <RouterLink
        to="/documents"
        class="font-label-md text-label-md text-primary font-semibold hover:underline flex items-center gap-1"
      >
        <span>{{ session.t('common.archive') }}</span>
        <span class="material-symbols-outlined text-[16px]">chevron_right</span>
      </RouterLink>
    </div>

    <div v-if="notices.length > 0" class="flex flex-col gap-space-md">
      <article
        v-for="notice in notices"
        :key="notice.id"
        class="p-space-md rounded-lg bg-surface-container-low/60 hover:bg-surface-container-low transition-colors border border-surface-container"
      >
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-space-xs mb-space-xs">
          <div class="flex items-center gap-2 flex-wrap">
            <AuthorBadge :badge="notice.author_badge" :is-mine="notice.is_mine" />
            <span class="text-xs text-on-surface-variant">•</span>
            <span class="font-label-md text-label-md text-on-surface-variant">
              {{ formatDate(notice.created_at, session.lang) }}
            </span>
          </div>
          <span
            v-if="!notice.comments_enabled"
            class="font-label-md text-[11px] text-on-surface-variant flex items-center gap-1"
          >
            <span class="material-symbols-outlined text-[14px]">comments_disabled</span>
            {{ session.t('dashboard.notices.officialNoticeBadge') }}
          </span>
        </div>

        <h3
          class="font-headline-md text-headline-md font-bold text-on-surface mb-space-xs cursor-pointer hover:text-secondary transition-colors"
          @click="router.push(`/forum/${notice.id}`)"
        >
          {{ notice.title }}
        </h3>

        <p class="font-body-md text-body-md text-on-surface-variant mb-space-sm leading-relaxed">
          {{ notice.body }}
        </p>

        <div class="flex flex-wrap items-center justify-between gap-space-sm pt-space-xs">
          <span class="text-xs text-on-surface-variant">
            {{
              notice.target_scope === 'ENTRANCE'
                ? session.t('dashboard.notices.entranceTarget', { entrance: notice.target_entrance })
                : session.t('dashboard.notices.allBuildingTarget')
            }}
          </span>
          <button
            type="button"
            class="font-label-md text-label-md text-primary font-semibold hover:underline flex items-center gap-1"
            @click="router.push(`/forum/${notice.id}`)"
          >
            <span>{{ session.t('dashboard.notices.readNotice') }}</span>
            <span class="material-symbols-outlined text-[14px]">arrow_forward</span>
          </button>
        </div>
      </article>
    </div>

    <BaseEmptyState
      v-else
      icon="campaign"
      :title="session.t('dashboard.notices.emptyTitle')"
      :description="session.t('dashboard.notices.emptyDesc')"
    />
  </div>
</template>
