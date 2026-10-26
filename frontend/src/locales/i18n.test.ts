import test from 'node:test'
import assert from 'node:assert/strict'
import en from './en.js'
import et from './et.js'
import { i18n, setI18nLocale, localizeError, SUPPORTED_LOCALES } from './index.js'

function collectKeys(obj: Record<string, any>, prefix = ''): string[] {
  const keys: string[] = []
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      keys.push(...collectKeys(v as Record<string, any>, path))
    } else {
      keys.push(path)
    }
  }
  return keys.sort()
}

test('en and et dictionaries have identical translation key sets', () => {
  const enKeys = collectKeys(en)
  const etKeys = collectKeys(et)
  assert.deepEqual(enKeys, etKeys)
  assert.ok(enKeys.length > 150, 'Expected comprehensive dictionary key coverage')
})

test('SUPPORTED_LOCALES includes en and et and setI18nLocale switches active language', () => {
  const codes = SUPPORTED_LOCALES.map((l) => l.code)
  assert.deepEqual(codes, ['en', 'et'])

  setI18nLocale('et')
  assert.equal(i18n.global.locale.value, 'et')
  assert.equal(i18n.global.t('nav.dashboard'), 'Töölaud')
  assert.equal(i18n.global.t('dashboard.greeting', { name: 'Kati' }), 'Tere päevast, Kati')

  setI18nLocale('en')
  assert.equal(i18n.global.locale.value, 'en')
  assert.equal(i18n.global.t('nav.dashboard'), 'Dashboard')
  assert.equal(i18n.global.t('dashboard.greeting', { name: 'Kati' }), 'Good afternoon, Kati')
})

test('localizeError translates backend error codes with parameters in both locales', () => {
  const msgEn = localizeError(
    'READING_BELOW_PREVIOUS',
    { previous_value: '84.100' },
    'en',
  )
  assert.equal(msgEn, 'Reading cannot be lower than the previous recorded value (84.100).')

  const msgEt = localizeError(
    'READING_BELOW_PREVIOUS',
    { previous_value: '84.100' },
    'et',
  )
  assert.equal(msgEt, 'Näit ei saa olla väiksem kui eelmine kinnitatud näit (84.100).')
})
