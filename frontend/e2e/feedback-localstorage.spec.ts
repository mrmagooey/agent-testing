/**
 * E2E tests for Feedback Trends filter localStorage persistence.
 *
 * Covers the useTrendFilters() hook contract: values written to localStorage
 * survive page reloads, and a fresh page with no localStorage uses the
 * correct defaults (dataset='', limit=10, tool-ext='', since='', until='').
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const LS_DATASET = 'feedback_trend_dataset'
const LS_LIMIT = 'feedback_trend_limit'
const LS_TOOL_EXT = 'feedback_trend_tool_ext'
const LS_SINCE = 'feedback_trend_since'
const LS_UNTIL = 'feedback_trend_until'

/** Shorthand locators scoped to the trends section to avoid strict-mode collisions. */
function trendLocators(page: Parameters<typeof test>[1] extends { page: infer P } ? P : never) {
  const section = page.getByTestId('trends-section')
  return {
    datasetSelect: section.locator('select').nth(0),
    limitSelect: section.locator('select').nth(1),
    toolExtInput: section.locator('input[type="text"]'),
    sinceInput: section.locator('input[type="date"]').nth(0),
    untilInput: section.locator('input[type="date"]').nth(1),
  }
}

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test.describe('Feedback Trends localStorage persistence', () => {
  // ---------------------------------------------------------------------------
  // 1. Clean localStorage: defaults
  // ---------------------------------------------------------------------------
  test('clean localStorage: all 5 filters show defaults (empty / 10)', async ({ page }) => {
    // Navigate once to get to the origin so localStorage.clear() works,
    // then clear and reload so the component mounts with no persisted state.
    await page.goto('/feedback')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/feedback')

    const { datasetSelect, limitSelect, toolExtInput, sinceInput, untilInput } = trendLocators(page)

    await expect(datasetSelect).toHaveValue('')
    await expect(limitSelect).toHaveValue('10')
    await expect(toolExtInput).toHaveValue('')
    await expect(sinceInput).toHaveValue('')
    await expect(untilInput).toHaveValue('')
  })

  // ---------------------------------------------------------------------------
  // 2. Setting dataset persists to localStorage
  // ---------------------------------------------------------------------------
  test('selecting a dataset writes to localStorage immediately', async ({ page }) => {
    await page.goto('/feedback')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/feedback')

    const { datasetSelect } = trendLocators(page)
    await datasetSelect.selectOption('cve-2024-python')

    // The setAndPersistDataset call is synchronous after the React event, so
    // we can read localStorage right after the select settles.
    const stored = await page.evaluate((key) => localStorage.getItem(key), LS_DATASET)
    expect(stored).toBe('cve-2024-python')
  })

  // ---------------------------------------------------------------------------
  // 3. Reload preserves dataset (pre-seed via navigate then set, then reload)
  // ---------------------------------------------------------------------------
  test('dataset survives page reload', async ({ page }) => {
    // Pre-seed via addInitScript so the value is present on the very first mount.
    await page.addInitScript((key) => {
      localStorage.setItem(key, 'cve-2024-js')
    }, LS_DATASET)
    await page.goto('/feedback')

    const { datasetSelect } = trendLocators(page)
    await expect(datasetSelect).toHaveValue('cve-2024-js')
  })

  // ---------------------------------------------------------------------------
  // 4. Setting limit persists; reload preserves it
  // ---------------------------------------------------------------------------
  test('changing limit to 50 persists to localStorage and survives reload', async ({ page }) => {
    await page.goto('/feedback')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/feedback')

    const { limitSelect } = trendLocators(page)
    await limitSelect.selectOption('50')

    // Stored as a string by setAndPersistLimit (String(v))
    const stored = await page.evaluate((key) => localStorage.getItem(key), LS_LIMIT)
    expect(stored).toBe('50')

    await page.reload()
    await expect(trendLocators(page).limitSelect).toHaveValue('50')
  })

  // ---------------------------------------------------------------------------
  // 5. Setting tool-ext text persists; reload preserves it
  // ---------------------------------------------------------------------------
  test('tool-extension filter persists to localStorage and survives reload', async ({ page }) => {
    await page.goto('/feedback')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/feedback')

    const { toolExtInput } = trendLocators(page)
    await toolExtInput.fill('tree_sitter')

    const stored = await page.evaluate((key) => localStorage.getItem(key), LS_TOOL_EXT)
    expect(stored).toBe('tree_sitter')

    await page.reload()
    await expect(trendLocators(page).toolExtInput).toHaveValue('tree_sitter')
  })

  // ---------------------------------------------------------------------------
  // 6. Setting since persists; reload preserves it
  // ---------------------------------------------------------------------------
  test('since date persists to localStorage and survives reload', async ({ page }) => {
    await page.goto('/feedback')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/feedback')

    const { sinceInput } = trendLocators(page)
    await sinceInput.fill('2026-01-15')

    const stored = await page.evaluate((key) => localStorage.getItem(key), LS_SINCE)
    expect(stored).toBe('2026-01-15')

    await page.reload()
    await expect(trendLocators(page).sinceInput).toHaveValue('2026-01-15')
  })

  // ---------------------------------------------------------------------------
  // 7. Setting until persists; reload preserves it
  // ---------------------------------------------------------------------------
  test('until date persists to localStorage and survives reload', async ({ page }) => {
    await page.goto('/feedback')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/feedback')

    const { untilInput } = trendLocators(page)
    await untilInput.fill('2026-04-30')

    const stored = await page.evaluate((key) => localStorage.getItem(key), LS_UNTIL)
    expect(stored).toBe('2026-04-30')

    await page.reload()
    await expect(trendLocators(page).untilInput).toHaveValue('2026-04-30')
  })

  // ---------------------------------------------------------------------------
  // 8. All 5 filters together persist and survive reload
  // ---------------------------------------------------------------------------
  test('all 5 filters persist together and are all restored on reload', async ({ page }) => {
    await page.goto('/feedback')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/feedback')

    const locators = trendLocators(page)
    await locators.datasetSelect.selectOption('injected-java-2024')
    await locators.limitSelect.selectOption('20')
    await locators.toolExtInput.fill('lsp')
    await locators.sinceInput.fill('2026-02-01')
    await locators.untilInput.fill('2026-03-31')

    // Assert all 5 localStorage keys
    const [ds, lim, ext, since, until] = await page.evaluate(
      ([k1, k2, k3, k4, k5]) => [
        localStorage.getItem(k1),
        localStorage.getItem(k2),
        localStorage.getItem(k3),
        localStorage.getItem(k4),
        localStorage.getItem(k5),
      ],
      [LS_DATASET, LS_LIMIT, LS_TOOL_EXT, LS_SINCE, LS_UNTIL]
    )
    expect(ds).toBe('injected-java-2024')
    expect(lim).toBe('20')
    expect(ext).toBe('lsp')
    expect(since).toBe('2026-02-01')
    expect(until).toBe('2026-03-31')

    // Reload and assert all 5 inputs show the persisted values
    await page.reload()
    const r = trendLocators(page)
    await expect(r.datasetSelect).toHaveValue('injected-java-2024')
    await expect(r.limitSelect).toHaveValue('20')
    await expect(r.toolExtInput).toHaveValue('lsp')
    await expect(r.sinceInput).toHaveValue('2026-02-01')
    await expect(r.untilInput).toHaveValue('2026-03-31')
  })

  // ---------------------------------------------------------------------------
  // 9. Pre-existing localStorage values populate inputs on mount (addInitScript)
  // ---------------------------------------------------------------------------
  test('pre-seeded localStorage values are loaded into inputs on first mount', async ({ page }) => {
    // addInitScript runs before the page's own JS, so useState lazy initialiser
    // reads these values before the component ever renders.
    await page.addInitScript(
      ([k1, k2, k3, k4, k5]) => {
        localStorage.setItem(k1, 'cve-2024-js')
        localStorage.setItem(k2, '50')
        localStorage.setItem(k3, 'tree_sitter')
        localStorage.setItem(k4, '2026-01-15')
        localStorage.setItem(k5, '2026-04-30')
      },
      [LS_DATASET, LS_LIMIT, LS_TOOL_EXT, LS_SINCE, LS_UNTIL]
    )
    await page.goto('/feedback')

    const r = trendLocators(page)
    await expect(r.datasetSelect).toHaveValue('cve-2024-js')
    await expect(r.limitSelect).toHaveValue('50')
    await expect(r.toolExtInput).toHaveValue('tree_sitter')
    await expect(r.sinceInput).toHaveValue('2026-01-15')
    await expect(r.untilInput).toHaveValue('2026-04-30')
  })
})
