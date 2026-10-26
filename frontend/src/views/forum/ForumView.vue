<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'
import { listPosts, getPost, deletePost } from '@/api/endpoints/communication.js'
import { formatDateTime } from '@/utils/dates.js'
import PostList from './components/PostList.vue'
import PostComposer from './components/PostComposer.vue'
import CommentList from './components/CommentList.vue'
import CommentComposer from './components/CommentComposer.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseCard from '@/components/base/BaseCard.vue'
import BaseChip from '@/components/base/BaseChip.vue'
import AuthorBadge from '@/components/domain/AuthorBadge.vue'
import ErrorCard from '@/components/domain/ErrorCard.vue'

const router = useRouter()
const session = useSessionStore()

const posts = ref([])
const loading = ref(false)
const error = ref(null)
const activeFilter = ref('ALL')
const composerOpen = ref(false)

const selectedPostDetail = ref(null)
const savingPrivacy = ref(false)

const filters = computed(() => [
  { key: 'ALL', label: session.t('forum.filterAll') },
  { key: 'OFFICIAL_NOTICE', label: session.t('forum.filterOfficial') },
  { key: 'GENERAL', label: session.t('forum.filterGeneral') },
  { key: 'MAINTENANCE', label: session.t('forum.filterMaintenance') },
  { key: 'MARKETPLACE', label: session.t('forum.filterMarketplace') },
])

const privacyModes = computed(() => [
  {
    value: 'FULL_NAME',
    label: session.t('forum.privacyFullName'),
    desc: 'Kati Kask • OWNER',
  },
  {
    value: 'INITIALS',
    label: session.t('forum.privacyInitials'),
    desc: 'K. K. (Apt 14) • OWNER',
  },
  {
    value: 'APARTMENT_ONLY',
    label: session.t('forum.privacyApartmentOnly'),
    desc: 'Apt 14 • OWNER',
  },
])

