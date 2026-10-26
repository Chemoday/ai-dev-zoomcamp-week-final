import test from 'node:test'
import assert from 'node:assert/strict'
import {
  applyTheme,
  DEFAULT_THEME_ID,
  getCycledTokens,
  getRotatedSwatches,
  getThemePreset,
  normalizeCycleIndex,
  SWATCH_CYCLE_COUNT,
  THEME_CATEGORIES,
  THEME_CYCLE_MODES,
  THEME_PRESETS,
} from './theme.js'

test('provides exactly 40 curated color palette presets across 6 categories', () => {
  assert.equal(THEME_PRESETS.length, 40)
  assert.ok(THEME_CATEGORIES.length >= 7)

  const ids = new Set(THEME_PRESETS.map((p) => p.id))
  assert.equal(ids.size, 40)

  for (const preset of THEME_PRESETS) {
    assert.equal(preset.swatches.length, 4)
    assert.match(preset.tokens['--color-surface'], /^#[0-9a-fA-F]{6}$/)
    assert.match(preset.tokens['--color-primary'], /^#[0-9a-fA-F]{6}$/)
    assert.match(preset.tokens['--color-secondary'], /^#[0-9a-fA-F]{6}$/)
    assert.match(preset.tokens['--color-on-surface'], /^#[0-9a-fA-F]{6}$/)
  }
})

test('getThemePreset and applyTheme resolve valid presets and fall back to default', () => {
  const applied = applyTheme('warm-sand', 0)
  assert.equal(applied, 'warm-sand')

  const fallback = getThemePreset('non-existent-id')
  assert.equal(fallback.id, DEFAULT_THEME_ID)
})

test('getRotatedSwatches and getCycledTokens cyclically permute only the 4 colors of a single preset', () => {
  assert.equal(SWATCH_CYCLE_COUNT, 4)
  assert.equal(THEME_CYCLE_MODES.length, 4)
  assert.equal(normalizeCycleIndex(4), 0)
  assert.equal(normalizeCycleIndex(-1), 3)

  const preset = getThemePreset('warm-sand')
  const [c0, c1, c2, c3] = preset.swatches

  assert.deepEqual(getRotatedSwatches(preset, 0), [c0, c1, c2, c3])
  assert.deepEqual(getRotatedSwatches(preset, 1), [c1, c2, c3, c0])
  assert.deepEqual(getRotatedSwatches(preset, 2), [c2, c3, c0, c1])
  assert.deepEqual(getRotatedSwatches(preset, 3), [c3, c0, c1, c2])
  assert.deepEqual(getRotatedSwatches(preset, 4), [c0, c1, c2, c3])

  const tokens1 = getCycledTokens(preset, 1)
  assert.equal(tokens1['--color-surface'], c1)
  assert.equal(tokens1['--color-surface-container-lowest'], c2)
  assert.equal(tokens1['--color-primary'], c3)
  assert.equal(tokens1['--color-secondary'], c0)

  const tokens2 = getCycledTokens(preset, 2)
  assert.equal(tokens2['--color-surface'], c2)
  assert.equal(tokens2['--color-surface-container-lowest'], c3)
  assert.equal(tokens2['--color-primary'], c0)
  assert.equal(tokens2['--color-secondary'], c1)
})
