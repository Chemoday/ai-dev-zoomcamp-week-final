<script setup>
import { ref } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { addComment } from '@/api/endpoints/communication.js'
import { localizeError } from '@/locales/index.js'
import BaseButton from '@/components/base/BaseButton.vue'

const props = defineProps({
  postId: {
    type: String,
    required: true,
  },
  disabled: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['added'])
const session = useSessionStore()

const body = ref('')
const submitting = ref(false)
const errorMsg = ref('')

async function handleSubmit() {
  errorMsg.value = ''
  if (!body.value.trim() || props.disabled) return

  submitting.value = true
  try {
    const comment = await addComment(props.postId, {
      body: body.value.trim(),
      author_apartment_id: session.activeMembership?.apartment_id || null,
    })
    body.value = ''
    emit('added', comment)
  } catch (err) {
    errorMsg.value = localizeError(err.errorCode, err.payload, session.lang)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <form class="space-y-2.5" @submit.prevent="handleSubmit">
    <textarea
      v-model="body"
      rows="3"
      :disabled="disabled"
      class="w-full rounded-lg bg-surface-container-lowest px-3 py-2 text-sm text-on-surface border border-outline-variant/50 focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
      :placeholder="
        disabled
          ? session.t('forum.commentsDisabledPlaceholder')
          : session.t('forum.writeCommentPlaceholder')
      "
      required
    />

    <p v-if="errorMsg" class="text-xs text-error font-medium">
      {{ errorMsg }}
    </p>

    <div class="flex justify-end">
      <BaseButton
        type="submit"
        variant="primary"
        size="sm"
        :loading="submitting"
        :disabled="disabled || !body.trim()"
      >
        {{ session.t('forum.postCommentBtn') }}
      </BaseButton>
    </div>
  </form>
</template>
