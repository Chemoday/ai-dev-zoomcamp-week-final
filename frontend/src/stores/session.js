import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { activeDemoUserId, setDemoUser } from '@/api/client.js'
import { getDemoUsers } from '@/api/endpoints/demo.js'
import { getBuilding, getMe, updatePrivacy } from '@/api/endpoints/property.js'
import { i18n, readStoredLocale, setI18nLocale, SUPPORTED_LOCALES } from '@/locales/index.js'
import {
  applyTheme,
  DEFAULT_CYCLE_INDEX,
  normalizeCycleIndex,
  readStoredTheme,
  readStoredThemeCyclesMap,
  THEME_CYCLE_MODES,
  THEME_PRESETS,
  writeStoredThemeCyclesMap,
} from '@/utils/theme.js'

export const useSessionStore = defineStore('session', () => {
  const demoUsers = ref([])
  const demoUsersLoaded = ref(false)
  const demoUsersError = ref(null)

  const me = ref(null)
  const building = ref(null)
  const meLoading = ref(false)
  const meError = ref(null)

  const selectedApartmentId = ref('')
  const lang = ref(setI18nLocale(readStoredLocale()))
  const themeCycles = ref(readStoredThemeCyclesMap())
  const initialThemeId = readStoredTheme()
  const theme = ref(applyTheme(initialThemeId, themeCycles.value[initialThemeId] ?? DEFAULT_CYCLE_INDEX))
  const themeCycle = computed(() => normalizeCycleIndex(themeCycles.value[theme.value] ?? DEFAULT_CYCLE_INDEX))
  const sessionRevision = ref(0)

  const actingUserId = computed(() => activeDemoUserId.value)

  const currentDemoUser = computed(() =>
    demoUsers.value.find((u) => u.user_id === actingUserId.value) || null,
  )

  const memberships = computed(() => me.value?.memberships || [])

  const activeMembership = computed(() => {
    if (!memberships.value.length) return null
    return (
      memberships.value.find((m) => m.apartment_id === selectedApartmentId.value) ||
      memberships.value[0]
    )
  })

  // Building metadata derived from GET /api/v1/property/building (PR #31)
  const buildingName = computed(() => building.value?.name || 'KÜ Sõpruse 12')
  const buildingRegistryCode = computed(() => building.value?.registry_code || '80123456')
  const buildingAddress = computed(
    () => building.value?.address || 'Sõpruse pst 12, Kristiine, Tallinn',
  )
  const buildingApartmentsCount = computed(
    () =>
      building.value?.apartments_count ??
      building.value?.total_apartments ??
      building.value?.apartment_count ??
      36,
  )
  const buildingEntrancesCount = computed(
    () =>
      building.value?.entrances_count ??
      building.value?.total_entrances ??
      building.value?.entrance_count ??
      2,
  )
  const buildingHeatedArea = computed(
    () =>
      building.value?.heated_area_m2 ??
      building.value?.heated_area ??
      building.value?.total_area_m2 ??
      '329.20',
  )

  function setLang(newLang) {
    const valid = setI18nLocale(newLang)
    lang.value = valid
  }

  function getPresetCycle(presetId) {
    return normalizeCycleIndex(themeCycles.value[presetId] ?? DEFAULT_CYCLE_INDEX)
  }

  function setTheme(newThemeId) {
    const cycleForTarget = getPresetCycle(newThemeId)
    theme.value = applyTheme(newThemeId, cycleForTarget)
  }

  function setThemeCycle(cycleIndex, targetThemeId = theme.value) {
    const normalized = normalizeCycleIndex(cycleIndex)
    themeCycles.value = {
      ...themeCycles.value,
      [targetThemeId]: normalized,
    }
    writeStoredThemeCyclesMap(themeCycles.value)
    if (targetThemeId === theme.value) {
      applyTheme(theme.value, normalized)
    }
  }

  function cycleTheme(step = 1, targetThemeId = theme.value) {
    const current = getPresetCycle(targetThemeId)
    const next = normalizeCycleIndex(current + step)
    themeCycles.value = {
      ...themeCycles.value,
      [targetThemeId]: next,
    }
    writeStoredThemeCyclesMap(themeCycles.value)
    theme.value = applyTheme(targetThemeId, next)
  }

  function resetAllThemeCycles() {
    themeCycles.value = {}
    writeStoredThemeCyclesMap({})
  }

  /**
   * Translate key using active locale stored in Pinia & vue-i18n.
   * Accessing lang.value ensures reactive tracking when called in templates/computed.
   * @param {string} key
   * @param {Record<string, any>} [params]
   * @returns {string}
   */
  function t(key, params = {}) {
    const current = lang.value
    return i18n.global.t(key, params, { locale: current })
  }

  function selectApartment(apartmentId) {
    selectedApartmentId.value = apartmentId
  }

  async function loadDemoUsers() {
    demoUsersError.value = null
    try {
      const list = await getDemoUsers()
      demoUsers.value = Array.isArray(list) ? list : []
      demoUsersLoaded.value = true

      // Default to "Owner Apt 14" if no stored acting user or stored id is not in list
      if (demoUsers.value.length > 0) {
        const currentValid = demoUsers.value.some((u) => u.user_id === actingUserId.value)
        if (!actingUserId.value || !currentValid) {
          const apt14 =
            demoUsers.value.find((u) => u.apartment_number === '14') || demoUsers.value[0]
          setDemoUser(apt14.user_id)
        }
      }
      return demoUsers.value
    } catch (error) {
      demoUsersError.value = error
      demoUsersLoaded.value = true
      return []
    }
  }

  async function loadMe() {
    if (!actingUserId.value) {
      me.value = null
      building.value = null
      return null
    }
    meLoading.value = true
    meError.value = null
    try {
      const [meData, buildingData] = await Promise.all([
        getMe(),
        getBuilding().catch(() => null),
      ])
      me.value = meData
      if (buildingData) {
        building.value = buildingData
      }
      if (meData.memberships?.length) {
        const stillValid = meData.memberships.some(
          (m) => m.apartment_id === selectedApartmentId.value,
        )
        if (!stillValid) {
          selectedApartmentId.value = meData.memberships[0].apartment_id
        }
      } else {
        selectedApartmentId.value = ''
      }
      return meData
    } catch (error) {
      me.value = null
      meError.value = error
      if (error?.status === 401) {
        setDemoUser('')
      }
      return null
    } finally {
      meLoading.value = false
    }
  }

  async function initializeSession() {
    if (!demoUsersLoaded.value) {
      await loadDemoUsers()
    }
    if (actingUserId.value && !me.value) {
      await loadMe()
    }
  }

  async function switchUser(userId) {
    if (!userId) {
      me.value = null
      building.value = null
      selectedApartmentId.value = ''
      setDemoUser('')
      sessionRevision.value += 1
      return
    }
    // Clear previous user's state immediately so no stale data survives
    me.value = null
    selectedApartmentId.value = ''
    setDemoUser(userId)
    sessionRevision.value += 1
    await loadMe()
  }

  async function changePrivacyMode(membershipId, privacyDisplayMode) {
    const updated = await updatePrivacy(membershipId, privacyDisplayMode)
    if (me.value && Array.isArray(me.value.memberships)) {
      me.value = {
        ...me.value,
        privacy_display_mode: updated.privacy_display_mode,
        memberships: me.value.memberships.map((m) =>
          m.membership_id === membershipId
            ? {
                ...m,
                privacy_display_mode: updated.privacy_display_mode,
                preview_badge: updated.preview_badge,
              }
            : m,
        ),
      }
    }
    sessionRevision.value += 1
    return updated
  }

  return {
    demoUsers,
    demoUsersLoaded,
    demoUsersError,
    me,
    building,
    buildingName,
    buildingRegistryCode,
    buildingAddress,
    buildingApartmentsCount,
    buildingEntrancesCount,
    buildingHeatedArea,
    meLoading,
    meError,
    actingUserId,
    currentDemoUser,
    memberships,
    activeMembership,
    selectedApartmentId,
    lang,
    supportedLocales: SUPPORTED_LOCALES,
    theme,
    themeCycle,
    themeCycles,
    themeCycleModes: THEME_CYCLE_MODES,
    themePresets: THEME_PRESETS,
    sessionRevision,
    setLang,
    getPresetCycle,
    setTheme,
    setThemeCycle,
    cycleTheme,
    resetAllThemeCycles,
    t,
    selectApartment,
    loadDemoUsers,
    loadMe,
    initializeSession,
    switchUser,
    changePrivacyMode,
  }
})

