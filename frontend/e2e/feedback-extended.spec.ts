/**
 * Extended Feedback page tests covering gaps from the audit:
 * - "Load Trends" button re-enable when dataset selected
 * - Trends error state
 * - Trends Since / Until date filters sent to API
 * - Trends tool extension filter input sent to API
 * - Trends limit dropdown changes API call param
 * - FP pattern loader error handling
 * - Comparison metric delta badge colors (green positive / red negative)
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const EXPERIMENT_A_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const EXPERIMENT_B_ID = 'cccccccc-0003-0003-0003-000000000003'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// Trends section — Load Trends button state
// ---------------------------------------------------------------------------

test.describe('Trends section', () => {
  test('"Load Trends" button re-enabled when dataset selected after being disabled', async ({ page }) => {
    await page.goto('/feedback')

    const btn = page.getByRole('button', { name: 'Load Trends' })
    // Initially disabled
    await expect(btn).toBeDisabled()

    // Select dataset
    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')

    // Now enabled
    await expect(btn).toBeEnabled()
  })

  test('Trends error state shown when GET /trends fails', async ({ page }) => {
    await page.route('**/api/trends**', (route) => {
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal server error' }),
      })
    })

    await page.goto('/feedback')
    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')
    await page.getByRole('button', { name: 'Load Trends' }).click()

    // Error message should appear
    await expect(page.locator('[data-testid="trends-section"] p.text-red-600, [data-testid="trends-section"] [class*="text-red"]').first()).toBeVisible()
  })

  test('Trends "Since" date input is accepted and sent to API', async ({ page }) => {
    await page.goto('/feedback')

    const requestPromise = page.waitForRequest(
      (req) => req.url().includes('/api/trends') && req.url().includes('since=')
    )

    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')

    // Fill the Since date input
    const sinceInput = page.locator('[data-testid="trends-section"] input[type="date"]').first()
    await sinceInput.fill('2026-01-01')

    await page.getByRole('button', { name: 'Load Trends' }).click()

    const req = await requestPromise
    expect(req.url()).toContain('since=2026-01-01')
  })

  test('Trends "Until" date input is accepted and sent to API', async ({ page }) => {
    await page.goto('/feedback')

    const requestPromise = page.waitForRequest(
      (req) => req.url().includes('/api/trends') && req.url().includes('until=')
    )

    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')

    // Fill the Until date input (second date input in the section)
    const untilInput = page.locator('[data-testid="trends-section"] input[type="date"]').nth(1)
    await untilInput.fill('2026-04-30')

    await page.getByRole('button', { name: 'Load Trends' }).click()

    const req = await requestPromise
    expect(req.url()).toContain('until=2026-04-30')
  })

  test('Trends tool extension filter input value is sent to API', async ({ page }) => {
    await page.goto('/feedback')

    const requestPromise = page.waitForRequest(
      (req) => req.url().includes('/api/trends') && req.url().includes('tool_ext=')
    )

    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')

    // Fill the tool extension filter text input
    const toolExtInput = page.locator('[data-testid="trends-section"] input[type="text"]')
    await toolExtInput.fill('tree_sitter')

    await page.getByRole('button', { name: 'Load Trends' }).click()

    const req = await requestPromise
    expect(req.url()).toContain('tool_ext=tree_sitter')
  })

  test('Trends limit dropdown changes limit param in API call', async ({ page }) => {
    await page.goto('/feedback')

    const requestPromise = page.waitForRequest(
      (req) => req.url().includes('/api/trends') && req.url().includes('limit=')
    )

    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')

    // Change limit dropdown — it's the second <select> in the trends section
    const limitSelect = page.locator('[data-testid="trends-section"] select').nth(1)
    await limitSelect.selectOption('20')

    await page.getByRole('button', { name: 'Load Trends' }).click()

    const req = await requestPromise
    expect(req.url()).toContain('limit=20')
  })
})

// ---------------------------------------------------------------------------
// FP Pattern Browser — error handling
// ---------------------------------------------------------------------------

test.describe('FP Pattern Browser error handling', () => {
  test('error state handled gracefully when /fp-patterns returns 500', async ({ page }) => {
    await page.route('**/api/experiments/*/fp-patterns', (route) => {
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal server error' }),
      })
    })

    await page.goto('/feedback')
    await page.locator('[data-testid="fp-pattern-browser-section"] select').selectOption(EXPERIMENT_A_ID)
    await page.getByRole('button', { name: 'Load Patterns' }).click()

    // On error, surface the backend detail in an alert region. The misleading
    // "No FP patterns found." copy (which means "successful empty result")
    // must be suppressed.
    await expect(
      page.getByRole('alert').filter({ hasText: 'Internal server error' }),
    ).toBeVisible()
    await expect(page.getByText('No FP patterns found.')).not.toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Comparison metric delta badge colors
// ---------------------------------------------------------------------------

test.describe('Comparison metric delta badge colors', () => {
  test('positive delta values rendered in green color', async ({ page }) => {
    await page.goto('/feedback')
    await page.locator('[data-testid="experiment-comparison-section"] select').first().selectOption(EXPERIMENT_A_ID)
    await page.locator('[data-testid="experiment-comparison-section"] select').nth(1).selectOption(EXPERIMENT_B_ID)
    await page.getByRole('button', { name: 'Compare' }).click()

    // The mock returns precision_delta: 0.056 → DeltaCell renders "+0.056" in green
    const positiveCell = page.locator('[class*="text-green"]').filter({ hasText: '+0.056' })
    await expect(positiveCell).toBeVisible()
  })

  test('positive delta values show + prefix', async ({ page }) => {
    await page.goto('/feedback')
    await page.locator('[data-testid="experiment-comparison-section"] select').first().selectOption(EXPERIMENT_A_ID)
    await page.locator('[data-testid="experiment-comparison-section"] select').nth(1).selectOption(EXPERIMENT_B_ID)
    await page.getByRole('button', { name: 'Compare' }).click()

    await expect(page.getByText('+0.056')).toBeVisible()
    await expect(page.getByText('+0.045')).toBeVisible()
  })

  test('negative delta values rendered in red color', async ({ page }) => {
    await page.route('**/api/experiments/compare**', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          metric_deltas: [
            {
              experiment_id: 'gpt-4o__zero_shot__with_tools',
              precision_delta: -0.030,
              recall_delta: -0.015,
              f1_delta: -0.022,
            },
          ],
          fp_patterns: [],
          stability: {},
        }),
      })
    })

    await page.goto('/feedback')
    await page.locator('[data-testid="experiment-comparison-section"] select').first().selectOption(EXPERIMENT_A_ID)
    await page.locator('[data-testid="experiment-comparison-section"] select').nth(1).selectOption(EXPERIMENT_B_ID)
    await page.getByRole('button', { name: 'Compare' }).click()

    // Negative delta → red color class
    const negativeCell = page.locator('[class*="text-red"]').filter({ hasText: /-0\.0/ })
    await expect(negativeCell.first()).toBeVisible()
  })
})
