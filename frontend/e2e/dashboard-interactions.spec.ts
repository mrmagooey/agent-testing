/**
 * Dashboard button/link interaction tests.
 *
 * Covers:
 * 1. "New Experiment" button navigates to /experiments/new.
 * 2. "Run Smoke Test" button: initially enabled → disabled with "Running…" text
 *    during pending → success banner with "View experiment →" link pointing to the
 *    smoke-test experiment URL → clicking the link navigates correctly.
 * 3. Clicking an active experiment row navigates to /experiments/{experiment_id}.
 * 4. Clicking a completed experiment row navigates to /experiments/{experiment_id}.
 *
 * Experiment IDs from fixtures/experiments.json:
 *   running  → bbbbbbbb-0002-0002-0002-000000000002
 *   completed → aaaaaaaa-0001-0001-0001-000000000001  (sorted most-recent first)
 */
import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const RUNNING_EXPERIMENT_ID = 'bbbbbbbb-0002-0002-0002-000000000002'
const COMPLETED_EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const SMOKE_EXPERIMENT_ID = 'smoke-test-experiment-id-0000000000001'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// 1. New Experiment button
// ---------------------------------------------------------------------------

test('"New Experiment" button navigates to /experiments/new', async ({ page }) => {
  await page.goto('/')
  const newExperimentBtn = page.getByRole('button', { name: 'New Experiment' })
  await expect(newExperimentBtn).toBeVisible()
  await newExperimentBtn.click()
  await expect(page).toHaveURL('/experiments/new')
})

// ---------------------------------------------------------------------------
// 2. Run Smoke Test button — state transitions + View experiment link
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
        experiment_id: SMOKE_EXPERIMENT_ID,
        message: 'Smoke test experiment created with 1 run.',
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

test('success banner appears with "View experiment →" link after smoke test completes', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Run Smoke Test' }).click()

  // Success banner
  await expect(page.getByText('Smoke test experiment created with 1 run.')).toBeVisible()

  // "View experiment →" link is present
  const viewLink = page.getByRole('link', { name: 'View experiment →' })
  await expect(viewLink).toBeVisible()
})

test('"View experiment →" link points to the correct smoke-test experiment URL', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Run Smoke Test' }).click()

  const viewLink = page.getByRole('link', { name: 'View experiment →' })
  await expect(viewLink).toBeVisible()

  // Verify the href resolves to the expected experiment path before clicking.
  const href = await viewLink.getAttribute('href')
  expect(href).toContain(`/experiments/${SMOKE_EXPERIMENT_ID}`)
})

test('clicking "View experiment →" link navigates to smoke-test experiment detail page', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Run Smoke Test' }).click()

  const viewLink = page.getByRole('link', { name: 'View experiment →' })
  await expect(viewLink).toBeVisible()
  await viewLink.click()

  await expect(page).toHaveURL(`/experiments/${SMOKE_EXPERIMENT_ID}`)
})

// ---------------------------------------------------------------------------
// 3. Active experiment row navigation
// ---------------------------------------------------------------------------

test('clicking an active (running) experiment row navigates to /experiments/{experiment_id}', async ({ page }) => {
  await page.goto('/')
  const activeSection = page.locator('section').filter({ hasText: 'Active Experiments' })

  // The running experiment ID prefix is "bbbbbbbb"; find its row by the truncated ID text.
  const row = activeSection.locator('tbody tr').filter({ hasText: 'bbbbbbbb' })
  await expect(row).toBeVisible()
  await row.click()

  await expect(page).toHaveURL(`/experiments/${RUNNING_EXPERIMENT_ID}`)
})

// ---------------------------------------------------------------------------
// 4. Completed experiment row navigation
// ---------------------------------------------------------------------------

test('clicking a completed experiment row navigates to /experiments/{experiment_id}', async ({ page }) => {
  await page.goto('/')
  const recentSection = page.locator('section').filter({ hasText: 'Recent Experiments' })

  // The fixture sorts completed experiments by completed_at descending.
  // aaaaaaaa-0001 (completed 2026-04-17) is most recent, then cccccccc-0003.
  const row = recentSection.locator('tbody tr').filter({ hasText: 'aaaaaaaa' })
  await expect(row).toBeVisible()
  await row.click()

  await expect(page).toHaveURL(`/experiments/${COMPLETED_EXPERIMENT_ID}`)
})
