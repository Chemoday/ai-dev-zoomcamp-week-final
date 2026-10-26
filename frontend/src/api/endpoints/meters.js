import { apiRequest } from '../client.js'

/**
 * List all billing periods of the building, newest first.
 * Added in PR #31 (GET /api/v1/meters/periods).
 * @returns {Promise<import('../schema').PeriodDTO[]>}
 */
export function listPeriods() {
  return apiRequest('/meters/periods')
}

/**
 * Load resident meter dashboard for an optional period_code.
 * @param {string | null} [periodCode]
 * @returns {Promise<import('../schema').DashboardResponse>}
 */
export function getMyDashboard(periodCode = null) {
  const query = periodCode ? `?period_code=${encodeURIComponent(periodCode)}` : ''
  return apiRequest(`/meters/my-dashboard${query}`)
}

/**
 * Submit a meter reading (supports confirmation_level 0, 1, 2 and optional reason).
 * @param {import('../schema').ReadingSubmitRequest} payload
 * @returns {Promise<import('../schema').ReadingResponse>}
 */
export function submitReading(payload) {
  return apiRequest('/meters/readings', {
    method: 'POST',
    body: payload,
  })
}

/**
 * Get audit history for a specific reading_id.
 * @param {string} readingId
 * @returns {Promise<import('../schema').ReadingHistoryResponse>}
 */
export function getReadingHistory(readingId) {
  return apiRequest(`/meters/readings/${encodeURIComponent(readingId)}/history`)
}

/**
 * Get period consumption matrix for board/chairman/accountant.
 * @param {string} periodCode
 * @returns {Promise<import('../schema').MatrixResponse>}
 */
export function getPeriodMatrix(periodCode) {
  return apiRequest(`/meters/periods/${encodeURIComponent(periodCode)}/matrix`)
}

/**
 * Auto-estimate missing readings in a period (only in REVIEW_BY_BOARD).
 * @param {string} periodCode
 * @returns {Promise<import('../schema').EstimateResponse>}
 */
export function estimateMissingReadings(periodCode) {
  return apiRequest(`/meters/periods/${encodeURIComponent(periodCode)}/estimate-missing`, {
    method: 'POST',
  })
}

/**
 * Transition period status (OPEN_FOR_RESIDENTS <-> REVIEW_BY_BOARD <-> LOCKED).
 * @param {string} periodCode
 * @param {import('../schema').PeriodStatus} status
 * @returns {Promise<import('../schema').PeriodStatusUpdateResponse>}
 */
export function changePeriodStatus(periodCode, status) {
  return apiRequest(`/meters/periods/${encodeURIComponent(periodCode)}/status`, {
    method: 'PATCH',
    body: { status },
  })
}

/**
 * Export period readings as CSV (raw Response for Blob download).
 * @param {string} periodCode
 * @returns {Promise<Response>}
 */
export function exportPeriodCsv(periodCode) {
  return apiRequest(`/meters/periods/${encodeURIComponent(periodCode)}/export-csv`, {
    raw: true,
  })
}
