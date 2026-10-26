import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  acknowledgeEmergency,
  getActiveEmergencies,
  resolveEmergency,
} from '@/api/endpoints/communication.js'
import { useSessionStore } from '@/stores/session.js'

const POLL_INTERVAL_MS = 45000

export function useEmergencies() {
  const session = useSessionStore()
  const emergencies = ref([])
  const loading = ref(false)
  let timerId = null

  const unacknowledgedEmergencies = computed(() =>
    emergencies.value.filter((item) => item.is_active && item.acknowledged_at === null),
  )

  const activeEmergencies = computed(() =>
    emergencies.value.filter((item) => item.is_active),
  )

  async function refresh() {
    if (!session.actingUserId) {
      emergencies.value = []
      return
    }
    loading.value = true
    try {
      const list = await getActiveEmergencies()
      emergencies.value = Array.isArray(list) ? list : []
    } catch {
      /* keep previous or empty on transient poll error */
    } finally {
      loading.value = false
    }
  }

  async function acknowledge(postId) {
    const res = await acknowledgeEmergency(postId)
    emergencies.value = emergencies.value.map((e) =>
      e.id === postId ? { ...e, acknowledged_at: res.acknowledged_at } : e,
    )
    return res
  }

  async function resolve(postId) {
    const res = await resolveEmergency(postId)
    emergencies.value = emergencies.value.filter((e) => e.id !== postId)
    return res
  }

  function handleWindowFocus() {
    refresh()
  }

  onMounted(() => {
    refresh()
    timerId = window.setInterval(refresh, POLL_INTERVAL_MS)
    window.addEventListener('focus', handleWindowFocus)
  })

  onUnmounted(() => {
    if (timerId !== null) {
      clearInterval(timerId)
      timerId = null
    }
    window.removeEventListener('focus', handleWindowFocus)
  })

  watch(
    () => session.actingUserId,
    () => {
      emergencies.value = []
      refresh()
    },
  )

  return {
    emergencies,
    activeEmergencies,
    unacknowledgedEmergencies,
    loading,
    refresh,
    acknowledge,
    resolve,
  }
}
