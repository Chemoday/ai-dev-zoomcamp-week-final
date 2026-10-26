import test from 'node:test'
import assert from 'node:assert/strict'
import {
  createAnomalyFlowState,
  prepareReadingSubmission,
  transitionOnReadingError,
  resetAnomalyOnEdit,
} from './anomalyFlow.js'

test('normal reading starts at confirmation_level 0 with normalized string', () => {
  const initial = createAnomalyFlowState()
  const prep = prepareReadingSubmission(initial, '86,2', 0)
  assert.equal(prep.ok, true)
  assert.equal(prep.value, '86.2')
  assert.equal(prep.confirmationLevel, 0)
  assert.equal(prep.nextState.pendingValue, '86.2')
})

test('anomalous value steps through popup 1 -> level 1 -> popup 2 -> level 2', () => {
  let state = createAnomalyFlowState()

  // Step 0: initial submission at level 0
  const prep0 = prepareReadingSubmission(state, '145.000', 0)
  assert.equal(prep0.ok, true)
  assert.equal(prep0.confirmationLevel, 0)
  state = prep0.nextState

  // Backend responds with 409 ANOMALY_CONFIRMATION_STEP_1
  state = transitionOnReadingError(state, {
    errorCode: 'ANOMALY_CONFIRMATION_STEP_1',
    payload: {
      consumption_delta: '60.900',
      average_delta: '2.100',
      max_reasonable_delta: '10.000',
    },
  })
  assert.deepEqual(state.anomaly, {
    step: 1,
    delta: '60.900',
    average: '2.100',
    limit: '10.000',
  })

  // User confirms Popup 1 -> submits at level 1 with exact same pendingValue
  const prep1 = prepareReadingSubmission(state, '145.000', 1)
  assert.equal(prep1.ok, true)
  assert.equal(prep1.confirmationLevel, 1)
  assert.equal(prep1.value, '145.000')
  state = prep1.nextState

  // Backend responds with 409 ANOMALY_CONFIRMATION_STEP_2
  state = transitionOnReadingError(state, {
    errorCode: 'ANOMALY_CONFIRMATION_STEP_2',
    payload: {
      consumption_delta: '60.900',
    },
  })
  assert.equal(state.anomaly?.step, 2)
  assert.equal(state.anomaly?.delta, '60.900')

  // User confirms Popup 2 -> submits at level 2
  const prep2 = prepareReadingSubmission(state, '145.000', 2)
  assert.equal(prep2.ok, true)
  assert.equal(prep2.confirmationLevel, 2)
  assert.equal(prep2.value, '145.000')
})

test('editing input while popup is open cancels anomaly popup and restarts at level 0', () => {
  let state = createAnomalyFlowState()
  const prep0 = prepareReadingSubmission(state, '145.000', 0)
  state = transitionOnReadingError(prep0.nextState, {
    errorCode: 'ANOMALY_CONFIRMATION_STEP_1',
    payload: { consumption_delta: '60.900' },
  })
  assert.equal(state.anomaly?.step, 1)

  // User edits input
  state = resetAnomalyOnEdit(state)
  assert.equal(state.anomaly, null)
  assert.equal(state.pendingValue, null)

  // Next submit starts at level 0 even if 1 was passed accidentally
  const nextPrep = prepareReadingSubmission(state, '86.500', 1)
  assert.equal(nextPrep.confirmationLevel, 0)
  assert.equal(nextPrep.value, '86.500')
})

test('READING_BELOW_PREVIOUS shows field error and opens no popup', () => {
  let state = createAnomalyFlowState()
  const prep = prepareReadingSubmission(state, '80.000', 0)
  state = transitionOnReadingError(prep.nextState, {
    errorCode: 'READING_BELOW_PREVIOUS',
    payload: { previous_value: '84.100' },
  })
  assert.equal(state.anomaly, null)
  assert.equal(state.fieldError, 'READING_BELOW_PREVIOUS')
})
