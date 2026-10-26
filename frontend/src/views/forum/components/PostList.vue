<script setup>
import { useSessionStore } from '@/stores/session.js'
import PostListItem from './PostListItem.vue'
import BaseSkeleton from '@/components/base/BaseSkeleton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

defineProps({
  posts: {
    type: Array,
    default: () => [],
  },
  selectedPostId: {
    type: String,
    default: null,
  },
  loading: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['select', 'open-detail', 'delete-post'])
const session = useSessionStore()
</script>

<template>
  <div class="space-y-4">
    <div v-if="loading" class="space-y-4">
      <BaseSkeleton :lines="4" />
    </div>

    <BaseEmptyState
      v-else-if="!posts.length"
      icon="forum"
      :title="session.t('forum.noDiscussionsFound')"
    />

    <div v-else class="space-y-4">
      <PostListItem
        v-for="post in posts"
        :key="post.id"
        :post="post"
        :selected="post.id === selectedPostId"
        @select="emit('select', $event)"
        @open-detail="emit('open-detail', $event)"
        @delete-post="emit('delete-post', $event)"
      />
    </div>
  </div>
</template>
