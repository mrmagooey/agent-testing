/**
 * E2E tests for the Cancel experiment UI flow.
 *
 * Covers:
 * 1. Happy path: running → (click Cancel + confirm) → cancelling → cancelled
 * 2. Negative: completed experiment has no accessible Cancel button
 * 3. Modal dismiss: cancel modal appears, "Keep Running" closes it without POSTing
 *
 * Uses sequencedMock to script multi-step GET /experiments/:id responses so the
 * UI state-transition polling (every 10 s) advances through running → cancelling
 * → cancelled without touching real time.
 */
import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'
import { sequencedMock } from './helpers/sequencedMock'

const RUNNING_ID = 'bbbbbbbb-0002-0002-0002-000000000002'
const COMPLETED_ID = 'aaaaaaaa-0001-0001-0001-000000000001'

// Base fixture objects for experiment states
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

const cancellingExperiment = {
  ...runningExperiment,
  status: 'cancelling',
  running_runs: 3,
  pending_runs: 0,
}

const cancelledExperiment = {
  ...runningExperiment,
  status: 'cancelled',
  completed_at: '2026-04-17T10:05:00Z',
  running_runs: 0,
  pending_runs: 0,
}

const completedExperiment = {
  experiment_id: COMPLETED_ID,
  status: 'completed',
  dataset: 'cve-2024-python',
  created_at: '2026-04-17T08:00:00Z',
  completed_at: '2026-04-17T09:30:00Z',
  total_runs: 40,
  completed_runs: 40,
  running_runs: 0,
  pending_runs: 0,
  failed_runs: 0,
  total_cost_usd: 12.45,
  spend_cap_usd: 50.0,
}

// ---------------------------------------------------------------------------
// Test 1: Happy path — running → cancel confirmed → cancelling → cancelled
// ---------------------------------------------------------------------------

test('happy path: cancel flow transitions UI through cancelling to cancelled', async ({ page }) => {
  // Install the base mock layer first
  await mockApi(page)

  // Override the experiment GET with a sequenced mock so we can drive state transitions.
  // Sequence:
  //   call 0 (initial page load):              running
  //   call 1 (first poll after cancel POST):   cancelling
  //   call 2+ (subsequent polls):              cancelled  (afterExhausted: 'last')
  const handle = await sequencedMock(
    page,
    /\/api\/experiments\/[^/]+$/,
    [
      { status: 200, body: runningExperiment },
      { status: 200, body: cancellingExperiment },
      { status: 200, body: cancelledExperiment },
    ],
    { method: 'GET', afterExhausted: 'last' },
  )

  // Use fake timers so we control polling without real 10-second waits
  await page.clock.install({ time: new Date('2026-04-17T10:00:00Z') })

  await page.goto(`/experiments/${RUNNING_ID}`)

  // --- Assert initial "running" state ---
  await expect(page.getByText('running').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible()

  // --- Track the cancel POST request ---
  const cancelRequestPromise = page.waitForRequest(
    (req) =>
      req.url().includes(`/experiments/${RUNNING_ID}/cancel`) && req.method() === 'POST',
    { timeout: 5000 },
  )

  // --- Open the modal and confirm ---
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()
  await page.getByRole('button', { name: 'Stop experiment' }).click()

  // Assert POST /experiments/:id/cancel fired
  const cancelReq = await cancelRequestPromise
  expect(cancelReq.method()).toBe('POST')
  expect(cancelReq.url()).toContain(`/experiments/${RUNNING_ID}/cancel`)

  // --- Advance clock to trigger the first poll → cancelling ---
  await page.clock.runFor(11_000)
  await expect(page.getByText('cancelling').first()).toBeVisible()

  // --- Advance clock again to trigger the second poll → cancelled ---
  await page.clock.runFor(11_000)
  await expect(page.getByText('cancelled').first()).toBeVisible()

  // Cancel button must be gone once the experiment is in a terminal state
  await expect(page.getByRole('button', { name: 'Cancel' })).not.toBeVisible()

  // Sequenced mock was called at least 3 times (initial + 2 polls)
  expect(handle.callCount()).toBeGreaterThanOrEqual(3)
})

// ---------------------------------------------------------------------------
// Test 2: Negative — completed experiment has no usable Cancel button
// ---------------------------------------------------------------------------

test('completed experiment: Cancel button is absent or disabled', async ({ page }) => {
  await mockApi(page)

  // Override to ensure we always serve the completed fixture for this ID
  await sequencedMock(
    page,
    /\/api\/experiments\/[^/]+$/,
    [{ status: 200, body: completedExperiment }],
    { method: 'GET', afterExhausted: 'last' },
  )

  await page.goto(`/experiments/${COMPLETED_ID}`)

  // Wait for the page to finish loading (status badge should appear)
  await expect(page.getByText('completed').first()).toBeVisible()

  // The Cancel button must not be interactable.
  // The component renders it only when !isTerminal — so it should not be present at all.
  const cancelBtn = page.getByRole('button', { name: 'Cancel' })
  const count = await cancelBtn.count()
  if (count > 0) {
    // If somehow rendered, it must be disabled — never clickable
    await expect(cancelBtn).toBeDisabled()
  } else {
    // Preferred: not rendered at all
    await expect(cancelBtn).not.toBeVisible()
  }
})

// ---------------------------------------------------------------------------
// Test 3: Modal dismiss — "Keep Running" closes modal, no POST fires
// ---------------------------------------------------------------------------

test('modal dismiss: "Keep Running" closes modal without sending cancel POST', async ({ page }) => {
  await mockApi(page)

  // Serve running fixture for this experiment
  await sequencedMock(
    page,
    /\/api\/experiments\/[^/]+$/,
    [{ status: 200, body: runningExperiment }],
    { method: 'GET', afterExhausted: 'last' },
  )

  await page.goto(`/experiments/${RUNNING_ID}`)

  // Confirm the experiment is shown as running
  await expect(page.getByText('running').first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible()

  // Track any cancel POST requests — there must be none
  const cancelRequests: string[] = []
  page.on('request', (req) => {
    if (req.url().includes('/cancel') && req.method() === 'POST') {
      cancelRequests.push(req.url())
    }
  })

  // Open the modal
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).toBeVisible()

  // Dismiss via "Keep running"
  await page.getByRole('button', { name: 'Keep running' }).click()

  // Modal must have disappeared
  await expect(page.getByRole('heading', { name: 'Stop all pending runs?' })).not.toBeVisible()

  // No cancel POST should have been sent
  expect(cancelRequests).toHaveLength(0)

  // The Cancel button must still be present (experiment is still running)
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible()
})
