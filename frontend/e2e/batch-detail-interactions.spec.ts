/**
 * Batch detail interaction tests covering:
 * - Cancel button visibility and modal flow (non-terminal vs terminal batches)
 * - Download button presence on terminal batches
 * - Matrix table row click navigation
 * - Row selection checkboxes, sticky bar, Compare Selected, Clear
 * - Max-2 selection enforcement
 */
import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const COMPLETED_BATCH_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUNNING_BATCH_ID = 'bbbbbbbb-0002-0002-0002-000000000002'

// Run IDs from batch-results.json (served for any batch by mockApi)
const RUN_ID_1 = 'run-001-aaa'
const RUN_ID_2 = 'run-002-bbb'
const RUN_ID_3 = 'run-003-ccc'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// Cancel button / modal — non-terminal (running) batch
// ---------------------------------------------------------------------------

test.describe('Cancel button on running batch', () => {
  test('Cancel button is visible for a running batch', async ({ page }) => {
    await page.goto(`/batches/${RUNNING_BATCH_ID}`)
    await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible()
  })

  test('clicking Cancel opens the confirmation modal with correct heading', async ({ page }) => {
    await page.goto(`/batches/${RUNNING_BATCH_ID}`)
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()
  })

  test('"Keep running" button closes the modal without POSTing cancel', async ({ page }) => {
    await page.goto(`/batches/${RUNNING_BATCH_ID}`)

    // Track any cancel POST requests
    const cancelRequests: string[] = []
    page.on('request', (req) => {
      if (req.url().includes('/cancel') && req.method() === 'POST') {
        cancelRequests.push(req.url())
      }
    })

    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()

    await page.getByRole('button', { name: 'Keep running' }).click()

    // Modal should be gone
    await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).not.toBeVisible()
    // No cancel POST should have been made
    expect(cancelRequests).toHaveLength(0)
  })

  test('modal can be reopened after dismissal via "Keep running"', async ({ page }) => {
    await page.goto(`/batches/${RUNNING_BATCH_ID}`)

    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()
    await page.getByRole('button', { name: 'Keep running' }).click()
    await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).not.toBeVisible()

    // Reopen
    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()
  })

  test('"Stop batch" POSTs to /api/batches/{id}/cancel', async ({ page }) => {
    await page.goto(`/batches/${RUNNING_BATCH_ID}`)

    // Intercept the cancel request
    const cancelPromise = page.waitForRequest(
      (req) =>
        req.url().includes(`/batches/${RUNNING_BATCH_ID}/cancel`) && req.method() === 'POST'
    )

    await page.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()
    await page.getByRole('button', { name: 'Stop batch' }).click()

    const cancelReq = await cancelPromise
    expect(cancelReq.url()).toContain(`/batches/${RUNNING_BATCH_ID}/cancel`)
    expect(cancelReq.method()).toBe('POST')
  })
})

// ---------------------------------------------------------------------------
// Cancel button and Download button — terminal (completed) batch
// ---------------------------------------------------------------------------

test.describe('Cancel and Download on completed batch', () => {
  test('Cancel button is NOT present for a completed batch', async ({ page }) => {
    await page.goto(`/batches/${COMPLETED_BATCH_ID}`)
    await expect(page.getByRole('button', { name: 'Cancel' })).not.toBeVisible()
  })

  test('Download button is present for a completed batch', async ({ page }) => {
    await page.goto(`/batches/${COMPLETED_BATCH_ID}`)
    // DownloadButton renders a <button> with label "Download Reports" (default)
    await expect(page.getByRole('button', { name: 'Download Reports' })).toBeVisible()
  })

  test('Download button click triggers a download with the expected filename', async ({
    page,
  }) => {
    await page.route(
      `**/api/batches/${COMPLETED_BATCH_ID}/results/download`,
      (route) =>
        route.fulfill({
          status: 200,
          headers: { 'content-disposition': `attachment; filename=batch-${COMPLETED_BATCH_ID}-reports.zip` },
          contentType: 'application/zip',
          body: 'zip-bytes',
        })
    )

    await page.goto(`/batches/${COMPLETED_BATCH_ID}`)

    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download Reports' }).click()
    const download = await downloadPromise

    expect(download.suggestedFilename()).toBe(`batch-${COMPLETED_BATCH_ID}-reports.zip`)
  })
})

// ---------------------------------------------------------------------------
// Matrix table row click — navigate to run detail
// ---------------------------------------------------------------------------

test.describe('MatrixTable row click navigation', () => {
  test('clicking a run row navigates to /batches/{id}/runs/{runId}', async ({ page }) => {
    await page.goto(`/batches/${COMPLETED_BATCH_ID}`)

    // Wait for the matrix to render
    await expect(page.getByRole('heading', { name: 'Comparative Matrix' })).toBeVisible()

    const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })

    // Click a data cell (model name) — avoids the checkbox and expand button
    const runCell = matrixSection.locator('td.font-mono').filter({ hasText: 'gpt-4o' }).first()
    await runCell.click()

    await expect(page).toHaveURL(/\/batches\/[^/]+\/runs\//)
  })

  test('navigated run URL contains both the batch id and a run id segment', async ({ page }) => {
    await page.goto(`/batches/${COMPLETED_BATCH_ID}`)
    await expect(page.getByRole('heading', { name: 'Comparative Matrix' })).toBeVisible()

    const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })
    const runCell = matrixSection.locator('td.font-mono').filter({ hasText: 'gpt-4o' }).first()
    await runCell.click()

    await expect(page).toHaveURL(
      new RegExp(`/batches/${COMPLETED_BATCH_ID}/runs/[^/]+`)
    )
  })
})

