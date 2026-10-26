import test from 'node:test'
import assert from 'node:assert/strict'
import {
  normalizeMeterInput,
  toScaledBigInt,
  fromScaledBigInt,
  subtractMeterValues,
  sumMeterValues,
  compareMeterValues,
  formatSignedDelta,
} from './meterValue.js'

test('normalizeMeterInput normalizes comma, spaces, and validates pattern', () => {
  assert.equal(normalizeMeterInput('1254.100'), '1254.100')
  assert.equal(normalizeMeterInput(' 128,5 '), '128.5')
  assert.equal(normalizeMeterInput('86.200'), '86.200')
  assert.equal(normalizeMeterInput('0'), '0')
  assert.equal(normalizeMeterInput('1254.1234'), null) // more than 3 decimals rejected
  assert.equal(normalizeMeterInput('-5.000'), null) // negative input rejected
  assert.equal(normalizeMeterInput('1,254.000'), null) // thousands separator rejected
  assert.equal(normalizeMeterInput('abc'), null)
  assert.equal(normalizeMeterInput(''), null)
})

test('scaled BigInt arithmetic avoids floating point errors', () => {
  assert.equal(toScaledBigInt('1254.100'), 1254100n)
  assert.equal(fromScaledBigInt(1254100n), '1254.100')
  assert.equal(subtractMeterValues('86.200', '84.100'), '2.100')
  assert.equal(subtractMeterValues('0.3', '0.1'), '0.200')
  assert.equal(sumMeterValues(['2.100', '1.400', '2.100', '1.350']), '6.950')
  assert.equal(compareMeterValues('84.000', '84.100'), -1)
  assert.equal(compareMeterValues('84.100', '84.100'), 0)
  assert.equal(compareMeterValues('86.200', '84.100'), 1)
  assert.equal(formatSignedDelta('2.100'), '+2.100')
  assert.equal(formatSignedDelta('-1.500'), '-1.500')
})
