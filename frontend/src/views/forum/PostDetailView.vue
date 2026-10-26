<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'
import { getPost } from '@/api/endpoints/communication.js'
import { formatDateTime } from '@/utils/dates.js'
import CommentList from './components/CommentList.vue'
import CommentComposer from './components/CommentComposer.vue'
import BaseCard from '@/components/base/BaseCard.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseChip from '@/components/base/BaseChip.vue'
import BaseSkeleton from '@/components/base/BaseSkeleton.vue'
import AuthorBadge from '@/components/domain/AuthorBadge.vue'
import ErrorCard from '@/components/domain/ErrorCard.vue'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const post = ref(null)
const loading = ref(false)
const error = ref(null)

async function loadPost() {
  const postId = route.params.postId
  if (!postId) return
  loading.value = true
  error.value = null
  try {
    post.value = await getPost(postId)
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

watch(
  () => [route.params.postId, session.actingUserId, session.sessionRevision],
  () => {
    loadPost()
  },
)

onMounted(() => {
  loadPost()
})
</script>

<template>
  <div class="w-full max-w-4xl mx-auto px-gutter py-space-lg space-y-6">
    <div>
      <BaseButton
        variant="ghost"
        size="sm"
        @click="router.push({ name: 'forum' })"
      >
        <span class="material-symbols-outlined text-[16px]">arrow_back</span>
        <span>{{ session.t('forum.backToForum') }}</span>
      </BaseButton>
    </div>

    <BaseSkeleton v-if="loading" :lines="4" />

    <ErrorCard
      v-else-if="error"
      :error="error"
      @retry="loadPost"
    />

    <template v-else-if="post">
      <BaseCard>
        <div class="flex flex-wrap items-center justify-between gap-2 mb-4">
          <BaseChip :tone="post.post_type === 'OFFICIAL_NOTICE' ? 'primary' : 'neutral'">
            {{ post.post_type === 'OFFICIAL_NOTICE' ? 'OFFICIAL NOTICE' : post.category }}
          </BaseChip>
          <span class="text-xs font-mono text-on-surface-variant">
            {{ formatDateTime(post.created_at, session.lang) }}
          </span>
        </div>

        <h1 class="text-xl sm:text-2xl font-bold text-on-surface mb-4">
          {{ post.title }}
        </h1>

        <p class="text-sm sm:text-base text-on-surface leading-relaxed whitespace-pre-line mb-6">
          {{ post.body }}
        </p>

        <div class="pt-4 border-t border-surface-container-high flex items-center justify-between">
          <AuthorBadge
            v-if="post.author_badge"
            :badge="post.author_badge"
            :is-mine="post.is_mine"
          />
        </div>
      </BaseCard>

      <BaseCard>
        <h2 class="text-sm font-bold uppercase tracking-wider text-on-surface-variant mb-4">
          {{ session.t('forum.commentsHeading') }} ({{ post.comments?.length ?? 0 }})
        </h2>

        <div class="space-y-6">
          <CommentList :comments="post.comments || []" />
          <CommentComposer
            :post-id="post.id"
            :disabled="post.comments_enabled === false"
            @added="loadPost"
          />
        </div>
      </BaseCard>
    </template>
  </div>
</template>