async function loadPosts() {
  if (!session.actingUserId) return
  loading.value = true
  error.value = null
  try {
    const params = { limit: 30 }
    if (activeFilter.value === 'OFFICIAL_NOTICE') {
      params.post_type = 'OFFICIAL_NOTICE'
    } else if (activeFilter.value !== 'ALL') {
      params.category = activeFilter.value
    }

    const data = await listPosts(params)
    posts.value = Array.isArray(data) ? data : []
    if (posts.value.length > 0) {
      const stillExists =
        selectedPostDetail.value && posts.value.some((p) => p.id === selectedPostDetail.value.id)
      const targetId = stillExists ? selectedPostDetail.value.id : posts.value[0].id
      await selectPost({ id: targetId })
    } else {
      selectedPostDetail.value = null
    }
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

async function selectPost(post) {
  if (!post?.id) return
  try {
    selectedPostDetail.value = await getPost(post.id)
  } catch {
    selectedPostDetail.value = post
  }
}

function openPostDetail(post) {
  router.push({ name: 'forum-detail', params: { postId: post.id } })
}

async function handleDeletePost(post) {
  if (!post?.id) return
  try {
    await deletePost(post.id)
    if (selectedPostDetail.value?.id === post.id) {
      selectedPostDetail.value = null
    }
    await loadPosts()
  } catch (err) {
    error.value = err
  }
}

async function handleCommentAdded() {
  if (selectedPostDetail.value?.id) {
    await selectPost({ id: selectedPostDetail.value.id })
    const idx = posts.value.findIndex((p) => p.id === selectedPostDetail.value.id)
    if (idx !== -1 && selectedPostDetail.value.comments) {
      posts.value[idx].comments_count = selectedPostDetail.value.comments.length
    }
  }
}

async function handlePrivacyChange(mode) {
  const membership = session.activeMembership
  if (!membership || savingPrivacy.value || mode === membership.privacy_display_mode) return
  savingPrivacy.value = true
  try {
    await session.changePrivacyMode(membership.membership_id, mode)
    await loadPosts()
  } finally {
    savingPrivacy.value = false
  }
}

watch(activeFilter, () => {
  loadPosts()
})

watch(
  () => [session.actingUserId, session.sessionRevision],
  () => {
    loadPosts()
  },
)

onMounted(() => {
  loadPosts()
})
</script>

<template>
  <div class="w-full max-w-[1280px] mx-auto px-gutter py-space-lg space-y-6">
    <!-- Page Header & Actions -->
    <div class="flex flex-col sm:flex-row sm:items-end justify-between gap-4">
      <div>
        <span class="text-[11px] font-bold uppercase tracking-widest text-primary block mb-1">
          KÜ Sõpruse 12 • Tallinn
        </span>
        <h1 class="text-2xl sm:text-3xl font-bold text-on-surface tracking-tight">
          {{ session.t('forum.title') }}
        </h1>
        <p class="text-sm text-on-surface-variant mt-1">
          {{ session.t('forum.subtitle') }}
        </p>
      </div>

      <BaseButton variant="primary" @click="composerOpen = true">
        <span class="material-symbols-outlined text-[18px]">add_comment</span>
        <span>{{ session.t('forum.newDiscussionBtn') }}</span>
      </BaseButton>
    </div>

    <!-- Category filter bar -->
    <div class="flex flex-wrap items-center gap-2 border-b border-surface-container-high pb-4">
      <button
        v-for="f in filters"
        :key="f.key"
        type="button"
        class="px-4 py-1.5 rounded-full text-xs font-semibold transition-colors"
        :class="
          activeFilter === f.key
            ? 'bg-primary text-on-primary shadow-xs'
            : 'bg-surface-container-low text-on-surface-variant hover:text-on-surface'
        "
        @click="activeFilter = f.key"
      >
        {{ f.label }}
      </button>
    </div>

    <ErrorCard v-if="error" :error="error" @retry="loadPosts" />

    <!-- 12-Col Split Layout: 8 Cols Feed / 4 Cols Active Thread & Privacy Widget -->
    <div v-else class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
      <div class="lg:col-span-7 xl:col-span-8">
        <PostList
          :posts="posts"
          :selected-post-id="selectedPostDetail?.id"
          :loading="loading"
          @select="selectPost"
          @open-detail="openPostDetail"
          @delete-post="handleDeletePost"
        />
      </div>

      <!-- Right Sidebar: Selected Thread Inspector + Privacy Card -->
      <aside class="lg:col-span-5 xl:col-span-4 space-y-6 lg:sticky lg:top-24">
        <!-- Active Post Discussion Panel -->
        <BaseCard v-if="selectedPostDetail">
          <div class="flex items-center justify-between gap-2 mb-3">
            <BaseChip :tone="selectedPostDetail.post_type === 'OFFICIAL_NOTICE' ? 'primary' : 'neutral'">
              {{ selectedPostDetail.post_type === 'OFFICIAL_NOTICE' ? 'OFFICIAL NOTICE' : selectedPostDetail.category }}
            </BaseChip>
            <button
              type="button"
              class="text-xs font-semibold text-primary hover:underline inline-flex items-center gap-1"
              @click="openPostDetail(selectedPostDetail)"
            >
              <span>{{ session.t('forum.expandBtn') }}</span>
              <span class="material-symbols-outlined text-[14px]">fullscreen</span>
            </button>
          </div>

          <h2 class="text-base font-bold text-on-surface mb-2">
            {{ selectedPostDetail.title }}
          </h2>

          <p class="text-xs text-on-surface-variant whitespace-pre-line leading-relaxed mb-4">
            {{ selectedPostDetail.body }}
          </p>

          <div class="flex items-center justify-between pb-3 mb-4 border-b border-surface-container-high">
            <AuthorBadge
              v-if="selectedPostDetail.author_badge"
              :badge="selectedPostDetail.author_badge"
              :is-mine="selectedPostDetail.is_mine"
            />
            <span class="text-[11px] font-mono text-on-surface-variant">
              {{ formatDateTime(selectedPostDetail.created_at, session.lang) }}
            </span>
          </div>

          <div class="space-y-4">
            <h3 class="text-xs font-bold uppercase tracking-wider text-on-surface-variant">
              {{ session.t('forum.commentsHeading') }} ({{ selectedPostDetail.comments?.length ?? 0 }})
            </h3>

            <CommentList :comments="selectedPostDetail.comments || []" />

            <CommentComposer
              :post-id="selectedPostDetail.id"
              :disabled="selectedPostDetail.comments_enabled === false"
              @added="handleCommentAdded"
            />
          </div>
        </BaseCard>

        <!-- Privacy Mode Widget (for Residents with a Unit Membership) -->
        <BaseCard v-if="session.activeMembership">
          <div class="flex items-center gap-2 mb-2">
            <span class="material-symbols-outlined text-primary text-[20px]">visibility</span>
            <h3 class="text-sm font-bold text-on-surface">
              {{ session.t('forum.privacyWidgetTitle') }}
            </h3>
          </div>
          <p class="text-xs text-on-surface-variant mb-3">
            {{ session.t('forum.privacyWidgetDesc') }}
          </p>

          <div class="space-y-2">
            <button
              v-for="mode in privacyModes"
              :key="mode.value"
              type="button"
              class="w-full text-left p-2.5 rounded-lg border transition-colors flex items-center justify-between gap-2"
              :class="
                session.activeMembership.privacy_display_mode === mode.value
                  ? 'bg-primary/8 border-primary text-on-surface'
                  : 'bg-surface-container-low/50 border-surface-container-high text-on-surface-variant hover:bg-surface-container-low'
              "
              :disabled="savingPrivacy"
              @click="handlePrivacyChange(mode.value)"
            >
              <div>
                <div class="text-xs font-semibold text-on-surface">{{ mode.label }}</div>
                <div class="text-[11px] text-on-surface-variant font-mono">{{ mode.desc }}</div>
              </div>
              <span
                class="material-symbols-outlined text-[18px]"
                :class="
                  session.activeMembership.privacy_display_mode === mode.value
                    ? 'text-primary'
                    : 'text-outline-variant'
                "
              >
                {{
                  session.activeMembership.privacy_display_mode === mode.value
                    ? 'radio_button_checked'
                    : 'radio_button_unchecked'
                }}
              </span>
            </button>
          </div>
        </BaseCard>
      </aside>
    </div>

    <PostComposer
      :open="composerOpen"
      @close="composerOpen = false"
      @created="loadPosts"
    />
  </div>
</template>
