<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'
import { useRole } from '@/composables/useRole.js'
import { getMyDashboard } from '@/api/endpoints/meters.js'
import { listPosts } from '@/api/endpoints/communication.js'
import { formatDate, formatPeriodLabel } from '@/utils/dates.js'
import DashboardMetersSummary from './components/DashboardMetersSummary.vue'
import DashboardLatestNotices from './components/DashboardLatestNotices.vue'
import BaseSkeleton from '@/components/base/BaseSkeleton.vue'
import ErrorCard from '@/components/domain/ErrorCard.vue'

const router = useRouter()
const session = useSessionStore()
const { greetingName, canViewMatrix } = useRole()

const dashboardData = ref(null)
const officialNotices = ref([])
const forumTopicsCount = ref(0)
const loading = ref(true)
const loadError = ref(null)

async function loadScreenData() {
  if (!session.actingUserId) return
  loading.value = true
  loadError.value = null
  try {
    const [dashRes, noticesRes, topicsRes] = await Promise.all([
      getMyDashboard(),
      listPosts({ post_type: 'OFFICIAL_NOTICE', limit: 5 }),
      listPosts({ post_type: 'FORUM_TOPIC', limit: 20 }),
    ])
    dashboardData.value = dashRes
    officialNotices.value = Array.isArray(noticesRes) ? noticesRes.slice(0, 3) : []
    forumTopicsCount.value = Array.isArray(topicsRes) ? topicsRes.length : 0
  } catch (err) {
    loadError.value = err
  } finally {
    loading.value = false
  }
}

onMounted(loadScreenData)

watch(
  () => [session.actingUserId, session.sessionRevision],
  () => {
    dashboardData.value = null
    officialNotices.value = []
    loadScreenData()
  },
)

const apartments = computed(() => dashboardData.value?.apartments || [])
const period = computed(() => dashboardData.value?.period || null)

const allMetersSubmitted = computed(() => {
  if (!apartments.value.length) return false
  return apartments.value.every((apt) => apt.meters.every((m) => m.reading !== null))
})

const periodLabel = computed(() =>
  period.value ? formatPeriodLabel(period.value.period_code, session.lang) : 'October 2026',
)
</script>

