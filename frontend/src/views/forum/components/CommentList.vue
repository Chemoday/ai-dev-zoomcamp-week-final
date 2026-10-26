<script setup>
import { useSessionStore } from '@/stores/session.js'
import CommentListItem from './CommentListItem.vue'

defineProps({
  comments: {
    type: Array,
    default: () => [],
  },
})

const session = useSessionStore()
</script>

<template>
  <div class="space-y-3">
    <div
      v-if="!comments.length"
      class="py-6 text-center text-xs text-on-surface-variant bg-surface-container-low/40 rounded-lg border border-dashed border-outline-variant/30"
    >
      {{ session.t('forum.noCommentsYet') }}
    </div>

    <CommentListItem
      v-for="comment in comments"
      :key="comment.id"
      :comment="comment"
    />
  </div>
</template>
