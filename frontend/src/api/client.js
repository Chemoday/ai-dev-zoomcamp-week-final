import { ref } from 'vue'

const STORAGE_KEY = 'demo_user_id'

function readStoredUser() {
  try {
    return localStorage.getItem(STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

export const activeDemoUserId = ref(readStoredUser())
export const lastTraceId = ref('')

export function setDemoUser(userId) {
  activeDemoUserId.value = userId
  try {
    if (userId) {
      localStorage.setItem(STORAGE_KEY, userId)
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  } catch {
    /* private mode: keep in memory */
  }
}

export class ApiError extends Error {
  constructor(status, body, traceId) {
    super(body?.message || `HTTP ${status}`) // developer text only, never shown to users
    this.status = status
    this.errorCode = body?.error_code || `HTTP_${status}`
    this.payload = body || {} // extra fields, e.g. consumption_delta
    this.fieldErrors = body?.errors || [] // VALIDATION_ERROR: [{ loc, message, type }]
    this.traceId = body?.trace_id || traceId // show it on 500s
  }
}

export async function apiRequest(path, { method = 'GET', body, headers = {}, raw = false } = {}) {
  const response = await fetch(`/api/v1${path}`, {
    method,
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(activeDemoUserId.value ? { 'X-Demo-User-Id': activeDemoUserId.value } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  const traceId = response.headers.get('X-Trace-Id') || ''
  if (traceId) lastTraceId.value = traceId

  if (!response.ok) {
    let errorBody = null
    try {
      errorBody = await response.json()
    } catch {
      /* non-JSON error */
    }
    const error = new ApiError(response.status, errorBody, traceId)
    if (response.status === 401) setDemoUser('') // stale demo id: show the switcher again
    throw error
  }
  if (response.status === 204) return null
  if (raw) return response // e.g. the CSV export (section 6)
  return response.json()
}
