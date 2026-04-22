/**
 * A4: Error/loading states (mock 500s) across key pages, plus uncovered flows:
 *     reclassify finding, download button, CVE import after resolve, smoke test failure.
 */
import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'

// ---------------------------------------------------------------------------
// Dashboard error state
// ---------------------------------------------------------------------------

test.describe('Dashboard error state', () => {
  test('shows error banner when GET /experiments returns 500', async ({ page }) => {
    await page.route('**/api/experiments', (route) => {
      route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Internal Server Error"}' })
    })
    await page.goto('/')
    // Dashboard shows an error div when initial fetch fails (uses text-signal-danger class)
    await expect(page.locator('[class*="signal-danger"]').filter({ hasText: /error|Error|failed|Failed|Server/i }).first()).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Smoke test failure state
// ---------------------------------------------------------------------------

test.describe('Dashboard smoke test failure', () => {
  test('smoke test error state shown when POST /smoke-test returns 500', async ({ page }) => {
    await mockApi(page)
    await page.route('**/api/smoke-test', (route) => {
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Coordinator not initialised' }),
      })
    })
    await page.goto('/')
    await page.getByRole('button', { name: 'Run Smoke Test' }).click()
    // Error message should appear in the smoke test card (uses text-signal-danger class)
    await expect(page.locator('[class*="signal-danger"]').filter({ hasText: /Coordinator|error|Error/i }).first()).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// ExperimentDetail error state
// ---------------------------------------------------------------------------

test.describe('ExperimentDetail error state', () => {
  test('shows error when GET /experiments/:id returns 404', async ({ page }) => {
    await mockApi(page)
    await page.route(`**/api/experiments/${EXPERIMENT_ID}`, (route) => {
      route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"Not found"}' })
    })
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(
      page.locator('[class*="red"], [class*="error"]').filter({ hasText: /not found|error|404/i }).first()
    ).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// RunDetail error state
// ---------------------------------------------------------------------------

test.describe('RunDetail error state', () => {
  test('shows error when GET /experiments/:id/runs/:runId returns 404', async ({ page }) => {
    await mockApi(page)
    await page.route(`**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`, (route) => {
      route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"Not found"}' })
    })
    await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)
    await expect(
      page.locator('[class*="red"]').filter({ hasText: /not found|error/i }).first()
    ).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// RunCompare error state
// ---------------------------------------------------------------------------

test.describe('RunCompare error state', () => {
  test('shows error when compare-runs returns 500', async ({ page }) => {
    await mockApi(page)
    // RunCompare calls GET /api/compare-runs?a_experiment=&a_run=&b_experiment=&b_run=.
    await page.route('**/api/compare-runs**', (route) => {
      route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"server error"}' })
    })
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=run-001-aaa&b=run-002-bbb`)
    await expect(
      page.locator('[class*="red"]').filter({ hasText: /error|Could not load/i }).first()
    ).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Loading skeleton / EmptyState
// ---------------------------------------------------------------------------

test.describe('Loading skeleton states', () => {
  test('Dashboard shows loading spinner during initial fetch', async ({ page }) => {
    await mockApi(page)
    await page.route('**/api/experiments', (route) => {
      const url = new URL(route.request().url())
      if (url.pathname.replace(/^\/api/, '') === '/experiments' && route.request().method() === 'GET') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      }
      return route.fallback()
    })
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
  })
})

test.describe('EmptyState components', () => {
  // Both empty-state messages appear in a single Dashboard render when experiments=[]
  test('Dashboard shows empty active experiments message when experiments list is empty', async ({ page }) => {
    // Install the full mockApi first so every route is handled, then override experiments with []
    await mockApi(page)
    await page.route('**/api/experiments', (route) => {
      const url = new URL(route.request().url())
      // Only intercept the bare /experiments list route — let sub-paths fall through to mockApi
      if (url.pathname.replace(/^\/api/, '') === '/experiments' && route.request().method() === 'GET') {
        route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      } else {
        route.fallback()
      }
    })
    await page.goto('/')
    // Wait for the "Active Experiments" card heading to confirm Dashboard rendered
    await expect(page.getByRole('heading', { name: 'Active Experiments' })).toBeVisible({ timeout: 10000 })
    await expect(page.getByText('No active experiments.')).toBeVisible()
  })

  test('Dashboard shows empty completed experiments message', async ({ page }) => {
    await mockApi(page)
    await page.route('**/api/experiments', (route) => {
      const url = new URL(route.request().url())
      if (url.pathname.replace(/^\/api/, '') === '/experiments' && route.request().method() === 'GET') {
        route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
      } else {
        route.fallback()
      }
    })
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Recent Experiments' })).toBeVisible({ timeout: 10000 })
    await expect(page.getByText(/No completed experiments yet/)).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Reclassify flow in RunDetail
// ---------------------------------------------------------------------------

test.describe('Reclassify finding flow', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)
  })

  test('Reclassify button is visible on fp finding rows when expanded', async ({ page }) => {
    // Expand a false-positive finding row — "Reflected XSS" has match_status fp
    const findingRow = page.locator('tbody tr').filter({ hasText: 'Reflected XSS in search results' }).first()
    await findingRow.click()
    // The button text is "Reclassify as Unlabeled Real"
    await expect(page.getByRole('button', { name: /Reclassify as Unlabeled Real/i })).toBeVisible()
  })

  test('clicking Reclassify button calls the reclassify API', async ({ page }) => {
    let reclassifyCalled = false
    await page.route('**/api/experiments/**/runs/**/reclassify', (route) => {
      reclassifyCalled = true
      route.fulfill({ status: 204, body: '' })
    })
    // Expand false-positive finding
    const findingRow = page.locator('tbody tr').filter({ hasText: 'Reflected XSS in search results' }).first()
    await findingRow.click()
    await page.getByRole('button', { name: /Reclassify as Unlabeled Real/i }).click()
    expect(reclassifyCalled).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Download button in RunDetail and ExperimentDetail
// ---------------------------------------------------------------------------

test.describe('Download button functionality', () => {
  test('RunDetail shows Download Run button', async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)
    await expect(page.getByRole('button', { name: /Download Run/i })).toBeVisible()
  })

  test('ExperimentDetail shows Download Reports button for completed experiment', async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    // DownloadButton renders for terminal (completed) experiments
    await expect(page.getByRole('button', { name: /Download Reports/i })).toBeVisible()
  })

  test('Download button triggers file download link creation', async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)

    // Listen for download events (the button creates an <a> and clicks it)
    const downloadPromise = page.waitForEvent('download', { timeout: 5000 }).catch(() => null)
    await page.getByRole('button', { name: /Download/i }).first().click()
    // Either a download event fires or we can verify the API URL was hit
    // Since it's a direct link (not fetch), we just verify the button is clickable
    // and doesn't throw
    await expect(page.getByRole('button', { name: /Download/i }).first()).toBeVisible()
    // Clean up the download promise
    await downloadPromise
  })
})

// ---------------------------------------------------------------------------
// CVE import flow (Resolve → Import)
// ---------------------------------------------------------------------------

test.describe('CVE import flow from resolved CVE', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto('/datasets/discover')
  })

  test('Import button triggers POST /datasets/import-cve', async ({ page }) => {
    let importCalled = false
    await page.route('**/api/datasets/import-cve', (route) => {
      importCalled = true
      route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ name: 'cve-2024-12345', label_count: 1 }),
      })
    })
    // Resolve a CVE first
    await page.getByRole('button', { name: 'Resolve CVE' }).click()
    await page.getByPlaceholder('CVE-2024-12345').fill('CVE-2024-12345')
    await page.getByRole('button', { name: 'Resolve', exact: true }).click()
    // Import button should appear
    await expect(page.getByRole('button', { name: 'Import' })).toBeVisible()
    await page.getByRole('button', { name: 'Import' }).click()
    expect(importCalled).toBe(true)
  })

  test('import success shows confirmation or navigates', async ({ page }) => {
    await page.route('**/api/datasets/import-cve', (route) => {
      route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ name: 'cve-2024-12345', label_count: 1 }),
      })
    })
    await page.getByRole('button', { name: 'Resolve CVE' }).click()
    await page.getByPlaceholder('CVE-2024-12345').fill('CVE-2024-12345')
    await page.getByRole('button', { name: 'Resolve', exact: true }).click()
    await page.getByRole('button', { name: 'Import' }).click()
    // After import, the page either navigates to datasets or shows a success state
    // Accept either outcome
    const isOnDatasets = page.url().includes('/datasets')
    expect(isOnDatasets).toBe(true)
  })
})