// ---------------------------------------------------------------------------
// Row selection checkboxes and sticky action bar
// ---------------------------------------------------------------------------

test.describe('Row selection and sticky bar', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`/batches/${COMPLETED_BATCH_ID}`)
    await expect(page.getByRole('heading', { name: 'Comparative Matrix' })).toBeVisible()
  })

  test('selecting 1 run shows "1 run selected" in the sticky bar', async ({ page }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')

    await checkboxes.nth(0).click()

    // Sticky bar at bottom of page
    const stickyBar = page.locator('div.fixed.bottom-0')
    await expect(stickyBar).toBeVisible()
    await expect(stickyBar.getByText('1 run selected')).toBeVisible()
  })

  test('"Compare Selected" is NOT shown with only 1 run selected', async ({ page }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')

    await checkboxes.nth(0).click()

    await expect(page.getByRole('button', { name: 'Compare Selected' })).not.toBeVisible()
  })

  test('selecting 2 runs shows "2 runs selected" and "Compare Selected" button', async ({
    page,
  }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')

    await checkboxes.nth(0).click()
    await checkboxes.nth(1).click()

    const stickyBar = page.locator('div.fixed.bottom-0')
    await expect(stickyBar.getByText('2 runs selected')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Compare Selected' })).toBeVisible()
  })

  test('"Compare Selected" navigates to /batches/{id}/compare?a=…&b=…', async ({ page }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')

    await checkboxes.nth(0).click()
    await checkboxes.nth(1).click()

    await page.getByRole('button', { name: 'Compare Selected' }).click()

    await expect(page).toHaveURL(
      new RegExp(`/batches/${COMPLETED_BATCH_ID}/compare\\?a=[^&]+&b=.+`)
    )
  })

  test('"Compare Selected" URL contains the two selected run IDs as query params', async ({
    page,
  }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')

    // Click the first two checkboxes (runs are sorted by f1 desc by default:
    // run-003-ccc 0.855, run-001-aaa 0.776, run-002-bbb 0.726)
    await checkboxes.nth(0).click()
    await checkboxes.nth(1).click()

    await page.getByRole('button', { name: 'Compare Selected' }).click()

    const url = page.url()
    // URL must contain a= and b= params pointing to run IDs
    expect(url).toMatch(/[?&]a=[^&]+/)
    expect(url).toMatch(/[?&]b=[^&]+/)
  })

  test('sticky "Clear" button deselects all and hides the sticky bar', async ({ page }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')

    await checkboxes.nth(0).click()
    await checkboxes.nth(1).click()

    const stickyBar = page.locator('div.fixed.bottom-0')
    await expect(stickyBar).toBeVisible()

    await stickyBar.getByRole('button', { name: 'Clear' }).click()

    // Sticky bar should disappear (no selected runs)
    await expect(stickyBar).not.toBeVisible()
  })

  test('sticky "Clear" button deselects after 1 selection too', async ({ page }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')

    await checkboxes.nth(0).click()

    const stickyBar = page.locator('div.fixed.bottom-0')
    await expect(stickyBar).toBeVisible()

    await stickyBar.getByRole('button', { name: 'Clear' }).click()

    await expect(stickyBar).not.toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Max-2 selection enforcement
// ---------------------------------------------------------------------------

test.describe('Max-2 selection enforcement', () => {
  test('checking a 3rd row evicts the first selection (selection stays at 2)', async ({
    page,
  }) => {
    await page.goto(`/batches/${COMPLETED_BATCH_ID}`)
    await expect(page.getByRole('heading', { name: 'Comparative Matrix' })).toBeVisible()

    const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')

    const total = await checkboxes.count()
    // Need at least 3 runs to test this
    expect(total).toBeGreaterThanOrEqual(3)

    await checkboxes.nth(0).click()
    await checkboxes.nth(1).click()

    // Verify 2 are selected
    const stickyBar = page.locator('div.fixed.bottom-0')
    await expect(stickyBar.getByText('2 runs selected')).toBeVisible()

    // Click the 3rd checkbox — MatrixTable evicts [0] and selects [1, 2]
    await checkboxes.nth(2).click()

    // Selection count must remain at 2 (not 3)
    await expect(stickyBar.getByText('2 runs selected')).toBeVisible()
    await expect(stickyBar.getByText('3 runs selected')).not.toBeVisible()

    // "Compare Selected" should still be visible (2 are selected)
    await expect(page.getByRole('button', { name: 'Compare Selected' })).toBeVisible()
  })

  test('after 3rd row click, 1st originally-selected checkbox becomes unchecked', async ({
    page,
  }) => {
    await page.goto(`/batches/${COMPLETED_BATCH_ID}`)
    await expect(page.getByRole('heading', { name: 'Comparative Matrix' })).toBeVisible()

    const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')

    const total = await checkboxes.count()
    expect(total).toBeGreaterThanOrEqual(3)

    // Select runs 0 and 1
    await checkboxes.nth(0).click()
    await checkboxes.nth(1).click()

    // Verify run 0 is checked
    await expect(checkboxes.nth(0)).toBeChecked()

    // Click run 2 — evicts run 0
    await checkboxes.nth(2).click()

    // Run 0 should now be unchecked (evicted by the sliding-window logic)
    await expect(checkboxes.nth(0)).not.toBeChecked()

    // Run 1 and run 2 should be checked
    await expect(checkboxes.nth(1)).toBeChecked()
    await expect(checkboxes.nth(2)).toBeChecked()
  })
})
