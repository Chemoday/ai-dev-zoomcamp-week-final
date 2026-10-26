import { ref } from 'vue'

/**
 * Reusable composable wrapping an async API request with data / loading / error refs.
 * @template T
 * @param {(...args: any[]) => Promise<T>} fetcher
 * @param {T | null} [initialValue]
 */
export function useApiResource(fetcher, initialValue = null) {
  const data = ref(initialValue)
  const loading = ref(false)
  const error = ref(null)

  async function execute(...args) {
    loading.value = true
    error.value = null
    try {
      const result = await fetcher(...args)
      data.value = result
      return result
    } catch (err) {
      error.value = err
      throw err
    } finally {
      loading.value = false
    }
  }

  function reset() {
    data.value = initialValue
    loading.value = false
    error.value = null
  }

  return {
    data,
    loading,
    error,
    execute,
    reset,
  }
}
