<script setup>
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'
import BaseCard from '@/components/base/BaseCard.vue'
import BaseChip from '@/components/base/BaseChip.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import AuthorBadge from '@/components/domain/AuthorBadge.vue'
import { formatDateTime } from '@/utils/dates.js'

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
  <BaseCard>
    <div class="flex items-center justify-between gap-2 mb-4">
      <div class="flex items-center gap-2.5">
        <span class="w-8 h-8 rounded-lg bg-primary/10 text-primary inline-flex items-center justify-center">
          <span class="material-symbols-outlined text-[18px]">campaign</span>
        </span>
        <div>
          <h2 class="text-base font-bold text-on-surface">
            {{ session.t('documents.noticesArchiveTitle') }}
          </h2>
          <p class="text-xs text-on-surface-variant">
            {{ session.t('documents.noticesArchiveSub') }}
          </p>
        </div>
      </div>

      <BaseChip tone="primary">{{ notices.length }}</BaseChip>
    </div>

    <BaseEmptyState
      v-if="!notices.length"
      icon="description"
      :title="session.t('documents.noNoticesFound')"
    />

    <div v-else class="divide-y divide-surface-container-high">
      <article
        v-for="notice in notices"
        :key="notice.id"
        class="py-4 first:pt-0 last:pb-0 flex flex-col sm:flex-row sm:items-center justify-between gap-3 group cursor-pointer"
        @click="router.push({ name: 'forum-detail', params: { postId: notice.id } })"
      >
        <div class="space-y-1 min-w-0">
          <div class="flex items-center gap-2">
            <span class="text-xs font-mono text-on-surface-variant">
              {{ formatDateTime(notice.created_at, session.lang) }}
            </span>
            <AuthorBadge
              v-if="notice.author_badge"
              :badge="notice.author_badge"
              :is-mine="notice.is_mine"
            />
          </div>
          <h3 class="text-sm font-bold text-on-surface group-hover:text-primary transition-colors truncate">
            {{ notice.title }}
          </h3>
          <p class="text-xs text-on-surface-variant line-clamp-2">
            {{ notice.body }}
          </p>
        </div>

        <div class="shrink-0 flex items-center gap-2">
          <span class="text-xs font-semibold text-primary inline-flex items-center gap-1">
            <span>{{ session.t('common.read') }}</span>
            <span class="material-symbols-outlined text-[16px]">chevron_right</span>
          </span>
        </div>
      </article>
    </div>
  </BaseCard>
</template>
