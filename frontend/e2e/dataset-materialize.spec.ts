import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const DATASET_NAME = 'cve-2024-python'

// ─── Shared fixture helpers ────────────────────────────────────────────────────

function makeDatasetRow(materializedAt: string | null) {
  return {
    name: DATASET_NAME,
    // 'git' makes the component render the "Git origin" section cleanly
    kind: 'git' as const,
    cve_id: 'CVE-2024-12345',
    origin_url: 'https://github.com/example/cve-repo',
    origin_commit: 'abc123def456',
    origin_ref: null,
    base_dataset: null,
    recipe_json: null,
    label_count: 14,
    file_count: 47,
    size_bytes: 524288,
    created_at: '2026-03-01T00:00:00Z',
    materialized_at: materializedAt,
    languages: ['python'],
    metadata: {},
  }
}

/**
 * Register a per-test GET handler for /api/datasets/<name>.
 * Must be called AFTER mockApi() so LIFO ordering ensures this handler
 * runs first (mockApi's catch-all is the fallback).
 */
async function mockDatasetGet(page: Parameters<typeof mockApi>[0], materializedAt: string | null) {
  await page.route(`**/api/datasets/${DATASET_NAME}`, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(makeDatasetRow(materializedAt)),
    })
  })
}

// ─── Tests ────────────────────────────────────────────────────────────────────

test('shows yellow materialize banner and enabled button when materialized_at is null', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, null)
  await page.goto(`/datasets/${DATASET_NAME}`)

  await expect(page.getByText('This dataset is not currently materialized on this deployment.')).toBeVisible()
  const btn = page.getByTestId('materialize-btn')
  await expect(btn).toBeAttached()
  await expect(btn).toBeEnabled()
  await expect(btn).toContainText('Materialize now')
})

test('shows em dash for Materialized timestamp when materialized_at is null', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, null)
  await page.goto(`/datasets/${DATASET_NAME}`)

  // The "Materialized" label row — value span immediately follows label span
  const materializedRow = page.locator('div').filter({ has: page.getByText('Materialized', { exact: true }) }).last()
  await expect(materializedRow).toContainText('—')
})

test('happy path: click Materialize now fires POST and banner disappears with formatted date', async ({ page }) => {
  let rematerializeCallCount = 0

  await mockApi(page)
  await mockDatasetGet(page, null)

  // Register POST handler after mockApi (LIFO → runs first)
  await page.route(`**/api/datasets/${DATASET_NAME}/rematerialize`, (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    rematerializeCallCount++
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ materialized_at: '2026-04-27T12:00:00Z' }),
    })
  })

  await page.goto(`/datasets/${DATASET_NAME}`)

  await expect(page.getByTestId('materialize-btn')).toBeAttached()
  await page.getByTestId('materialize-btn').click()

  // Banner disappears after success
  await expect(page.getByTestId('materialize-btn')).not.toBeAttached()
  expect(rematerializeCallCount).toBe(1)

  // "Materialized" row now shows a date (contains digits, not em dash)
  const materializedRow = page.locator('div').filter({ has: page.getByText('Materialized', { exact: true }) }).last()
  await expect(materializedRow).not.toContainText('—')
  // Should contain at least one digit from the formatted date
  await expect(materializedRow).toContainText(/\d/)
})

test('in-flight state: button shows Materializing… and is disabled while POST is pending', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, null)

  await page.route(`**/api/datasets/${DATASET_NAME}/rematerialize`, async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    // Delay fulfillment by 1.5 s to observe in-flight state
    await new Promise((resolve) => setTimeout(resolve, 1500))
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ materialized_at: '2026-04-27T12:00:00Z' }),
    })
  })

  await page.goto(`/datasets/${DATASET_NAME}`)

  await page.getByTestId('materialize-btn').click()

  // Immediately after click the button should show the in-flight label and be disabled
  const inflight = page.getByRole('button', { name: 'Materializing…' })
  await expect(inflight).toBeVisible()
  await expect(inflight).toBeDisabled()

  // Wait for it to settle so we don't leave a dangling async timer
  await expect(page.getByTestId('materialize-btn')).not.toBeAttached({ timeout: 5000 })
})

test('error path: 500 response surfaces detail text, banner stays, button re-enables', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, null)

  await page.route(`**/api/datasets/${DATASET_NAME}/rematerialize`, (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    return route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Materialization failed: workspace full' }),
    })
  })

  await page.goto(`/datasets/${DATASET_NAME}`)

  await page.getByTestId('materialize-btn').click()

  // Error element with role="alert" should appear with the detail text
  const errorEl = page.getByTestId('materialize-error')
  await expect(errorEl).toBeVisible()
  await expect(errorEl).toHaveAttribute('role', 'alert')
  await expect(errorEl).toContainText('Materialization failed: workspace full')

  // Banner remains visible — the button is still in the DOM
  await expect(page.getByTestId('materialize-btn')).toBeAttached()

  // Button is re-enabled and shows original text
  await expect(page.getByTestId('materialize-btn')).toBeEnabled()
  await expect(page.getByTestId('materialize-btn')).toContainText('Materialize now')
})

test('generic error path: aborted request shows fallback message', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, null)

  await page.route(`**/api/datasets/${DATASET_NAME}/rematerialize`, (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    return route.abort()
  })

  await page.goto(`/datasets/${DATASET_NAME}`)

  await page.getByTestId('materialize-btn').click()

  const errorEl = page.getByTestId('materialize-error')
  await expect(errorEl).toBeVisible()
  await expect(errorEl).toContainText('Materialization failed. Please try again.')
})

test('banner is NOT shown and Materialized row shows formatted date when materialized_at is set', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, '2026-03-15T09:30:00Z')
  await page.goto(`/datasets/${DATASET_NAME}`)

  // No banner present
  await expect(page.getByTestId('materialize-btn')).not.toBeAttached()
  await expect(page.getByText('This dataset is not currently materialized on this deployment.')).not.toBeVisible()

  // Materialized row shows a formatted date (contains digits, not em dash)
  const materializedRow = page.locator('div').filter({ has: page.getByText('Materialized', { exact: true }) }).last()
  await expect(materializedRow).not.toContainText('—')
  await expect(materializedRow).toContainText(/\d/)
})
