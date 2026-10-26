<script setup>
import { ref, computed } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import { createPost } from '@/api/endpoints/communication.js'
import { localizeError } from '@/locales/index.js'
import BaseModal from '@/components/base/BaseModal.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseInput from '@/components/base/BaseInput.vue'
import AuthorBadge from '@/components/domain/AuthorBadge.vue'

defineProps({
  open: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['close', 'created'])
const session = useSessionStore()
const { canPublishOfficial, buildingRole } = useRole()

const postType = ref('FORUM_TOPIC')
const category = ref('GENERAL')
const title = ref('')
const body = ref('')
const commentsEnabled = ref(true)
const submitting = ref(false)
const errorMsg = ref('')

const availableTypes = computed(() => {
  const list = [
    {
      value: 'FORUM_TOPIC',
      label: session.t('forum.composer.typeForumTopic'),
      icon: 'forum',
    },
  ]
  if (canPublishOfficial.value) {
    list.push({
      value: 'OFFICIAL_NOTICE',
      label: session.t('forum.composer.typeOfficialNotice'),
      icon: 'campaign',
    })
    list.push({
      value: 'EMERGENCY',
      label: session.t('forum.composer.typeEmergency'),
      icon: 'warning',
    })
  }
  return list
})

const categories = computed(() => [
  { value: 'GENERAL', label: session.t('forum.filterGeneral') },
  { value: 'MAINTENANCE', label: session.t('forum.filterMaintenance') },
  { value: 'WATER_SHUTOFF', label: session.t('forum.waterShutoffCategory') },
  { value: 'MARKETPLACE', label: session.t('forum.filterMarketplace') },
])

const previewBadge = computed(() => {
  if (session.activeMembership?.preview_badge) {
    return session.activeMembership.preview_badge
  }
  return {
    display_label: session.currentDemoUser?.label || 'Board',
    role_badge: buildingRole.value === 'ACCOUNTANT' ? 'ACCOUNTANT' : 'BOARD',
  }
})

async function handleSubmit() {
  errorMsg.value = ''
  if (!title.value.trim() || !body.value.trim()) {
    errorMsg.value = session.t('forum.composer.validationRequired')
    return
  }

  submitting.value = true
  try {
    const created = await createPost({
      post_type: postType.value,
      category: category.value,
      title: title.value.trim(),
      body: body.value.trim(),
      target_scope: 'ALL_BUILDING',
      comments_enabled: commentsEnabled.value,
      author_apartment_id: session.activeMembership?.apartment_id || null,
    })
    title.value = ''
    body.value = ''
    postType.value = 'FORUM_TOPIC'
    category.value = 'GENERAL'
    emit('created', created)
    emit('close')
  } catch (err) {
    errorMsg.value = localizeError(err.errorCode, err.payload, session.lang)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <BaseModal
    :open="open"
    :title="session.t('forum.composer.modalTitle')"
    max-width="max-w-xl"
    @close="emit('close')"
  >
    <form class="space-y-4" @submit.prevent="handleSubmit">
      <!-- Author privacy preview -->
      <div class="p-3 rounded-lg bg-surface-container-low border border-surface-container flex items-center justify-between gap-2">
        <span class="text-xs text-on-surface-variant font-medium">
          {{ session.t('forum.composer.postingAs') }}
        </span>
        <AuthorBadge :badge="previewBadge" />
      </div>

      <!-- Post Type Selection (Board can also publish Official Notice / Emergency) -->
      <div v-if="availableTypes.length > 1">
        <label class="block text-xs font-semibold text-on-surface-variant mb-1.5">
          {{ session.t('forum.composer.postTypeLabel') }}
        </label>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <button
            v-for="tItem in availableTypes"
            :key="tItem.value"
            type="button"
            class="flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-semibold border transition-colors text-left"
            :class="
              postType === tItem.value
                ? 'bg-primary text-on-primary border-primary'
                : 'bg-surface-container-lowest text-on-surface border-outline-variant/40 hover:bg-surface-container-low'
            "
            @click="postType = tItem.value"
          >
            <span class="material-symbols-outlined text-[16px]">{{ tItem.icon }}</span>
            <span class="truncate">{{ tItem.label }}</span>
          </button>
        </div>
      </div>

      <!-- Category selection -->
      <div>
        <label class="block text-xs font-semibold text-on-surface-variant mb-1.5">
          {{ session.t('forum.composer.categoryLabel') }}
        </label>
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <button
            v-for="cat in categories"
            :key="cat.value"
            type="button"
            class="px-3 py-2 rounded-lg text-xs font-semibold border transition-colors text-center"
            :class="
              category === cat.value
                ? 'bg-primary-container text-on-primary border-primary'
                : 'bg-surface-container-lowest text-on-surface border-outline-variant/40 hover:bg-surface-container-low'
            "
            @click="category = cat.value"
          >
            {{ cat.label }}
          </button>
        </div>
      </div>

      <BaseInput
        id="post-composer-title"
        v-model="title"
        :label="session.t('forum.composer.titleLabel')"
        :placeholder="session.t('forum.composer.titlePlaceholder')"
      />

      <div>
        <label for="post-composer-body" class="block text-xs font-semibold text-on-surface-variant mb-1">
          {{ session.t('forum.composer.bodyLabel') }} *
        </label>
        <textarea
          id="post-composer-body"
          v-model="body"
          rows="5"
          class="w-full rounded-lg bg-surface-container-lowest px-3 py-2 text-sm text-on-surface border border-outline-variant/60 focus:outline-none focus:ring-2 focus:ring-primary"
          :placeholder="session.t('forum.composer.bodyPlaceholder')"
          required
        />
      </div>

      <label class="inline-flex items-center gap-2 text-xs text-on-surface cursor-pointer">
        <input v-model="commentsEnabled" type="checkbox" class="rounded border-outline-variant" />
        <span>{{ session.t('forum.composer.allowCommentsLabel') }}</span>
      </label>

      <p v-if="errorMsg" class="text-xs text-error font-medium">
        {{ errorMsg }}
      </p>
    </form>

    <template #footer>
      <BaseButton variant="secondary" @click="emit('close')">
        {{ session.t('common.cancel') }}
      </BaseButton>
      <BaseButton
        variant="primary"
        :loading="submitting"
        @click="handleSubmit"
      >
        {{ session.t('forum.composer.publishBtn') }}
      </BaseButton>
    </template>
  </BaseModal>
</template>
