/**
 * Dashboard button/link interaction tests.
 *
 * Covers:
 * 1. "New Batch" button navigates to /batches/new.
 * 2. "Run Smoke Test" button: initially enabled → disabled with "Running…" text
 *    during pending → success banner with "View batch" link pointing to the
 *    smoke-test batch URL → clicking the link navigates correctly.
 * 3. Clicking an active batch row navigates to /batches/{batch_id}.
 * 4. Clicking a completed batch row navigates to /batches/{batch_id}.
 *
 * Batch IDs from fixtures/batches.json:
 *   running  → bbbbbbbb-0002-0002-0002-000000000002
 *   completed → aaaaaaaa-0001-0001-0001-000000000001  (sorted most-recent first)
 */
import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const RUNNING_BATCH_ID = 'bbbbbbbb-0002-0002-0002-000000000002'
const COMPLETED_BATCH_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const SMOKE_BATCH_ID = 'smoke-test-batch-id-0000000000001'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// 1. New Batch button
// ---------------------------------------------------------------------------

test('"New Batch" button navigates to /batches/new', async ({ page }) => {
  await page.goto('/')
  const newBatchBtn = page.getByRole('button', { name: 'New Batch' })
  await expect(newBatchBtn).toBeVisible()
  await newBatchBtn.click()
  await expect(page).toHaveURL('/batches/new')
})

// ---------------------------------------------------------------------------
// 2. Run Smoke Test button — state transitions + View batch link
// ---------------------------------------------------------------------------

test('"Run Smoke Test" button is enabled before click', async ({ page }) => {
  await page.goto('/')
  const btn = page.getByRole('button', { name: 'Run Smoke Test' })
  await expect(btn).toBeEnabled()
})

test('"Run Smoke Test" button becomes disabled with "Running…" text while pending', async ({ page }) => {
  // Delay the smoke-test response so we can observe the intermediate state.
  await page.route('**/api/smoke-test', async (route) => {
    // Hold the response for 2 seconds then fulfill with the normal mock payload.
    await new Promise((resolve) => setTimeout(resolve, 2000))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        batch_id: SMOKE_BATCH_ID,
        message: 'Smoke test batch created with 1 run.',
        total_runs: 1,
      }),
    })
  })

  await page.goto('/')
  const btn = page.getByRole('button', { name: /Run Smoke Test|Running/ })
  await btn.click()

  // Button should immediately flip to disabled "Running…"
  await expect(page.getByRole('button', { name: 'Running…' })).toBeDisabled()
})

test('success banner appears with "View batch" link after smoke test completes', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Run Smoke Test' }).click()

  // Success banner
  await expect(page.getByText('Smoke test batch created with 1 run.')).toBeVisible()

  // "View batch" link is present
  const viewLink = page.getByRole('link', { name: 'View batch' })
  await expect(viewLink).toBeVisible()
})

test('"View batch" link points to the correct smoke-test batch URL', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Run Smoke Test' }).click()

  const viewLink = page.getByRole('link', { name: 'View batch' })
  await expect(viewLink).toBeVisible()

  // Verify the href resolves to the expected batch path before clicking.
  const href = await viewLink.getAttribute('href')
  expect(href).toContain(`/batches/${SMOKE_BATCH_ID}`)
})

test('clicking "View batch" link navigates to smoke-test batch detail page', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Run Smoke Test' }).click()

  const viewLink = page.getByRole('link', { name: 'View batch' })
  await expect(viewLink).toBeVisible()
  await viewLink.click()

  await expect(page).toHaveURL(`/batches/${SMOKE_BATCH_ID}`)
})

// ---------------------------------------------------------------------------
// 3. Active batch row navigation
// ---------------------------------------------------------------------------

test('clicking an active (running) batch row navigates to /batches/{batch_id}', async ({ page }) => {
  await page.goto('/')
  const activeSection = page.locator('section').filter({ hasText: 'Active Batches' })

  // The running batch ID prefix is "bbbbbbbb"; find its row by the truncated ID text.
  const row = activeSection.locator('tbody tr').filter({ hasText: 'bbbbbbbb' })
  await expect(row).toBeVisible()
  await row.click()

  await expect(page).toHaveURL(`/batches/${RUNNING_BATCH_ID}`)
})

// ---------------------------------------------------------------------------
// 4. Completed batch row navigation
// ---------------------------------------------------------------------------

test('clicking a completed batch row navigates to /batches/{batch_id}', async ({ page }) => {
  await page.goto('/')
  const recentSection = page.locator('section').filter({ hasText: 'Recent Batches' })

  // The fixture sorts completed batches by completed_at descending.
  // aaaaaaaa-0001 (completed 2026-04-17) is most recent, then cccccccc-0003.
  const row = recentSection.locator('tbody tr').filter({ hasText: 'aaaaaaaa' })
  await expect(row).toBeVisible()
  await row.click()

  await expect(page).toHaveURL(`/batches/${COMPLETED_BATCH_ID}`)
})
