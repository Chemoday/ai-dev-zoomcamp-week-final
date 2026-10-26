import { apiRequest } from '../client.js'

/**
 * Get current acting user's building role, privacy mode, and apartment memberships.
 * @returns {Promise<import('../schema').MeResponse>}
 */
export function getMe() {
  return apiRequest('/property/me')
}

/**
 * Get building metadata: name, registry_code, address with district, apartment/entrance counts, heated area.
 * Added in PR #31 (GET /api/v1/property/building).
 * @returns {Promise<import('../schema').BuildingResponse>}
 */
export function getBuilding() {
  return apiRequest('/property/building')
}

/**
 * Update privacy display mode for a specific apartment membership.
 * @param {string} membershipId
 * @param {import('../schema').DisplayMode} privacyDisplayMode
 * @returns {Promise<import('../schema').PrivacyUpdateResponse>}
 */
export function updatePrivacy(membershipId, privacyDisplayMode) {
  return apiRequest(`/property/memberships/${encodeURIComponent(membershipId)}/privacy`, {
    method: 'PATCH',
    body: { privacy_display_mode: privacyDisplayMode },
  })
}
