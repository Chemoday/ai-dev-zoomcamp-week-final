const TALLINN_TZ = 'Europe/Tallinn'

/**
 * Format an ISO UTC timestamp string in Europe/Tallinn timezone (e.g. "22 Oct 2026").
 * @param {string | null | undefined} isoString
 * @param {string} locale
 * @returns {string}
 */
export function formatDate(isoString, locale = 'en-GB') {
  if (!isoString) return '—'
  try {
    const date = new Date(isoString)
    if (Number.isNaN(date.getTime())) return String(isoString)
    return new Intl.DateTimeFormat(locale === 'et' ? 'et-EE' : 'en-GB', {
      timeZone: TALLINN_TZ,
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    }).format(date)
  } catch {
    return String(isoString)
  }
}

/**
 * Format an ISO UTC timestamp string with date and time in Europe/Tallinn timezone.
 * @param {string | null | undefined} isoString
 * @param {string} locale
 * @returns {string}
 */
export function formatDateTime(isoString, locale = 'en-GB') {
  if (!isoString) return '—'
  try {
    const date = new Date(isoString)
    if (Number.isNaN(date.getTime())) return String(isoString)
    return new Intl.DateTimeFormat(locale === 'et' ? 'et-EE' : 'en-GB', {
      timeZone: TALLINN_TZ,
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(date)
  } catch {
    return String(isoString)
  }
}

/**
 * Format period code ("2026-10") into readable label ("October 2026" / "Oktoober 2026").
 * @param {string | null | undefined} periodCode
 * @param {string} lang
 * @returns {string}
 */
export function formatPeriodLabel(periodCode, lang = 'en') {
  if (!periodCode || !/^[0-9]{4}-(0[1-9]|1[0-2])$/.test(periodCode)) {
    return periodCode || '—'
  }
  const [year, month] = periodCode.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, 15, 12, 0, 0))
  const formatted = new Intl.DateTimeFormat(lang === 'et' ? 'et-EE' : 'en-US', {
    timeZone: TALLINN_TZ,
    month: 'long',
    year: 'numeric',
  }).format(date)
  return formatted.charAt(0).toUpperCase() + formatted.slice(1)
}
