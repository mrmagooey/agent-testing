/**
 * Feedback page — error feedback + stale-data clearing
 *
 * User story:
 *   When a Feedback-page async load fails (Trends, Experiment Comparison,
 *   FP Patterns), the page must (a) clear stale data from a previous
 *   successful load so I don't misread the old chart as fresh, and
 *   (b) surface the error visibly with role="alert" — not silently swallow it.
 *
 * Three sibling fixes covered:
 *   1. Trends: success → failure clears the trend grid
 *   2. Compare: success → failure clears the comparison
 *   3. FP-patterns: backend error renders an alert (was silently swallowed)
 *      and the misleading "No FP patterns found." line is suppressed.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.describe('Feedback page — error UX', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  // -------------------------------------------------------------------------
  // 1. Trends: stale data is cleared when a subsequent load fails
  // -------------------------------------------------------------------------

  test('Trends: failed reload clears the previous chart and shows alert', async ({ page }) => {
    let callCount = 0
    await page.route('**/api/trends*', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      callCount++
      if (callCount === 1) {
        // First call: let mockApi fixture serve the success response
        return route.fallback()
      }
      // Second call: fail
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Trend service unavailable' }),
      })
    })

    await page.goto('/feedback')

    // Successful first load
    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')
    await page.getByRole('button', { name: 'Load Trends' }).click()
    await expect(page.getByTestId('trend-grid')).toBeVisible()

    // Click Load Trends again — backend now fails
    await page.getByRole('button', { name: 'Load Trends' }).click()

    // Stale grid must be gone
    await expect(page.getByTestId('trend-grid')).not.toBeVisible()

    // Alert visible with the backend detail
    const alert = page.getByRole('alert').filter({ hasText: 'Trend service unavailable' })
    await expect(alert).toBeVisible()
  })

  // -------------------------------------------------------------------------
  // 2. Compare: stale comparison is cleared when a subsequent compare fails
  // -------------------------------------------------------------------------

  test('Compare: failed reload clears the previous comparison and shows alert', async ({ page }) => {
    let callCount = 0
    await page.route('**/api/experiments/compare*', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      callCount++
      if (callCount === 1) return route.fallback()
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Compare service crashed' }),
      })
    })

    await page.goto('/feedback')

    // Pick two completed experiments — mockApi default fixtures contain at
    // least one completed; we use the first two <option>s of each select.
    const selects = page.locator('[data-testid="experiment-comparison-section"] select')
    const aSelect = selects.nth(0)
    const bSelect = selects.nth(1)
    // Wait until both selects have at least 2 real <option> elements (placeholder + ≥1 real id)
    await expect.poll(() => aSelect.locator('option').count()).toBeGreaterThanOrEqual(2)
    await expect.poll(() => bSelect.locator('option').count()).toBeGreaterThanOrEqual(2)
    const aOpts = await aSelect.locator('option').all()
    const bOpts = await bSelect.locator('option').all()
    // Skip the placeholder option (value=""), pick two distinct real ids
    const realA = await aOpts[1].getAttribute('value')
    const realB = await bOpts[bOpts.length > 2 ? 2 : 1].getAttribute('value')
    // Need two completed experiments for a real compare. If the fixture only
    // exposes one, skip — same-vs-same wouldn't exercise the merge path.
    if (!realA || !realB || realA === realB) test.skip()
    await aSelect.selectOption(realA!)
    await bSelect.selectOption(realB!)

    // First compare succeeds
    await page.getByRole('button', { name: 'Compare', exact: true }).click()
    await expect(page.getByRole('heading', { name: /metric deltas/i })).toBeVisible()

    // Second compare fails
    await page.getByRole('button', { name: 'Compare', exact: true }).click()

    // Stale comparison must be gone (the heading was inside the comparison block)
    await expect(page.getByRole('heading', { name: /metric deltas/i })).not.toBeVisible()

    // Alert with backend detail
    const alert = page.getByRole('alert').filter({ hasText: 'Compare service crashed' })
    await expect(alert).toBeVisible()
  })

  // -------------------------------------------------------------------------
  // 3. FP-patterns: backend error renders alert, suppresses "no patterns" line
  // -------------------------------------------------------------------------

  test('FP patterns: backend error renders alert (was silently swallowed)', async ({ page }) => {
    await page.route('**/api/experiments/*/fp-patterns', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'FP indexer offline' }),
      })
    })

    await page.goto('/feedback')

    // The FP section's experiment select uses the same completed-experiments
    // list. Find it via the section testid (or last select).
    const fpSelect = page.locator('[data-testid="fp-pattern-browser-section"] select').first()
    await expect(fpSelect).toBeVisible()
    const opts = await fpSelect.locator('option').all()
    const realOpt = await opts[1].getAttribute('value')
    if (!realOpt) test.skip()
    await fpSelect.selectOption(realOpt!)

    await page.getByRole('button', { name: 'Load Patterns' }).click()

    // Alert with the backend detail
    const alert = page.getByRole('alert').filter({ hasText: 'FP indexer offline' })
    await expect(alert).toBeVisible()

    // The misleading "No FP patterns found." line must NOT show — that copy
    // means "loaded successfully, zero results", not "load failed".
    await expect(page.getByText('No FP patterns found.')).not.toBeVisible()
  })
})
