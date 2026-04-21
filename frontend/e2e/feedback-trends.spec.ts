/**
 * E2E tests for the Feedback page Trends section.
 *
 * These tests seed 3 experiments with varying F1 for the same (model, strategy),
 * where the last experiment has F1 significantly below the trailing median
 * (regression fixture), then verify:
 *   - the sparkline renders
 *   - the regression badge appears when latest F1 is below trailing median
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test.describe('Feedback page — Trends section', () => {
  test('shows Trends section heading', async ({ page }) => {
    await page.goto('/feedback')
    await expect(page.getByRole('heading', { name: 'Trends' })).toBeVisible()
  })

  test('Load Trends button is disabled until dataset is selected', async ({ page }) => {
    await page.goto('/feedback')
    const btn = page.getByRole('button', { name: 'Load Trends' })
    await expect(btn).toBeDisabled()
  })

  test('Load Trends button is enabled after selecting a dataset', async ({ page }) => {
    await page.goto('/feedback')
    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')
    const btn = page.getByRole('button', { name: 'Load Trends' })
    await expect(btn).toBeEnabled()
  })

  test('loads and shows trend grid after clicking Load Trends', async ({ page }) => {
    await page.goto('/feedback')
    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')
    await page.getByRole('button', { name: 'Load Trends' }).click()

    // The trend grid table should appear
    await expect(page.getByTestId('trend-grid')).toBeVisible()
  })

  test('shows regression badge for series with F1 drop', async ({ page }) => {
    await page.goto('/feedback')
    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')
    await page.getByRole('button', { name: 'Load Trends' }).click()

    // The fixture seeds gpt-4o with a regression (latest=0.74, median≈0.855 → delta≈-0.12)
    await expect(page.getByLabel('Regression detected')).toBeVisible()
  })

  test('regression row has red background tint', async ({ page }) => {
    await page.goto('/feedback')
    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')
    await page.getByRole('button', { name: 'Load Trends' }).click()

    await expect(page.getByTestId('trend-grid')).toBeVisible()

    // Find the row containing "gpt-4o" which is the regression row in the fixture
    const rows = page.locator('[data-testid="trend-grid"] tbody tr')
    const regressionRow = rows.filter({ hasText: 'gpt-4o' })
    await expect(regressionRow).toHaveClass(/bg-red/)
  })

  test('non-regression row does not have red background', async ({ page }) => {
    await page.goto('/feedback')
    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')
    await page.getByRole('button', { name: 'Load Trends' }).click()

    await expect(page.getByTestId('trend-grid')).toBeVisible()

    // claude-3-sonnet row should NOT be red
    const rows = page.locator('[data-testid="trend-grid"] tbody tr')
    const normalRow = rows.filter({ hasText: 'claude-3-sonnet' })
    await expect(normalRow).not.toHaveClass(/bg-red/)
  })

  test('shows sparkline in each row', async ({ page }) => {
    await page.goto('/feedback')
    await page.selectOption('[data-testid="trends-section"] select', 'cve-2024-python')
    await page.getByRole('button', { name: 'Load Trends' }).click()

    await expect(page.getByTestId('trend-grid')).toBeVisible()
    // At least one recharts svg should be present per row
    const svgs = page.locator('[data-testid="trend-grid"] svg')
    await expect(svgs.first()).toBeVisible()
  })

  test('existing A-vs-B section remains visible below Trends', async ({ page }) => {
    await page.goto('/feedback')
    await expect(page.getByRole('heading', { name: 'Experiment Comparison' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'FP Pattern Browser' })).toBeVisible()
  })

  test('trends section appears above Experiment Comparison', async ({ page }) => {
    await page.goto('/feedback')
    const trendsHeading = page.getByRole('heading', { name: 'Trends' })
    const compareHeading = page.getByRole('heading', { name: 'Experiment Comparison' })

    const trendsBox = await trendsHeading.boundingBox()
    const compareBox = await compareHeading.boundingBox()

    expect(trendsBox?.y).toBeLessThan(compareBox?.y ?? Infinity)
  })
})
