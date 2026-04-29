/**
 * Story 66: MatrixFilterBar adds a Status dimension so users can isolate failed runs.
 *
 * Uses experiment-results-with-failures.json (3 runs: completed + failed + cancelled)
 * which provides the status variety needed to surface the Status filter button.
 *
 * Four tests:
 *   1. Status filter button appears when runs vary in status.
 *   2. Status filter button does NOT appear when all runs share the same status.
 *   3. Selecting "failed" filters the MatrixTable to only the failed run.
 *   4. Status filter persists in the URL as status=failed.
 */
import { test, expect } from '@playwright/test'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { mockApi } from './helpers/mockApi'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'

const failuresFixture = JSON.parse(
  readFileSync(join(__dirname, 'fixtures/experiment-results-with-failures.json'), 'utf-8'),
)

const defaultFixture = JSON.parse(
  readFileSync(join(__dirname, 'fixtures/experiment-results.json'), 'utf-8'),
)

test.describe('MatrixFilterBar status dimension', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    // Override results route with failures fixture (completed + failed + cancelled)
    await page.route(`**/api/experiments/${EXPERIMENT_ID}/results`, async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(failuresFixture),
      })
    })
  })

  test('Status filter button appears when runs vary in status', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    const statusBtn = page.getByRole('button', { name: /status/i })
    await expect(statusBtn).toBeVisible()
  })

  test('Status filter button does NOT appear when all runs share the same status', async ({ page }) => {
    // Override route again with the default fixture (all runs are "completed")
    await page.route(`**/api/experiments/${EXPERIMENT_ID}/results`, async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(defaultFixture),
      })
    })

    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // Status button must be absent — only 1 unique status value → hidden by allDims filter
    const statusBtn = page.getByRole('button', { name: /^status$/i })
    await expect(statusBtn).toHaveCount(0)
  })

  test('Selecting "failed" via the status popover filters MatrixTable to only the failed run', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // Open the Status popover and select "failed"
    await page.getByRole('button', { name: /status/i }).click()
    await page.getByRole('option', { name: /^failed$/i }).click()

    // The failed run's strategy is "few_shot" — it must appear
    await expect(page.locator('tr').filter({ hasText: 'few_shot' }).first()).toBeVisible()

    // The completed run's strategy is "zero_shot" — it must be hidden
    await expect(page.locator('tr').filter({ hasText: 'zero_shot' })).toHaveCount(0)

    // The cancelled run's strategy is "agent" — it must be hidden
    await expect(page.locator('tr').filter({ hasText: 'agent' })).toHaveCount(0)
  })

  test('Status filter persists in URL after applying status=failed', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // Open the Status popover and select "failed"
    await page.getByRole('button', { name: /status/i }).click()
    await page.getByRole('option', { name: /^failed$/i }).click()

    // URL must contain status=failed
    await expect(page).toHaveURL(/[?&]status=failed/)
  })
})
