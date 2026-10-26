<script setup>
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useSessionStore } from '@/stores/session.js'
import {
  DEFAULT_CYCLE_INDEX,
  DEFAULT_THEME_ID,
  getCycledTokens,
  getRotatedSwatches,
  getThemePreset,
  SWATCH_CYCLE_COUNT,
  THEME_CATEGORIES,
  THEME_CYCLE_MODES,
  THEME_PRESETS,
} from '@/utils/theme.js'

const session = useSessionStore()
const activeCategory = ref('all')

const filteredPresets = computed(() => {
  const list =
    activeCategory.value === 'all'
      ? THEME_PRESETS
      : THEME_PRESETS.filter((preset) => preset.category === activeCategory.value)

  return list.map((preset) => {
    const cardCycle = session.getPresetCycle(preset.id)
    return {
      ...preset,
      cardCycle,
      rotatedSwatches: getRotatedSwatches(preset, cardCycle),
      cycledTokens: getCycledTokens(preset, cardCycle),
    }
  })
})

const currentPreset = computed(() => getThemePreset(session.theme))
const currentCycle = computed(() => session.getPresetCycle(session.theme))
const currentRotatedSwatches = computed(() =>
  getRotatedSwatches(currentPreset.value, currentCycle.value),
)
const currentTokens = computed(() => getCycledTokens(currentPreset.value, currentCycle.value))

function selectTheme(themeId) {
  session.setTheme(themeId)
}

function cycleCardColors(themeId, event) {
  if (event) {
    event.stopPropagation()
  }
  session.cycleTheme(1, themeId)
}

function selectCycleMode(cycleIndex) {
  session.setThemeCycle(cycleIndex, session.theme)
}

function resetToDefault() {
  session.resetAllThemeCycles()
  session.setTheme(DEFAULT_THEME_ID)
}
</script>

