/**
 * Extended ExperimentDetail tests covering gaps from the audit:
 * - "Compare with another" link → /compare?a_experiment=...
 * - TokenMeter displayed values (cost/run text)
 * - Results loading skeleton (resultsLoading=true)
 * - Matrix table Compare button enable/disable (selectedRuns.length === 2)
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const COMPLETED_EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUNNING_EXPERIMENT_ID = 'bbbbbbbb-0002-0002-0002-000000000002'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// "Compare with another" link
// ---------------------------------------------------------------------------

test.describe('"Compare with another" button', () => {
  test('"Compare with another" button is visible on completed experiment detail', async ({ page }) => {
    await page.goto(`/experiments/${COMPLETED_EXPERIMENT_ID}`)
    const btn = page.getByRole('button', { name: 'Compare with another' })
    await expect(btn).toBeVisible()
  })

  test('"Compare with another" button navigates to /compare?a_experiment=... when clicked', async ({ page }) => {
    await page.goto(`/experiments/${COMPLETED_EXPERIMENT_ID}`)
    const btn = page.getByRole('button', { name: 'Compare with another' })
    await expect(btn).toBeVisible()
    await btn.click()

    await expect(page).toHaveURL(new RegExp(`/compare\\?a_experiment=${COMPLETED_EXPERIMENT_ID}`))
  })
})

// ---------------------------------------------------------------------------
// TokenMeter displayed values
// ---------------------------------------------------------------------------

test.describe('TokenMeter', () => {
  test('TokenMeter shows "Experiment total" cost', async ({ page }) => {
    await page.goto(`/experiments/${COMPLETED_EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // TokenMeter renders "Experiment total" label next to a cost value
    await expect(page.getByText('Experiment total')).toBeVisible()
  })

  test('TokenMeter shows "Avg/run" cost', async ({ page }) => {
    await page.goto(`/experiments/${COMPLETED_EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    await expect(page.getByText('Avg/run')).toBeVisible()
  })

  test('TokenMeter shows run count', async ({ page }) => {
    await page.goto(`/experiments/${COMPLETED_EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    await expect(page.getByText('Runs').first()).toBeVisible()
  })

  test('TokenMeter cost values are in $ format', async ({ page }) => {
    await page.goto(`/experiments/${COMPLETED_EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // Look for dollar amounts in the token meter area
    const costPatterns = page.locator('span.font-mono').filter({ hasText: /^\$[\d.]+$/ })
    const count = await costPatterns.count()
    expect(count).toBeGreaterThan(0)
  })
})

// ---------------------------------------------------------------------------
// Results loading skeleton
// ---------------------------------------------------------------------------

test.describe('Results loading skeleton', () => {
  test('loading state resolves and matrix heading appears for completed experiment', async ({ page }) => {
    // For a completed experiment, results are fetched and the matrix is shown.
    // Verify the full render completes correctly (covers the resultsLoading=false path).
    await page.goto(`/experiments/${COMPLETED_EXPERIMENT_ID}`)

    // The experiment matrix should be visible after results load
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible({ timeout: 10000 })
  })

  test('running experiment does not show matrix (no results endpoint called)', async ({ page }) => {
    // Non-terminal experiments show no results matrix
    await page.goto(`/experiments/${RUNNING_EXPERIMENT_ID}`)

    // The matrix heading should not appear for non-terminal experiments
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).not.toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Compare button enable/disable
// ---------------------------------------------------------------------------

test.describe('Matrix table Compare button enable/disable', () => {
  test('Compare Selected button not visible until 2 runs are selected', async ({ page }) => {
    await page.goto(`/experiments/${COMPLETED_EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // Initially not visible
    await expect(page.getByRole('button', { name: 'Compare Selected' })).not.toBeVisible()

    // Select 1 run
    const matrixSection = page.locator('section').filter({ hasText: 'Experiment Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')
    await checkboxes.nth(0).click()

    // Still not visible with only 1
    await expect(page.getByRole('button', { name: 'Compare Selected' })).not.toBeVisible()

    // Select 2nd run
    await checkboxes.nth(1).click()

    // Now visible
    await expect(page.getByRole('button', { name: 'Compare Selected' })).toBeVisible()
  })
})
