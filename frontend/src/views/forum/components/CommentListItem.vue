<script setup>
import { useSessionStore } from '@/stores/session.js'
import AuthorBadge from '@/components/domain/AuthorBadge.vue'
import { formatDateTime } from '@/utils/dates.js'

defineProps({
  comment: {
    type: Object,
    required: true,
  },
})

const session = useSessionStore()
</script>

<template>
  <div class="p-3.5 rounded-lg bg-surface-container-low/70 border border-surface-container-high space-y-2">
    <div class="flex items-center justify-between gap-2">
      <AuthorBadge
        v-if="comment.author_badge"
        :badge="comment.author_badge"
        :is-mine="comment.is_mine"
      />
      <span class="text-[11px] font-mono text-on-surface-variant">
        {{ formatDateTime(comment.created_at, session.lang) }}
      </span>
    </div>
    <p class="text-sm text-on-surface leading-relaxed whitespace-pre-line">
      {{ comment.body }}
    </p>
  </div>
</template>
