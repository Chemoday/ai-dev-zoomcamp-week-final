<script setup>
import { ref, onMounted, watch } from 'vue'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import { listPosts } from '@/api/endpoints/communication.js'
import { listPeriods } from '@/api/endpoints/meters.js'
import NoticeList from './components/NoticeList.vue'
import ReadingReportList from './components/ReadingReportList.vue'
import AssociationFileGrid from './components/AssociationFileGrid.vue'
import BaseSkeleton from '@/components/base/BaseSkeleton.vue'
import ErrorCard from '@/components/domain/ErrorCard.vue'

const session = useSessionStore()
const { canViewMatrix } = useRole()

const notices = ref([])
const periods = ref([
  { code: '2026-10', status: 'OPEN_FOR_RESIDENTS' },
  { code: '2026-09', status: 'LOCKED' },
  { code: '2026-08', status: 'LOCKED' },
])
const loading = ref(false)
const error = ref(null)

async function loadDocumentsData() {
  if (!session.actingUserId) return
  loading.value = true
  error.value = null
  try {
    const [noticeData, periodsData] = await Promise.all([
      listPosts({ post_type: 'OFFICIAL_NOTICE', limit: 20 }),
      listPeriods().catch(() => []),
    ])
    notices.value = Array.isArray(noticeData) ? noticeData : []
    if (Array.isArray(periodsData) && periodsData.length > 0) {
      periods.value = periodsData.map((p) => ({
        code: p.period_code,
        status: p.status,
        due_date: p.due_date,
      }))
    }
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

watch(
  () => [session.actingUserId, session.sessionRevision],
  () => {
    loadDocumentsData()
  },
)

onMounted(() => {
  loadDocumentsData()
})
</script>

<template>
  <div class="w-full max-w-[1280px] mx-auto px-gutter py-space-lg space-y-6">
    <div>
      <span class="text-[11px] font-bold uppercase tracking-widest text-primary block mb-1">
        {{ session.buildingName }} • Reg {{ session.buildingRegistryCode }}
      </span>
      <h1 class="text-2xl sm:text-3xl font-bold text-on-surface tracking-tight">
        {{ session.t('documents.title') }}
      </h1>
      <p class="text-sm text-on-surface-variant mt-1">
        {{ session.t('documents.subtitle') }}
      </p>
    </div>

    <BaseSkeleton v-if="loading" :lines="3" />

    <ErrorCard
      v-else-if="error"
      :error="error"
      @retry="loadDocumentsData"
    />

    <template v-else>
      <!-- CSV Accounting Exports (Board / Chairman / Accountant only) -->
      <ReadingReportList
        v-if="canViewMatrix"
        :periods="periods"
      />

      <!-- Association Bylaws, Minutes & Financial Reports -->
      <AssociationFileGrid />

      <!-- Official Notices Archive from GET /communication/posts?post_type=OFFICIAL_NOTICE -->
      <NoticeList :notices="notices" />
    </template>
  </div>
</template>