<template>
  <div class="w-full max-w-[1280px] mx-auto px-gutter py-space-xl">
    <!-- Top Header & Actions -->
    <div class="flex flex-col lg:flex-row lg:items-end justify-between gap-space-md pb-space-lg border-b border-outline-variant/40">
      <div class="flex flex-col gap-space-xs max-w-2xl">
        <div class="flex items-center gap-2 font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
          <span>{{ session.t('themeLab.kicker') }}</span>
          <span aria-hidden="true">·</span>
          <span>
            {{ session.t('themeLab.activeTheme') }}:
            <strong class="text-on-surface">{{ currentPreset.name }}</strong>
          </span>
        </div>
        <h1 class="font-headline-xl text-headline-xl text-on-surface">
          {{ session.t('themeLab.title') }}
        </h1>
        <p class="font-body-md text-body-md text-on-surface-variant">
          {{ session.t('themeLab.subtitle') }}
        </p>
      </div>

      <div class="flex flex-wrap items-center gap-space-sm">
        <button
          v-if="session.theme !== DEFAULT_THEME_ID || currentCycle !== DEFAULT_CYCLE_INDEX"
          type="button"
          class="px-4 py-2 rounded-lg border border-outline-variant/60 bg-surface-container-lowest text-on-surface font-label-md text-label-md hover:bg-surface-container-low transition-colors cursor-pointer"
          @click="resetToDefault"
        >
          {{ session.t('themeLab.resetDefault') }}
        </button>
        <RouterLink
          to="/"
          class="px-4 py-2 rounded-lg bg-primary text-on-primary font-label-md text-label-md hover:opacity-95 transition-opacity inline-flex items-center gap-2"
        >
          <span>{{ session.t('themeLab.backToDashboard') }}</span>
        </RouterLink>
      </div>
    </div>

    <!-- Active Card 4-Color Swatch Cycling Panel -->
    <section
      class="mt-space-lg rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-space-md sm:p-space-lg flex flex-col gap-space-md"
    >
      <div class="flex flex-col lg:flex-row lg:items-center justify-between gap-space-md">
        <div class="flex flex-col gap-1 max-w-3xl">
          <div class="flex flex-wrap items-center gap-2">
            <h2 class="font-headline-md text-body-md font-semibold text-on-surface">
              {{ session.t('themeLab.cycleTitle') }}:
              <span class="text-secondary">{{ currentPreset.name }}</span>
            </h2>
            <span class="font-mono text-xs px-2 py-0.5 rounded bg-surface-container-low text-on-surface-variant border border-outline-variant/30">
              {{ currentCycle + 1 }} / {{ SWATCH_CYCLE_COUNT }}
            </span>
            <div class="flex items-center gap-1 ml-1">
              <span
                v-for="(hex, idx) in currentRotatedSwatches"
                :key="`${idx}-${hex}`"
                class="w-4 h-4 rounded-xs border border-outline-variant/60"
                :style="{ backgroundColor: hex }"
                :title="hex"
              />
            </div>
          </div>
          <p class="font-body-sm text-body-sm text-on-surface-variant">
            {{ session.t('themeLab.cycleSubtitle') }}
          </p>
        </div>

        <div class="flex items-center gap-2 shrink-0">
          <button
            v-if="currentCycle !== DEFAULT_CYCLE_INDEX"
            type="button"
            class="px-3.5 py-2.5 rounded-lg border border-outline-variant/60 bg-surface-container-low text-on-surface font-label-md text-label-md hover:bg-surface-container transition-colors cursor-pointer"
            @click="selectCycleMode(0)"
          >
            {{ session.t('themeLab.cycleReset') }}
          </button>
          <button
            type="button"
            class="px-4 py-2.5 rounded-lg bg-secondary text-on-secondary font-label-md text-label-md hover:opacity-95 transition-all cursor-pointer inline-flex items-center gap-2 shadow-xs"
            @click="cycleCardColors(session.theme)"
          >
            <svg
              class="w-4 h-4"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
              <path d="M3 3v5h5" />
              <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
              <path d="M16 16h5v5" />
            </svg>
            <span>{{ session.t('themeLab.cycleButton') }}</span>
          </button>
        </div>
      </div>

      <!-- 4 Cyclic Shift Positions for the Active Card -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 pt-1">
        <button
          v-for="mode in THEME_CYCLE_MODES"
          :key="mode.id"
          type="button"
          class="px-3 py-2.5 rounded-lg text-left font-label-md text-label-md transition-all cursor-pointer border flex items-center justify-between gap-2"
          :class="
            currentCycle === mode.index
              ? 'bg-primary text-on-primary border-primary shadow-xs'
              : 'bg-surface-container-low/70 text-on-surface-variant border-outline-variant/30 hover:text-on-surface hover:bg-surface-container'
          "
          @click="selectCycleMode(mode.index)"
        >
          <span class="truncate">{{ session.t(mode.labelKey) }}</span>
        </button>
      </div>
    </section>

    <!-- Category Segmented Filter Bar -->
    <div class="mt-space-lg flex flex-wrap items-center gap-1.5 p-1.5 rounded-xl bg-surface-container-low border border-outline-variant/30 w-fit">
      <button
        v-for="cat in THEME_CATEGORIES"
        :key="cat.id"
        type="button"
        class="px-3.5 py-2 rounded-lg font-label-md text-label-md transition-colors cursor-pointer"
        :class="
          activeCategory === cat.id
            ? 'bg-primary text-on-primary shadow-xs'
            : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container/60'
        "
        @click="activeCategory = cat.id"
      >
        {{ session.t(cat.labelKey) }}
      </button>
    </div>

    <!-- 20 Presets Grid -->
    <div class="mt-space-lg grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-space-md">
      <div
        v-for="(preset, index) in filteredPresets"
        :key="preset.id"
        role="button"
        tabindex="0"
        class="group text-left rounded-xl p-4 transition-all cursor-pointer flex flex-col justify-between border"
        :class="
          session.theme === preset.id
            ? 'bg-surface-container-lowest border-primary ring-2 ring-primary/20 shadow-sm'
            : 'bg-surface-container-lowest border-outline-variant/40 hover:border-outline'
        "
        @click="selectTheme(preset.id)"
        @keydown.enter.prevent="selectTheme(preset.id)"
        @keydown.space.prevent="selectTheme(preset.id)"
      >
        <div>
          <!-- Mini Architectural Specimen Preview Box (rendered in THIS card's own 4 colors) -->
          <div
            class="w-full h-28 rounded-lg p-3 flex flex-col justify-between border mb-3 transition-transform group-hover:scale-[1.01]"
            :style="{
              backgroundColor: preset.cycledTokens['--color-surface'],
              borderColor: preset.cycledTokens['--color-outline-variant'],
              color: preset.cycledTokens['--color-on-surface'],
            }"
          >
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-1.5">
                <span
                  class="w-2.5 h-2.5 rounded-xs"
                  :style="{ backgroundColor: preset.cycledTokens['--color-primary'] }"
                />
                <span
                  class="w-2.5 h-2.5 rounded-xs"
                  :style="{ backgroundColor: preset.cycledTokens['--color-secondary'] }"
                />
              </div>
              <span
                class="font-mono text-[10px] tracking-tight"
                :style="{ color: preset.cycledTokens['--color-on-surface-variant'] }"
              >
                {{ String(index + 1).padStart(2, '0') }} · {{ preset.id }}
              </span>
            </div>

            <!-- Simulated inner card inside mini preview -->
            <div
              class="rounded-md p-2 flex items-center justify-between border"
              :style="{
                backgroundColor: preset.cycledTokens['--color-surface-container-lowest'],
                borderColor: preset.cycledTokens['--color-outline-variant'],
              }"
            >
              <div class="flex flex-col gap-0.5">
                <span
                  class="text-[11px] font-semibold leading-none"
                  :style="{ color: preset.cycledTokens['--color-on-surface'] }"
                >
                  KÜ Sõpruse 12
                </span>
                <span
                  class="text-[10px] font-mono leading-none"
                  :style="{ color: preset.cycledTokens['--color-on-surface-variant'] }"
                >
                  142.800 m³
                </span>
              </div>
              <span
                class="px-2 py-1 rounded text-[10px] font-semibold leading-none"
                :style="{
                  backgroundColor: preset.cycledTokens['--color-primary'],
                  color: preset.cycledTokens['--color-on-primary'],
                }"
              >
                Aa
              </span>
            </div>
          </div>

          <!-- Title & Per-Card 4-Color Cycle Button -->
          <div class="flex items-center justify-between gap-2">
            <h2 class="font-headline-md text-body-md font-semibold text-on-surface">
              {{ preset.name }}
            </h2>
            <div class="flex items-center gap-1.5 shrink-0">
              <button
                type="button"
                class="px-1.5 py-1 rounded-md bg-surface-container-low hover:bg-surface-container text-on-surface border border-outline-variant/40 transition-colors cursor-pointer inline-flex items-center gap-1 font-mono text-[10px]"
                :title="session.t('themeLab.cycleButton')"
                @click="cycleCardColors(preset.id, $event)"
              >
                <svg
                  class="w-3 h-3"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  aria-hidden="true"
                >
                  <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
                  <path d="M3 3v5h5" />
                  <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
                  <path d="M16 16h5v5" />
                </svg>
                <span v-if="preset.cardCycle > 0">+{{ preset.cardCycle }}</span>
              </button>
              <span
                v-if="session.theme === preset.id"
                class="font-label-md text-label-md text-secondary font-semibold"
              >
                {{ session.t('themeLab.applied') }}
              </span>
            </div>
          </div>

          <p class="mt-1 font-body-sm text-body-sm text-on-surface-variant line-clamp-2">
            {{ preset.subtitle }}
          </p>
        </div>

        <!-- Swatch Strip & HEX codes ( strictly this card's 4 colors in their current cyclic order ) -->
        <div class="mt-4 pt-3 border-t border-outline-variant/30 flex items-center justify-between gap-2">
          <div class="flex items-center gap-1.5">
            <span
              v-for="(hex, swatchIdx) in preset.rotatedSwatches"
              :key="`${preset.id}-${swatchIdx}-${hex}`"
              class="w-5 h-5 rounded-md border border-outline-variant/50 transition-colors"
              :style="{ backgroundColor: hex }"
              :title="hex"
            />
          </div>
          <span class="font-mono text-xs text-on-surface-variant">
            {{ preset.rotatedSwatches[2] }} · {{ preset.rotatedSwatches[3] }}
          </span>
        </div>
      </div>
    </div>

    <!-- Live Specimen Section -->
    <section class="mt-space-xl pt-space-xl border-t border-outline-variant/40">
      <div class="flex flex-col gap-space-xs mb-space-lg">
        <h2 class="font-headline-lg text-headline-lg text-on-surface">
          {{ session.t('themeLab.previewTitle') }}
        </h2>
        <p class="font-body-md text-body-md text-on-surface-variant">
          {{ session.t('themeLab.previewSubtitle') }}
        </p>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-12 gap-space-lg">
        <!-- Surface Elevation Ladder -->
        <div class="lg:col-span-5 rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-space-lg flex flex-col gap-space-md">
          <span class="font-label-lg text-label-lg uppercase tracking-wider text-on-surface-variant">
            {{ session.t('themeLab.surfaceHierarchy') }}
          </span>
          <div class="flex flex-col gap-2">
            <div class="p-3 rounded-lg bg-surface border border-outline-variant/30 flex items-center justify-between">
              <span class="font-label-md text-label-md text-on-surface">surface</span>
              <span class="font-mono text-xs text-on-surface-variant">{{ currentTokens['--color-surface'] }}</span>
            </div>
            <div class="p-3 rounded-lg bg-surface-container-lowest border border-outline-variant/30 flex items-center justify-between">
              <span class="font-label-md text-label-md text-on-surface">surface-container-lowest</span>
              <span class="font-mono text-xs text-on-surface-variant">{{ currentTokens['--color-surface-container-lowest'] }}</span>
            </div>
            <div class="p-3 rounded-lg bg-surface-container-low border border-outline-variant/30 flex items-center justify-between">
              <span class="font-label-md text-label-md text-on-surface">surface-container-low</span>
              <span class="font-mono text-xs text-on-surface-variant">{{ currentTokens['--color-surface-container-low'] }}</span>
            </div>
            <div class="p-3 rounded-lg bg-surface-container border border-outline-variant/30 flex items-center justify-between">
              <span class="font-label-md text-label-md text-on-surface">surface-container</span>
              <span class="font-mono text-xs text-on-surface-variant">{{ currentTokens['--color-surface-container'] }}</span>
            </div>
            <div class="p-3 rounded-lg bg-surface-container-high border border-outline-variant/30 flex items-center justify-between">
              <span class="font-label-md text-label-md text-on-surface">surface-container-high</span>
              <span class="font-mono text-xs text-on-surface-variant">{{ currentTokens['--color-surface-container-high'] }}</span>
            </div>
          </div>
        </div>

        <!-- Interactive Component Specimen -->
        <div class="lg:col-span-7 rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-space-lg flex flex-col justify-between gap-space-lg">
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-space-sm pb-space-md border-b border-outline-variant/30">
            <div>
              <h3 class="font-headline-md text-headline-md text-on-surface">
                {{ session.t('themeLab.specimenCardTitle') }}
              </h3>
              <p class="font-body-sm text-body-sm text-on-surface-variant">
                {{ session.t('themeLab.specimenCardMeta') }}
              </p>
            </div>
            <div class="text-right">
              <span class="font-mono text-2xl font-bold text-on-surface">86.400 m³</span>
              <p class="font-body-sm text-body-sm text-secondary font-medium">+2.100 m³ vs prev</p>
            </div>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-3 gap-space-sm">
            <div class="p-3.5 rounded-lg bg-surface-container-low">
              <span class="font-label-md text-label-md text-on-surface-variant block">Previous (Feb)</span>
              <span class="font-mono text-base font-semibold text-on-surface mt-0.5 block">84.300 m³</span>
            </div>
            <div class="p-3.5 rounded-lg bg-surface-container-low">
              <span class="font-label-md text-label-md text-on-surface-variant block">3-Month Avg</span>
              <span class="font-mono text-base font-semibold text-on-surface mt-0.5 block">2.250 m³</span>
            </div>
            <div class="p-3.5 rounded-lg bg-secondary-container/50">
              <span class="font-label-md text-label-md text-on-secondary-container block">Consumption Status</span>
              <span class="font-mono text-base font-semibold text-on-secondary-container mt-0.5 block">-6.7% Normal</span>
            </div>
          </div>

          <div class="flex flex-wrap items-center gap-space-sm pt-space-sm">
            <button
              type="button"
              class="px-4 py-2.5 rounded-lg bg-primary text-on-primary font-label-md text-label-md cursor-pointer"
            >
              {{ session.t('themeLab.specimenPrimaryBtn') }}
            </button>
            <button
              type="button"
              class="px-4 py-2.5 rounded-lg bg-secondary text-on-secondary font-label-md text-label-md cursor-pointer"
            >
              {{ session.t('themeLab.specimenSecondaryBtn') }}
            </button>
            <button
              type="button"
              class="px-4 py-2.5 rounded-lg border border-outline-variant bg-surface-container-lowest text-on-surface font-label-md text-label-md cursor-pointer"
            >
              {{ session.t('themeLab.specimenOutlineBtn') }}
            </button>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
