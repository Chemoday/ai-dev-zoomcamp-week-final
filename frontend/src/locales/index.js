import { createI18n } from 'vue-i18n'
import en from './en.js'
import et from './et.js'

export const SUPPORTED_LOCALES = [
  { code: 'en', label: 'EN' },
  { code: 'et', label: 'ET' },
]

const LANG_KEY = 'hoa_ui_lang'

export function readStoredLocale() {
  try {
    const saved = localStorage.getItem(LANG_KEY)
    if (saved && SUPPORTED_LOCALES.some((l) => l.code === saved)) {
      return saved
    }
  } catch {
    /* ignore */
  }
  return 'en'
}

const MESSAGES = {
  en,
  et,
}

export const i18n = createI18n({
  legacy: false,
  globalInjection: true,
  locale: readStoredLocale(),
  fallbackLocale: 'en',
  messages: MESSAGES,
})

/**
 * Synchronize vue-i18n locale, localStorage, and document.documentElement.lang.
 * @param {string} newLocale
 * @returns {string}
 */
export function setI18nLocale(newLocale) {
  const valid = SUPPORTED_LOCALES.some((l) => l.code === newLocale) ? newLocale : 'en'
  i18n.global.locale.value = valid
  try {
    localStorage.setItem(LANG_KEY, valid)
  } catch {
    /* ignore */
  }
  if (typeof document !== 'undefined' && document.documentElement) {
    document.documentElement.lang = valid
  }
  return valid
}

/**
 * Localize an error_code from the API without relying on English developer message.
 * @param {string} errorCode
 * @param {Record<string, any>} [payload]
 * @param {string} [lang]
 * @returns {string}
 */
export function localizeError(errorCode, payload = {}, lang) {
  const activeLocale = lang || i18n.global.locale.value || 'en'
  const dict = MESSAGES[activeLocale]?.errors || MESSAGES.en.errors
  const key = dict[errorCode] ? errorCode : 'DEFAULT'

  const previousValue =
    payload?.previous_value || (activeLocale === 'et' ? 'eelmine' : 'previous')
  const allowedStatuses =
    (payload?.allowed_statuses || []).join(', ') ||
    (activeLocale === 'et' ? 'puudub' : 'none')
  const traceSuffix = payload?.trace_id
    ? activeLocale === 'et'
      ? ` Veakood: ${payload.trace_id}`
      : ` Error code: ${payload.trace_id}`
    : ''

  return i18n.global.t(
    `errors.${key}`,
    {
      ...payload,
      previous_value: previousValue,
      allowed_statuses: allowedStatuses,
      trace_suffix: traceSuffix,
    },
    { locale: activeLocale },
  )
}

/**
 * Get dictionary for current language.
 * @param {string} [lang]
 */
export function useTranslations(lang) {
  const activeLocale = lang || i18n.global.locale.value || 'en'
  return MESSAGES[activeLocale] || MESSAGES.en
}

