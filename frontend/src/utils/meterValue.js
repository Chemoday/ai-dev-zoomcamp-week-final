const METER_VALUE = /^[0-9]{1,9}(\.[0-9]{1,3})?$/
const SIGNED_METER_VALUE = /^-?[0-9]{1,12}(\.[0-9]{1,3})?$/

/**
 * Normalize user input into a valid decimal string for the backend.
 * Replaces comma with dot, strips whitespace, and checks pattern ^[0-9]{1,9}(\.[0-9]{1,3})?$.
 * Never converts to JS Number.
 * @param {string | null | undefined} text
 * @returns {string | null}
 */
export function normalizeMeterInput(text) {
  if (text === null || text === undefined) return null
  const value = String(text).trim().replace(/\s+/g, '').replace(',', '.')
  return METER_VALUE.test(value) ? value : null
}

/**
 * Convert a decimal string (up to 3 decimal places) to scaled BigInt (× 1000).
 * @param {string | null | undefined} decimalStr
 * @returns {bigint | null}
 */
export function toScaledBigInt(decimalStr) {
  if (decimalStr === null || decimalStr === undefined) return null
  const cleaned = String(decimalStr).trim().replace(/\s+/g, '').replace(',', '.')
  if (!SIGNED_METER_VALUE.test(cleaned)) return null
  const isNegative = cleaned.startsWith('-')
  const unsigned = isNegative ? cleaned.slice(1) : cleaned
  const [intPart, fracPart = ''] = unsigned.split('.')
  const paddedFrac = (fracPart + '000').slice(0, 3)
  const scaled = BigInt(intPart) * 1000n + BigInt(paddedFrac)
  return isNegative ? -scaled : scaled
}

/**
 * Format scaled BigInt (× 1000) back into a 3-decimal string ("1254.100").
 * @param {bigint | null} scaled
 * @returns {string}
 */
export function fromScaledBigInt(scaled) {
  if (scaled === null || scaled === undefined) return '0.000'
  const isNegative = scaled < 0n
  const abs = isNegative ? -scaled : scaled
  const intPart = abs / 1000n
  const fracPart = String(abs % 1000n).padStart(3, '0')
  return `${isNegative ? '-' : ''}${intPart.toString()}.${fracPart}`
}

/**
 * Format a valid decimal string to canonical 3-decimal representation ("128.5" -> "128.500").
 * @param {string | null | undefined} text
 * @returns {string | null}
 */
export function toThreeDecimals(text) {
  const scaled = toScaledBigInt(text)
  if (scaled === null) return null
  return fromScaledBigInt(scaled)
}

/**
 * Subtract previous decimal string from current decimal string using scaled BigInt.
 * Returns null if either value is invalid.
 * @param {string} currentStr
 * @param {string} previousStr
 * @returns {string | null}
 */
export function subtractMeterValues(currentStr, previousStr) {
  const curr = toScaledBigInt(currentStr)
  const prev = toScaledBigInt(previousStr)
  if (curr === null || prev === null) return null
  return fromScaledBigInt(curr - prev)
}

/**
 * Sum an array of decimal strings using scaled BigInt.
 * @param {Array<string | null | undefined>} values
 * @returns {string}
 */
export function sumMeterValues(values) {
  let total = 0n
  for (const val of values) {
    const scaled = toScaledBigInt(val)
    if (scaled !== null) {
      total += scaled
    }
  }
  return fromScaledBigInt(total)
}

/**
 * Compare two decimal strings using scaled BigInt.
 * Returns -1 if a < b, 0 if a === b, 1 if a > b, or null if invalid.
 * @param {string} a
 * @param {string} b
 * @returns {-1 | 0 | 1 | null}
 */
export function compareMeterValues(a, b) {
  const sa = toScaledBigInt(a)
  const sb = toScaledBigInt(b)
  if (sa === null || sb === null) return null
  if (sa < sb) return -1
  if (sa > sb) return 1
  return 0
}

/**
 * Format consumption delta with explicit sign ("+2.100").
 * @param {string | null | undefined} deltaStr
 * @returns {string}
 */
export function formatSignedDelta(deltaStr) {
  const scaled = toScaledBigInt(deltaStr)
  if (scaled === null) return '0.000'
  const formatted = fromScaledBigInt(scaled)
  return scaled > 0n ? `+${formatted}` : formatted
}
