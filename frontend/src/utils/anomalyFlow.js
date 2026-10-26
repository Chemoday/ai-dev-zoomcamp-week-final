import { normalizeMeterInput } from './meterValue.js'

/**
 * @typedef {object} AnomalyPopupInfo
 * @property {number} step
 * @property {string} delta
 * @property {string | null} [average]
 * @property {string | null} [limit]
 */

/**
 * @typedef {object} AnomalyFlowState
 * @property {AnomalyPopupInfo | null} anomaly
 * @property {string | null} pendingValue
 * @property {string | null} fieldError
 * @property {any} apiError
 */

/**
 * Pure state helpers for the two-popup anomaly confirmation flow (Task 6).
 * Always starts at confirmation_level: 0.
 * Editing input while a popup is open cancels the popup and restarts at level 0.
 * @returns {AnomalyFlowState}
 */
export function createAnomalyFlowState() {
  return {
    anomaly: null,
    pendingValue: null,
    fieldError: null,
    apiError: null,
  }
}

/**
 * Prepare a submission payload and validate input before sending.
 * @param {AnomalyFlowState} state
 * @param {string} rawInput
 * @param {number} requestedLevel
 * @returns {{ ok: boolean, value?: string, confirmationLevel?: number, nextState: AnomalyFlowState }}
 */
export function prepareReadingSubmission(state, rawInput, requestedLevel = 0) {
  const level = state.anomaly ? requestedLevel : 0
  const value = level === 0 ? normalizeMeterInput(rawInput) : state.pendingValue

  if (value === null) {
    return {
      ok: false,
      nextState: {
        ...state,
        anomaly: null,
        fieldError: 'invalid_number',
        apiError: null,
      },
    }
  }

  return {
    ok: true,
    value,
    confirmationLevel: level,
    nextState: {
      ...state,
      pendingValue: value,
      fieldError: null,
      apiError: null,
    },
  }
}

/**
 * Transition state based on an API error from POST /api/v1/meters/readings.
 * @param {AnomalyFlowState} state
 * @param {any} error - ApiError with errorCode and payload
 * @returns {AnomalyFlowState}
 */
export function transitionOnReadingError(state, error) {
  const code = error?.errorCode || ''
  const payload = error?.payload || {}

  if (code === 'ANOMALY_CONFIRMATION_STEP_1') {
    return {
      ...state,
      fieldError: null,
      apiError: null,
      anomaly: {
        step: 1,
        delta: payload.consumption_delta || '0.000',
        average: payload.average_delta || null,
        limit: payload.max_reasonable_delta || null,
      },
    }
  }

  if (code === 'ANOMALY_CONFIRMATION_STEP_2') {
    return {
      ...state,
      fieldError: null,
      apiError: null,
      anomaly: {
        step: 2,
        delta: payload.consumption_delta || state.anomaly?.delta || '0.000',
        average: state.anomaly?.average || null,
        limit: state.anomaly?.limit || null,
      },
    }
  }

  if (code === 'READING_BELOW_PREVIOUS') {
    return {
      ...state,
      anomaly: null,
      fieldError: 'READING_BELOW_PREVIOUS',
      apiError: error,
    }
  }

  if (code === 'REASON_REQUIRED') {
    return {
      ...state,
      anomaly: null,
      fieldError: 'REASON_REQUIRED',
      apiError: error,
    }
  }

  return {
    ...state,
    anomaly: null,
    fieldError: code || 'UNKNOWN_ERROR',
    apiError: error,
  }
}

/**
 * Reset anomaly flow when the user edits the input value or cancels the modal.
 * @param {AnomalyFlowState} state
 * @returns {AnomalyFlowState}
 */
export function resetAnomalyOnEdit(state) {
  return {
    ...state,
    anomaly: null,
    pendingValue: null,
    fieldError: null,
    apiError: null,
  }
}