<template>
  <div class="w-full max-w-[1280px] mx-auto px-gutter py-space-lg">
    <!-- Top Welcome & Nordic State Header -->
    <section class="w-full mb-space-lg">
      <div class="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-surface-container flex flex-col lg:flex-row lg:items-center justify-between gap-space-md">
        <div class="flex flex-col gap-space-xs">
          <div class="flex items-center gap-space-xs text-xs">
            <span class="font-label-md text-label-md uppercase tracking-wider text-secondary font-semibold">
              {{ session.t('dashboard.portalBadge') }}
            </span>
            <span aria-hidden="true">·</span>
            <span class="font-label-md text-label-md text-on-surface-variant">
              {{ session.t('dashboard.liveSync') }}
            </span>
          </div>

          <h1 class="font-headline-xl text-headline-xl text-on-surface tracking-tight">
            {{ session.t('dashboard.greeting', { name: greetingName }) }}
          </h1>

          <div class="flex flex-wrap items-center gap-y-1 gap-x-space-md text-on-surface-variant">
            <span class="font-body-md text-body-md flex items-center gap-1.5">
              <span class="material-symbols-outlined text-[18px] text-on-surface-variant">apartment</span>
              {{ session.buildingName }} • {{ session.buildingApartmentsCount }} {{ session.t('dashboard.apartmentsLabel') }} ({{ session.buildingEntrancesCount }} {{ session.t('dashboard.entrancesLabel') }})
            </span>
            <span aria-hidden="true">·</span>
            <span class="font-body-md text-body-md flex items-center gap-1.5">
              <span class="material-symbols-outlined text-[18px] text-secondary">thermostat</span>
              {{ session.t('dashboard.heatingStatus') }}
            </span>
          </div>
        </div>

        <!-- Action Status & Direct Meter Link -->
        <div class="flex flex-col sm:flex-row sm:items-center gap-space-sm pt-space-xs lg:pt-0">
          <div
            v-if="apartments.length > 0"
            class="inline-flex items-center gap-2 px-space-md py-space-sm rounded-full"
            :class="
              allMetersSubmitted
                ? 'bg-secondary-container/60 text-on-secondary-container'
                : 'bg-amber-100 text-amber-900'
            "
          >
            <span class="material-symbols-outlined text-[18px]">
              {{ allMetersSubmitted ? 'check_circle' : 'schedule' }}
            </span>
            <span class="font-label-lg text-label-lg font-medium">
              {{ periodLabel }}:
              {{
                allMetersSubmitted
                  ? session.t('dashboard.readingsVerified')
                  : session.t('dashboard.readingsDue')
              }}
            </span>
          </div>

          <button
            v-if="apartments.length > 0"
            type="button"
            class="px-space-md py-space-sm rounded-lg bg-primary text-on-primary hover:bg-primary-container font-label-lg text-label-lg transition-colors flex items-center justify-center gap-1.5"
            @click="router.push('/readings')"
          >
            <span>{{ session.t('dashboard.viewReadings') }}</span>
            <span class="material-symbols-outlined text-[16px]">arrow_forward</span>
          </button>

          <button
            v-else-if="canViewMatrix"
            type="button"
            class="px-space-md py-space-sm rounded-lg bg-primary text-on-primary hover:bg-primary-container font-label-lg text-label-lg transition-colors flex items-center justify-center gap-1.5"
            @click="router.push('/accounting')"
          >
            <span>{{ session.t('dashboard.openReadingsMatrix') }}</span>
            <span class="material-symbols-outlined text-[16px]">arrow_forward</span>
          </button>
        </div>
      </div>
    </section>

    <ErrorCard v-if="loadError" :error="loadError" class="mb-space-lg" @retry="loadScreenData" />

    <div v-else-if="loading" class="mb-space-lg">
      <BaseSkeleton :lines="5" />
    </div>

    <template v-else>
      <!-- 4 Focused Metric KPI Cards -->
      <DashboardMetersSummary
        :period="period"
        :apartments="apartments"
        :forum-count="forumTopicsCount"
      />

      <!-- Two-Column Nordic Core Layout -->
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-space-lg items-start">
        <!-- LEFT MAIN COLUMN (8 cols) -->
        <div class="lg:col-span-8 flex flex-col gap-space-lg">
          <DashboardLatestNotices :notices="officialNotices" />

          <!-- Quick Building Shortcuts & Tools -->
          <div class="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-surface-container">
            <h2 class="font-headline-md text-headline-md font-bold text-on-surface mb-space-md">
              {{ session.t('dashboard.quickServicesTitle') }}
            </h2>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-space-md">
              <!-- Tool 1: Submit Readings / Matrix -->
              <div class="p-space-md rounded-lg bg-surface-container-low flex flex-col justify-between hover:bg-surface-container transition-colors">
                <div class="flex flex-col gap-space-xs">
                  <div class="w-10 h-10 rounded-lg bg-surface-container-lowest flex items-center justify-center text-primary shadow-xs">
                    <span class="material-symbols-outlined text-[20px]">speed</span>
                  </div>
                  <h4 class="font-label-lg text-label-lg font-bold text-on-surface mt-space-xs">
                    {{ session.t('dashboard.utilityPortalTitle') }}
                  </h4>
                  <p class="font-body-sm text-body-sm text-on-surface-variant">
                    {{ session.t('dashboard.utilityPortalDesc') }}
                  </p>
                </div>
                <button
                  type="button"
                  class="mt-space-md w-full py-2 px-space-sm rounded bg-surface-container-lowest font-label-md text-label-md font-semibold text-on-surface hover:bg-primary hover:text-on-primary transition-colors text-center"
                  @click="router.push('/readings')"
                >
                  {{ session.t('dashboard.goToReadings') }}
                </button>
              </div>

              <!-- Tool 2: Documents & Reports -->
              <div class="p-space-md rounded-lg bg-surface-container-low flex flex-col justify-between hover:bg-surface-container transition-colors">
                <div class="flex flex-col gap-space-xs">
                  <div class="w-10 h-10 rounded-lg bg-surface-container-lowest flex items-center justify-center text-primary shadow-xs">
                    <span class="material-symbols-outlined text-[20px]">folder_open</span>
                  </div>
                  <h4 class="font-label-lg text-label-lg font-bold text-on-surface mt-space-xs">
                    {{ session.t('dashboard.docsReportsTitle') }}
                  </h4>
                  <p class="font-body-sm text-body-sm text-on-surface-variant">
                    {{ session.t('dashboard.docsReportsDesc') }}
                  </p>
                </div>
                <button
                  type="button"
                  class="mt-space-md w-full py-2 px-space-sm rounded bg-surface-container-lowest font-label-md text-label-md font-semibold text-on-surface hover:bg-primary hover:text-on-primary transition-colors text-center flex items-center justify-center gap-1"
                  @click="router.push('/documents')"
                >
                  <span class="material-symbols-outlined text-[16px]">description</span>
                  <span>{{ session.t('dashboard.viewDocuments') }}</span>
                </button>
              </div>

              <!-- Tool 3: Emergency Dispatch -->
              <div class="p-space-md rounded-lg bg-surface-container-low flex flex-col justify-between hover:bg-surface-container transition-colors">
                <div class="flex flex-col gap-space-xs">
                  <div class="w-10 h-10 rounded-lg bg-surface-container-lowest flex items-center justify-center text-error shadow-xs">
                    <span class="material-symbols-outlined text-[20px]">support_agent</span>
                  </div>
                  <h4 class="font-label-lg text-label-lg font-bold text-on-surface mt-space-xs">
                    {{ session.t('dashboard.emergencyTitle') }}
                  </h4>
                  <p class="font-body-sm text-body-sm text-on-surface-variant">
                    {{ session.t('dashboard.emergencyDesc') }}
                  </p>
                </div>
                <a
                  href="tel:+3726543210"
                  class="mt-space-md w-full py-2 px-space-sm rounded bg-surface-container-lowest font-label-md text-label-md font-bold text-on-surface hover:bg-error hover:text-on-error transition-colors text-center block"
                >
                  {{ session.t('dashboard.callEmergency') }}
                </a>
              </div>
            </div>
          </div>

          <!-- Communal Property Snapshot (Resilient SVG Architectural Illustration) -->
          <div class="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-surface-container flex flex-col md:flex-row items-center gap-space-lg">
            <div class="w-full md:w-1/3 aspect-[4/3] rounded-lg overflow-hidden relative bg-surface-container flex items-center justify-center">
              <svg
                viewBox="0 0 320 240"
                class="w-full h-full object-cover"
                role="img"
                aria-label="Sõpruse pst 12 Renovated Nordic Apartment Building"
              >
                <defs>
                  <linearGradient id="skyGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#dce9ff" />
                    <stop offset="100%" stop-color="#f8f9ff" />
                  </linearGradient>
                </defs>
                <rect width="320" height="240" fill="url(#skyGrad)" />
                <rect x="0" y="195" width="320" height="45" fill="#d3e4fe" />
                <rect x="42" y="42" width="236" height="153" rx="4" fill="#131b2e" />
                <rect x="48" y="48" width="224" height="147" rx="2" fill="#ffffff" />
                <!-- 4 floors of Nordic slatted wooden balconies & windows -->
                <g fill="#eff4ff" stroke="#cbdbf5" stroke-width="1">
                  <rect x="60" y="60" width="44" height="24" rx="2" />
                  <rect x="116" y="60" width="44" height="24" rx="2" />
                  <rect x="172" y="60" width="44" height="24" rx="2" />
                  <rect x="224" y="60" width="36" height="24" rx="2" />

                  <rect x="60" y="94" width="44" height="24" rx="2" />
                  <rect x="116" y="94" width="44" height="24" rx="2" />
                  <rect x="172" y="94" width="44" height="24" rx="2" />
                  <rect x="224" y="94" width="36" height="24" rx="2" />

                  <rect x="60" y="128" width="44" height="24" rx="2" />
                  <rect x="116" y="128" width="44" height="24" rx="2" />
                  <rect x="172" y="128" width="44" height="24" rx="2" />
                  <rect x="224" y="128" width="36" height="24" rx="2" />
                </g>
                <!-- Entrances 1 & 2 -->
                <rect x="95" y="164" width="32" height="31" rx="2" fill="#006c4a" />
                <rect x="195" y="164" width="32" height="31" rx="2" fill="#006c4a" />
                <!-- Birch trees -->
                <circle cx="30" cy="165" r="20" fill="#82f5c1" opacity="0.85" />
                <rect x="28" y="175" width="4" height="20" fill="#45464d" />
                <circle cx="292" cy="160" r="22" fill="#82f5c1" opacity="0.85" />
                <rect x="290" y="172" width="4" height="23" fill="#45464d" />
              </svg>
              <div class="absolute bottom-2 left-2 px-2 py-1 bg-surface-container-lowest/90 backdrop-blur-xs rounded font-label-md text-[11px] font-semibold text-on-surface">
                {{ session.t('dashboard.renovatedYear') }}
              </div>
            </div>

            <div class="w-full md:w-2/3 flex flex-col justify-between">
              <div class="flex items-center gap-space-xs mb-1">
                <span class="font-label-md text-label-md uppercase tracking-wider text-secondary font-bold">
                  {{ session.t('dashboard.energyPerformance') }}
                </span>
                <span class="px-2 py-0.5 rounded bg-secondary-container text-on-secondary-container font-label-md text-label-md font-bold">
                  Class B
                </span>
              </div>
              <h3 class="font-headline-md text-headline-md font-bold text-on-surface">
                {{ session.buildingAddress }} ({{ session.t('common.reg') }} {{ session.buildingRegistryCode }})
              </h3>
              <p class="font-body-sm text-body-sm text-on-surface-variant mt-1 leading-relaxed">
                {{ session.t('dashboard.buildingFeaturesDesc') }}
              </p>
              <div class="grid grid-cols-3 gap-space-sm mt-space-md pt-space-xs">
                <div class="flex flex-col">
                  <span class="font-label-md text-label-md text-on-surface-variant">
                    {{ session.t('dashboard.apartmentsEntrances') }}
                  </span>
                  <span class="font-headline-md text-headline-md font-bold text-on-surface font-numeric-display">
                    {{ session.buildingApartmentsCount }} / {{ session.buildingEntrancesCount }}
                  </span>
                </div>
                <div class="flex flex-col">
                  <span class="font-label-md text-label-md text-on-surface-variant">
                    {{ session.t('dashboard.heatedArea') }}
                  </span>
                  <span class="font-headline-md text-headline-md font-bold text-on-surface font-numeric-display">
                    {{ session.buildingHeatedArea }} m²
                  </span>
                </div>
                <div class="flex flex-col">
                  <span class="font-label-md text-label-md text-on-surface-variant">
                    {{ session.t('dashboard.registryCodeLabel') }}
                  </span>
                  <span class="font-headline-md text-headline-md font-bold text-secondary font-numeric-display">
                    {{ session.buildingRegistryCode }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- RIGHT PERSONAL OVERVIEW COLUMN (4 cols) -->
        <div class="lg:col-span-4 flex flex-col gap-space-lg">
          <!-- Render one card per apartment from GET /property/me memberships (or hide if apartments: []) -->
          <template v-if="session.memberships.length > 0">
            <div
              v-for="m in session.memberships"
              :key="m.membership_id"
              class="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-surface-container"
            >
              <div class="flex items-center justify-between pb-space-sm mb-space-sm border-b border-surface-container">
                <div class="flex items-center gap-2.5">
                  <div class="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-on-primary font-bold font-label-md text-label-md">
                    {{ m.apartment_number }}
                  </div>
                  <div class="flex flex-col">
                    <span class="font-label-lg text-label-lg font-bold text-on-surface">
                      {{ session.t('common.apartmentNumber', { number: m.apartment_number }) }}
                    </span>
                    <span class="font-label-md text-label-md text-on-surface-variant">
                      {{ session.t('common.floorEntranceComma', { floor: m.floor, entrance: m.entrance }) }}
                    </span>
                  </div>
                </div>
                <span class="px-2.5 py-0.5 rounded-full bg-surface-container font-label-md text-label-md text-on-surface font-semibold">
                  {{ m.relation_type }}
                </span>
              </div>

              <div class="flex flex-col gap-space-sm">
                <div class="flex items-center justify-between py-1">
                  <span class="font-body-sm text-body-sm text-on-surface-variant">
                    {{ session.t('dashboard.submitReadingsRight') }}
                  </span>
                  <span class="font-label-lg text-label-lg font-semibold text-secondary flex items-center gap-1">
                    <span class="material-symbols-outlined text-[16px]">
                      {{ m.can_submit_readings ? 'check_circle' : 'lock' }}
                    </span>
                    {{ m.can_submit_readings ? session.t('dashboard.authorized') : session.t('dashboard.readOnly') }}
                  </span>
                </div>

                <div class="flex items-center justify-between py-1">
                  <span class="font-body-sm text-body-sm text-on-surface-variant">
                    {{ session.t('dashboard.privacyDisplayMode') }}
                  </span>
                  <span class="font-label-lg text-label-lg font-semibold text-on-surface">
                    {{ m.privacy_display_mode }}
                  </span>
                </div>

                <div class="flex items-center justify-between py-1">
                  <span class="font-body-sm text-body-sm text-on-surface-variant">
                    {{ session.t('dashboard.forumBadgePreview') }}
                  </span>
                  <span class="font-label-md text-xs font-semibold text-on-surface bg-surface-container-low px-2 py-0.5 rounded">
                    {{ m.preview_badge.display_label }} • {{ m.preview_badge.role_badge }}
                  </span>
                </div>

                <div class="flex items-center justify-between py-1">
                  <span class="font-body-sm text-body-sm text-on-surface-variant">
                    {{ session.t('dashboard.generalAssemblyVote') }}
                  </span>
                  <span class="font-label-lg text-label-lg font-semibold text-on-surface">
                    {{ m.can_vote ? session.t('dashboard.yesOwner') : session.t('dashboard.no') }}
                  </span>
                </div>
              </div>

              <div class="mt-space-md pt-space-sm">
                <button
                  type="button"
                  class="w-full py-2 rounded-lg bg-surface-container-high hover:bg-surface-container-highest text-on-surface font-label-md text-label-md font-semibold transition-colors flex items-center justify-center gap-1.5"
                  @click="router.push('/readings')"
                >
                  <span class="material-symbols-outlined text-[16px]">speed</span>
                  <span>
                    {{ session.t('dashboard.manageAptMeters', { number: m.apartment_number }) }}
                  </span>
                </button>
              </div>
            </div>
          </template>

          <!-- Next Association Milestones Timeline -->
          <div class="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-surface-container">
            <div class="flex items-center justify-between mb-space-md">
              <div class="flex items-center gap-space-xs">
                <span class="material-symbols-outlined text-[20px] text-primary">calendar_month</span>
                <h3 class="font-label-lg text-label-lg font-bold text-on-surface uppercase tracking-wider">
                  {{ session.t('dashboard.milestonesTitle') }}
                </h3>
              </div>
              <span class="font-label-md text-label-md text-on-surface-variant">
                {{ period ? formatDate(period.due_date, session.lang) : 'Nov 2026' }}
              </span>
            </div>

            <div class="relative pl-6 space-y-space-md before:absolute before:left-2 before:top-2 before:bottom-2 before:w-0.5 before:bg-surface-container-highest">
              <div class="relative">
                <span class="absolute -left-6 top-1 w-2.5 h-2.5 rounded-full bg-secondary ring-4 ring-surface-container-lowest" />
                <div class="flex flex-col">
                  <span class="font-label-md text-label-md font-bold text-secondary">Nov 4 • 23:59</span>
                  <span class="font-body-md text-body-md font-semibold text-on-surface leading-snug">
                    {{ session.t('dashboard.milestone1Title') }}
                  </span>
                  <span class="font-body-sm text-body-sm text-on-surface-variant">
                    {{ session.t('dashboard.milestone1Desc') }}
                  </span>
                </div>
              </div>

              <div class="relative">
                <span class="absolute -left-6 top-1 w-2.5 h-2.5 rounded-full bg-primary ring-4 ring-surface-container-lowest" />
                <div class="flex flex-col">
                  <span class="font-label-md text-label-md font-bold text-on-surface">Nov 6</span>
                  <span class="font-body-md text-body-md font-semibold text-on-surface leading-snug">
                    {{ session.t('dashboard.milestone2Title') }}
                  </span>
                  <span class="font-body-sm text-body-sm text-on-surface-variant">
                    {{ session.t('dashboard.milestone2Desc') }}
                  </span>
                </div>
              </div>

              <div class="relative">
                <span class="absolute -left-6 top-1 w-2.5 h-2.5 rounded-full bg-primary ring-4 ring-surface-container-lowest" />
                <div class="flex flex-col">
                  <span class="font-label-md text-label-md font-bold text-on-surface">Nov 10</span>
                  <span class="font-body-md text-body-md font-semibold text-on-surface leading-snug">
                    {{ session.t('dashboard.milestone3Title') }}
                  </span>
                  <span class="font-body-sm text-body-sm text-on-surface-variant">
                    {{ session.t('dashboard.milestone3Desc') }}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <!-- Weekly Heat Efficiency Widget (SVG Sparkline from Mockup) -->
          <div class="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-surface-container flex flex-col justify-between">
            <div class="flex items-center justify-between mb-space-sm">
              <div class="flex flex-col">
                <span class="font-label-md text-label-md uppercase tracking-wider text-on-surface-variant font-semibold">
                  {{ session.t('dashboard.weeklyHeatEfficiency') }}
                </span>
                <span class="font-headline-md text-headline-md font-bold text-on-surface font-numeric-display">
                  0.038 MWh/m²
                </span>
              </div>
              <div class="w-8 h-8 rounded-full bg-secondary-container flex items-center justify-center text-on-secondary-container">
                <span class="material-symbols-outlined text-[18px]">energy_savings_leaf</span>
              </div>
            </div>
            <div class="w-full py-2">
              <svg class="w-full h-14" fill="none" viewBox="0 0 240 60" xmlns="http://www.w3.org/2000/svg">
                <path
                  class="text-secondary"
                  d="M0 45 C 30 42, 50 50, 80 32 C 110 14, 130 28, 160 18 C 190 8, 210 22, 240 12"
                  stroke="currentColor"
                  stroke-linecap="round"
                  stroke-width="2.5"
                />
                <path
                  class="text-secondary/10"
                  d="M0 45 C 30 42, 50 50, 80 32 C 110 14, 130 28, 160 18 C 190 8, 210 22, 240 12 L 240 60 L 0 60 Z"
                  fill="currentColor"
                />
                <circle class="fill-secondary" cx="240" cy="12" r="4" />
              </svg>
              <div class="flex items-center justify-between text-on-surface-variant font-label-md text-label-md mt-1">
                <span>Mon</span>
                <span>Wed</span>
                <span>Fri</span>
                <span>Sun</span>
              </div>
            </div>
            <p class="font-body-sm text-body-sm text-on-surface-variant mt-space-xs">
              {{ session.t('dashboard.heatEfficiencyDesc') }}
            </p>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
