<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import AuthorBadge from '@/components/domain/AuthorBadge.vue'
import BaseChip from '@/components/base/BaseChip.vue'
import { formatDateTime } from '@/utils/dates.js'

const props = defineProps({
  post: {
    type: Object,
    required: true,
  },
  selected: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['select', 'open-detail', 'delete-post'])
const session = useSessionStore()
const { canDeleteForumTopic } = useRole()

const isPinnedNotice = computed(() => props.post.post_type === 'OFFICIAL_NOTICE')

const categoryLabel = computed(() => {
  if (props.post.post_type === 'OFFICIAL_NOTICE') {
    return session.t('forum.officialNoticeBadge')
  }
  switch (props.post.category) {
    case 'MAINTENANCE':
      return session.t('forum.filterMaintenance')
    case 'WATER_SHUTOFF':
      return session.t('forum.waterShutoffCategory')
    case 'MARKETPLACE':
      return session.t('forum.filterMarketplace')
    default:
      return session.t('forum.filterGeneral')
  }
})

const categoryTone = computed(() => {
  if (props.post.post_type === 'OFFICIAL_NOTICE') return 'primary'
  if (props.post.category === 'MAINTENANCE' || props.post.category === 'WATER_SHUTOFF') return 'warning'
  return 'neutral'
})
</script>

<template>
  <article
    class="rounded-xl p-6 transition-all cursor-pointer relative overflow-hidden group"
    :class="[
      isPinnedNotice
        ? 'bg-primary-container text-on-primary-container shadow-sm'
        : 'bg-surface-container-lowest text-on-surface border border-surface-container-high hover:border-primary/40 shadow-xs',
      selected ? 'ring-2 ring-primary' : '',
    ]"
    @click="emit('select', post)"
  >
    <div v-if="isPinnedNotice" class="absolute top-0 right-0 p-4 opacity-10 pointer-events-none">
      <span class="material-symbols-outlined text-6xl">campaign</span>
    </div>

    <div class="relative z-10">
      <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div class="flex items-center gap-2">
          <span
            v-if="isPinnedNotice"
            class="px-2.5 py-0.5 bg-secondary-fixed text-on-secondary-fixed text-[11px] font-bold uppercase tracking-wider rounded"
          >
            {{ categoryLabel }}
          </span>
          <BaseChip v-else :tone="categoryTone">
            {{ categoryLabel }}
          </BaseChip>
          <span
            class="text-xs font-mono"
            :class="isPinnedNotice ? 'text-on-primary-container/75' : 'text-on-surface-variant'"
          >
            {{ formatDateTime(post.created_at, session.lang) }}
          </span>
        </div>

        <div class="flex items-center gap-2">
          <button
            v-if="canDeleteForumTopic && post.post_type === 'FORUM_TOPIC'"
            type="button"
            class="text-xs font-semibold inline-flex items-center gap-1 px-2 py-1 rounded text-error hover:bg-error-container/40 transition-colors"
            @click.stop="emit('delete-post', post)"
          >
            <span class="material-symbols-outlined text-[15px]">delete</span>
            <span>{{ session.t('common.delete') }}</span>
          </button>

          <button
            type="button"
            class="text-xs font-semibold inline-flex items-center gap-1 px-2 py-1 rounded transition-colors"
            :class="isPinnedNotice ? 'text-secondary-fixed hover:bg-white/10' : 'text-primary hover:bg-primary/5'"
            @click.stop="emit('open-detail', post)"
          >
            <span>{{ session.t('forum.fullThreadBtn') }}</span>
            <span class="material-symbols-outlined text-[15px]">open_in_new</span>
          </button>
        </div>
      </div>

      <h3
        class="text-lg font-bold mb-2 leading-snug"
        :class="isPinnedNotice ? 'text-white' : 'text-on-surface group-hover:text-primary transition-colors'"
      >
        {{ post.title }}
      </h3>

      <p
        class="text-sm leading-relaxed mb-4 line-clamp-3 whitespace-pre-line"
        :class="isPinnedNotice ? 'text-on-primary-container/90' : 'text-on-surface-variant'"
      >
        {{ post.body }}
      </p>

      <div
        class="flex flex-wrap items-center justify-between gap-3 pt-3 border-t"
        :class="isPinnedNotice ? 'border-white/15' : 'border-surface-container-high'"
      >
        <div class="flex items-center gap-2">
          <AuthorBadge
            v-if="!isPinnedNotice && post.author_badge"
            :badge="post.author_badge"
            :is-mine="post.is_mine"
          />
          <div v-else-if="post.author_badge" class="flex items-center gap-2 text-xs text-white/90 font-semibold">
            <span class="material-symbols-outlined text-[16px] text-secondary-fixed">verified</span>
            <span>{{ post.author_badge.display_label }}</span>
            <span class="opacity-60">•</span>
            <span class="text-[11px] uppercase tracking-wider text-secondary-fixed">{{ post.author_badge.role_badge }}</span>
          </div>
        </div>

        <div
          class="flex items-center gap-1.5 text-xs font-semibold"
          :class="isPinnedNotice ? 'text-white/90' : 'text-on-surface-variant'"
        >
          <span class="material-symbols-outlined text-[16px]">chat_bubble</span>
          <span class="font-mono tabular-nums">{{ post.comments_count ?? 0 }}</span>
          <span>{{ session.t('forum.commentsCountLabel') }}</span>
        </div>
      </div>
    </div>
  </article>
</template>
