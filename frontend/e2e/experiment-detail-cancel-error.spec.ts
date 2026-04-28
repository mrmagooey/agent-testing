/**
 * E2E tests for cancel-error feedback in the ExperimentDetail modal.
 *
 * Covers:
 * 1. Backend rejects cancel (500 with detail) — modal stays open and shows error
 * 2. Generic error path (500, no body) — modal stays open with fallback message
 * 3. Successful cancel — modal closes (sanity, since happy-path exists in experiment-cancel.spec.ts)
 */
import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'
import { sequencedMock } from './helpers/sequencedMock'

const RUNNING_ID = 'bbbbbbbb-0002-0002-0002-000000000002'

const runningExperiment = {
  experiment_id: RUNNING_ID,
  status: 'running',
  dataset: 'cve-2024-js',
  created_at: '2026-04-17T10:00:00Z',
  completed_at: null,
  total_runs: 40,
  completed_runs: 15,
  running_runs: 5,
  pending_runs: 20,
  failed_runs: 0,
  total_cost_usd: 4.67,
  spend_cap_usd: 50.0,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Navigate to a running experiment page with the base mock in place and the
 * GET /experiments/:id endpoint returning `runningExperiment` indefinitely.
 */
async function setupRunningPage(page: import('@playwright/test').Page) {
  await mockApi(page)

  await sequencedMock(
    page,
    /\/api\/experiments\/[^/]+$/,
    [{ status: 200, body: runningExperiment }],
    { method: 'GET', afterExhausted: 'last' },
  )

  await page.goto(`/experiments/${RUNNING_ID}`)
  await expect(page.getByText('running').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible()
}

// ---------------------------------------------------------------------------
// Test 1: Backend rejects cancel — modal shows error and stays open
// ---------------------------------------------------------------------------

test('cancel rejected by backend: modal shows error message and stays open', async ({ page }) => {
  await setupRunningPage(page)

  // Override the cancel POST to return 500 with a detail message
  await page.route(`**/api/experiments/${RUNNING_ID}/cancel*`, async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Cancel rejected: K8s unreachable' }),
    })
  })

  // Open the confirm modal
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()

  // Confirm the cancel
  await page.getByRole('button', { name: 'Stop experiment' }).click()

  // Error message must appear inside the modal
  await expect(page.getByText('Cancel rejected: K8s unreachable')).toBeVisible()

  // Modal must still be open — heading and action buttons still visible
  await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop experiment' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Keep running' })).toBeVisible()

  // Stop button must be re-enabled (cancelling state cleared)
  await expect(page.getByRole('button', { name: 'Stop experiment' })).not.toBeDisabled()
})

// ---------------------------------------------------------------------------
// Test 2: Generic error path — fallback message when no detail
// ---------------------------------------------------------------------------

test('cancel fails with no detail: modal stays open with a fallback error message', async ({ page }) => {
  await setupRunningPage(page)

  // Return 500 with an empty body (no JSON, no detail)
  await page.route(`**/api/experiments/${RUNNING_ID}/cancel*`, async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: '{}',
    })
  })

  // Open and confirm
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()
  await page.getByRole('button', { name: 'Stop experiment' }).click()

  // Some error text must appear — the exact string depends on the ApiError fallback
  // Use the ARIA role so screen readers announce it and the selector is stable
  const errorBlock = page.getByRole('alert')
  await expect(errorBlock).toBeVisible()

  // Modal must still be open
  await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop experiment' })).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 3: Successful cancel — modal closes
// ---------------------------------------------------------------------------

test('successful cancel: modal closes after confirmation', async ({ page }) => {
  await setupRunningPage(page)

  // Override cancel POST to succeed
  await page.route(`**/api/experiments/${RUNNING_ID}/cancel*`, async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ cancelled_jobs: 1 }),
    })
  })

  // Open and confirm
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()
  await page.getByRole('button', { name: 'Stop experiment' }).click()

  // Modal must disappear
  await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 4: Dismiss after error — error cleared on re-open
// ---------------------------------------------------------------------------

test('dismissing modal after error clears the error on re-open', async ({ page }) => {
  await setupRunningPage(page)

  let callCount = 0

  // First call fails, second call succeeds
  await page.route(`**/api/experiments/${RUNNING_ID}/cancel*`, async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback()
      return
    }
    callCount++
    if (callCount === 1) {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Cancel rejected: K8s unreachable' }),
      })
    } else {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ cancelled_jobs: 1 }),
      })
    }
  })

  // First attempt — triggers error
  await page.getByRole('button', { name: 'Cancel' }).click()
  await page.getByRole('button', { name: 'Stop experiment' }).click()
  await expect(page.getByText('Cancel rejected: K8s unreachable')).toBeVisible()

  // Dismiss via "Keep running" — error must be cleared
  await page.getByRole('button', { name: 'Keep running' }).click()
  await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).not.toBeVisible()

  // Re-open — no error text should be present
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()
  await expect(page.getByText('Cancel rejected: K8s unreachable')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 5: Successful cancel triggers immediate refetch — status flips
// ---------------------------------------------------------------------------

test('successful cancel refetches experiment so status flips to cancelled immediately', async ({ page }) => {
  await mockApi(page)

  // Track GETs so the first few are 'running' and only post-cancel returns 'cancelled'.
  // StrictMode dev double-mount fires the initial fetchExperiment twice, so we
  // count requests and switch responses based on whether the cancel POST has
  // already been observed.
  let cancelPosted = false
  await page.route(/\/api\/experiments\/[^/]+$/, async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback()
      return
    }
    const body = cancelPosted
      ? { ...runningExperiment, status: 'cancelled', completed_at: '2026-04-17T11:00:00Z' }
      : runningExperiment
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })
  })

  // POST /cancel succeeds and flips the GET-response branch.
  await page.route(`**/api/experiments/${RUNNING_ID}/cancel*`, async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback()
      return
    }
    cancelPosted = true
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ cancelled_jobs: 1 }),
    })
  })

  await page.goto(`/experiments/${RUNNING_ID}`)
  await expect(page.getByText('running').first()).toBeVisible()

  // Trigger the cancel
  await page.getByRole('button', { name: 'Cancel' }).click()
  await page.getByRole('button', { name: 'Stop experiment' }).click()

  // Status badge must flip to 'cancelled' WITHOUT waiting for the 10s poll cycle.
  // Generous timeout since the refetch is async, but well below 10s.
  await expect(page.getByText('cancelled').first()).toBeVisible({ timeout: 3000 })
  // The 'Cancel' button (which only renders when !isTerminal) must be gone.
  await expect(page.getByRole('button', { name: 'Cancel' })).not.toBeVisible()
})
