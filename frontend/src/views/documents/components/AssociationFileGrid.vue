<script setup>
import { computed, ref } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import BaseCard from '@/components/base/BaseCard.vue'
import BaseChip from '@/components/base/BaseChip.vue'
import BaseModal from '@/components/base/BaseModal.vue'
import BaseButton from '@/components/base/BaseButton.vue'

const session = useSessionStore()
const previewDoc = ref(null)

const documents = computed(() => [
  {
    id: 'bylaws-2024',
    title: session.t('documents.items.bylawsTitle'),
    category: session.t('documents.items.bylawsCategory'),
    date: '14.03.2024',
    size: '420 KB',
    format: 'PDF',
    icon: 'gavel',
    summary: session.t('documents.items.bylawsSummary'),
  },
  {
    id: 'agm-2025-minutes',
    title: session.t('documents.items.agmTitle'),
    category: session.t('documents.items.agmCategory'),
    date: '22.05.2025',
    size: '1.1 MB',
    format: 'PDF',
    icon: 'assignment',
    summary: session.t('documents.items.agmSummary'),
  },
  {
    id: 'annual-report-2025',
    title: session.t('documents.items.annualReportTitle'),
    category: session.t('documents.items.annualReportCategory'),
    date: '18.01.2026',
    size: '890 KB',
    format: 'PDF',
    icon: 'account_balance',
    summary: session.t('documents.items.annualReportSummary'),
  },
  {
    id: 'house-rules-2024',
    title: session.t('documents.items.houseRulesTitle'),
    category: session.t('documents.items.houseRulesCategory'),
    date: '01.09.2024',
    size: '310 KB',
    format: 'PDF',
    icon: 'menu_book',
    summary: session.t('documents.items.houseRulesSummary'),
  },
])

function downloadTextSummary(doc) {
  const blob = new Blob(
    [`${doc.title}\nDate: ${doc.date}\nCategory: ${doc.category}\n\n${doc.summary}\n`],
    {
      type: 'text/plain;charset=utf-8',
    },
  )
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${doc.id}.txt`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}
</script>

<template>
  <BaseCard>
    <div class="flex items-center justify-between gap-2 mb-5">
      <div class="flex items-center gap-2.5">
        <span class="w-8 h-8 rounded-lg bg-surface-container-high text-primary inline-flex items-center justify-center">
          <span class="material-symbols-outlined text-[18px]">folder_open</span>
        </span>
        <div>
          <h2 class="text-base font-bold text-on-surface">
            {{ session.t('documents.filesTitle') }}
          </h2>
          <p class="text-xs text-on-surface-variant">
            {{ session.t('documents.filesSub') }}
          </p>
        </div>
      </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div
        v-for="doc in documents"
        :key="doc.id"
        class="p-4 rounded-xl bg-surface-container-low/60 border border-surface-container-high hover:border-primary/40 transition-colors flex items-start justify-between gap-4 cursor-pointer"
        @click="previewDoc = doc"
      >
        <div class="flex items-start gap-3 min-w-0">
          <div class="w-10 h-10 rounded-lg bg-surface-container-lowest border border-surface-container-high flex items-center justify-center text-primary shrink-0">
            <span class="material-symbols-outlined text-[20px]">{{ doc.icon }}</span>
          </div>
          <div class="min-w-0">
            <div class="flex items-center gap-2 mb-1">
              <BaseChip tone="neutral">{{ doc.category }}</BaseChip>
              <span class="text-[11px] font-mono text-on-surface-variant">{{ doc.date }}</span>
            </div>
            <h3 class="text-sm font-bold text-on-surface leading-snug mb-1">
              {{ doc.title }}
            </h3>
            <p class="text-xs text-on-surface-variant line-clamp-2">
              {{ doc.summary }}
            </p>
          </div>
        </div>

        <div class="shrink-0 flex flex-col items-end gap-1">
          <span class="px-2 py-0.5 rounded bg-surface-container-high text-[10px] font-mono font-bold text-on-surface-variant">
            {{ doc.format }} • {{ doc.size }}
          </span>
          <button
            type="button"
            class="text-xs font-semibold text-primary hover:underline inline-flex items-center gap-0.5 mt-2"
            @click.stop="previewDoc = doc"
          >
            <span>{{ session.t('common.preview') }}</span>
            <span class="material-symbols-outlined text-[15px]">visibility</span>
          </button>
        </div>
      </div>
    </div>

    <BaseModal
      :open="Boolean(previewDoc)"
      :title="previewDoc?.title || ''"
      max-width="max-w-lg"
      @close="previewDoc = null"
    >
      <div v-if="previewDoc" class="space-y-4">
        <div class="flex items-center justify-between text-xs font-mono text-on-surface-variant pb-3 border-b border-surface-container-high">
          <span>{{ previewDoc.category }}</span>
          <span>{{ previewDoc.date }} • {{ previewDoc.size }}</span>
        </div>
        <p class="text-sm text-on-surface leading-relaxed">
          {{ previewDoc.summary }}
        </p>
      </div>
      <template #footer>
        <BaseButton variant="secondary" @click="previewDoc = null">
          {{ session.t('common.close') }}
        </BaseButton>
        <BaseButton
          v-if="previewDoc"
          variant="primary"
          @click="downloadTextSummary(previewDoc)"
        >
          <span class="material-symbols-outlined text-[16px]">download</span>
          <span>{{ session.t('documents.downloadSummaryBtn') }}</span>
        </BaseButton>
      </template>
    </BaseModal>
  </BaseCard>
</template>
