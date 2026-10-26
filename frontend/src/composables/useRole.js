import { computed } from 'vue'
import { useSessionStore } from '@/stores/session.js'

/**
 * Role and permission checks derived strictly from GET /api/v1/property/me
 * (Section 3 of docs/FRONTEND_GUIDE.md).
 */
export function useRole() {
  const session = useSessionStore()

  const buildingRole = computed(() => session.me?.building_role || 'USER')

  const isChairman = computed(() => buildingRole.value === 'CHAIRMAN')
  const isBoard = computed(() => buildingRole.value === 'BOARD')
  const isAccountant = computed(() => buildingRole.value === 'ACCOUNTANT')
  const isResident = computed(() => buildingRole.value === 'USER')

  // Matrix, history, CSV export: BOARD, CHAIRMAN, ACCOUNTANT
  const canViewMatrix = computed(() =>
    ['BOARD', 'CHAIRMAN', 'ACCOUNTANT'].includes(buildingRole.value),
  )

  // Period status transitions & estimate missing: CHAIRMAN, ACCOUNTANT
  const canManagePeriods = computed(() =>
    ['CHAIRMAN', 'ACCOUNTANT'].includes(buildingRole.value),
  )

  // Submit any meter in matrix (incl. master meters): CHAIRMAN, ACCOUNTANT
  const canSubmitAnyMeter = computed(() =>
    ['CHAIRMAN', 'ACCOUNTANT'].includes(buildingRole.value),
  )

  // Publish emergency / official notice & resolve emergency: BOARD, CHAIRMAN
  const canPublishOfficial = computed(() =>
    ['BOARD', 'CHAIRMAN'].includes(buildingRole.value),
  )

  // Delete a forum topic: CHAIRMAN only
  const canDeleteForumTopic = computed(() => buildingRole.value === 'CHAIRMAN')

  // Whether the user has at least one apartment with can_submit_readings
  const canSubmitOwnReadings = computed(() =>
    (session.me?.memberships || []).some((m) => m.can_submit_readings),
  )

  // Greet with switcher label or membership preview_badge (never expose real PII unless chosen in preview_badge)
  const greetingName = computed(() => {
    const badgeLabel = session.activeMembership?.preview_badge?.display_label
    if (badgeLabel) return badgeLabel
    if (session.currentDemoUser?.label) return session.currentDemoUser.label
    return buildingRole.value
  })

  return {
    buildingRole,
    isChairman,
    isBoard,
    isAccountant,
    isResident,
    canViewMatrix,
    canManagePeriods,
    canSubmitAnyMeter,
    canPublishOfficial,
    canDeleteForumTopic,
    canSubmitOwnReadings,
    greetingName,
  }
}
