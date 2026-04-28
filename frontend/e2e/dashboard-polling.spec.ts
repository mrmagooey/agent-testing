/**
 * E2E tests for the Dashboard 15-second polling behaviour.
 *
 * User story: "As a Dashboard user, I want the experiments list to refresh
 * automatically every 15 seconds so I see live progress on running
 * experiments without manually reloading."
 *
 * All tests use a fake clock (page.clock.install) to control time without
 * real waits, and a per-test route override layered on top of mockApi.
 *
 * NOTE on React 18 StrictMode double-invoke:
 * The app runs in StrictMode in development (npm run dev). StrictMode
 * intentionally mounts → unmounts → remounts every component to detect
 * side-effects, so `useEffect` fires twice on initial render. This means
 * `fetchExperiments(true)` fires 2 times at mount in dev, and 2 setInterval
 * callbacks are created (the first is cleaned up by the unmount, only the
 * second persists). Tests capture the baseline callCount after the page
 * settles and reason about increments rather than absolute totals.
 *
 * NOTE on fake-clock timing:
 * page.clock.install({ time }) sets the fake clock start. The browser
 * still consumes some real time loading the page, but page.clock.runFor()
 * advances the fake clock by the specified amount from its *current*
 * position (which includes any advancement that occurred during page load).
 * Tests that check "does NOT fire before interval" must account for this by
 * advancing to a known safe distance before the expected fire time.
 */
import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Shared fixture helpers
// ---------------------------------------------------------------------------

const RUNNING_EXPERIMENT = {
  experiment_id: 'cccccccc-0003-0003-0003-000000000003',
  status: 'running',
  dataset: 'cve-2024-polling',
  created_at: '2026-04-27T10:00:00Z',
  completed_at: null,
  total_runs: 20,
  completed_runs: 5,
  running_runs: 3,
  pending_runs: 12,
  failed_runs: 0,
  total_cost_usd: 2.50,
  spend_cap_usd: 50.0,
}

const COMPLETED_EXPERIMENT = {
  ...RUNNING_EXPERIMENT,
  status: 'completed',
  completed_at: '2026-04-27T10:15:00Z',
  completed_runs: 20,
  running_runs: 0,
  pending_runs: 0,
}

function fulfillJson(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

// ---------------------------------------------------------------------------
// Test 1: Initial mount fires GET /api/experiments before any clock advance.
// Verify at least 1 call fires at mount and the count does not grow further
// without a clock advance.
// ---------------------------------------------------------------------------

test('fires at least one GET on initial mount and stabilises before the first poll interval', async ({ page }) => {
  await mockApi(page)

  let callCount = 0
  await page.route('**/api/experiments*', async (route) => {
    if (route.request().method() !== 'GET') {
      return route.fallback()
    }
    callCount++
    await route.fulfill(fulfillJson([RUNNING_EXPERIMENT]))
  })

  // Install fake clock BEFORE goto so setInterval fires under our control
  await page.clock.install({ time: new Date('2026-04-27T10:00:00Z') })
  await page.goto('/')

  // Wait for the page to finish its initial render (experiment row visible)
  const activeSection = page.locator('section').filter({ hasText: 'Active Experiments' })
  await expect(activeSection.getByText('cccccccc')).toBeVisible()

  // Wait until mount-side fetches have settled (handles StrictMode double-invoke)
  await expect.poll(() => callCount, { timeout: 2000, intervals: [50, 100, 100, 200] }).toBeGreaterThanOrEqual(1)

  // Capture baseline after mount has stabilised
  const baseline = callCount
  expect(baseline).toBeGreaterThanOrEqual(1)

  // Without advancing the clock, no additional polling calls should fire.
  // Poll for a short window — if any real-time setInterval slipped through, callCount would tick.
  await expect.poll(() => callCount, { timeout: 500, intervals: [100, 100, 100, 100, 100] }).toBe(baseline)
})

// ---------------------------------------------------------------------------
// Test 2: After 15 s of clock time, exactly one more GET fires.
// We advance 15 s using page.clock.runFor and verify the count increments
// by exactly 1.
// ---------------------------------------------------------------------------

test('fires a second GET after advancing 15 s of clock time', async ({ page }) => {
  await mockApi(page)

  let callCount = 0
  await page.route('**/api/experiments*', async (route) => {
    if (route.request().method() !== 'GET') {
      return route.fallback()
    }
    callCount++
    await route.fulfill(fulfillJson([RUNNING_EXPERIMENT]))
  })

  await page.clock.install({ time: new Date('2026-04-27T10:00:00Z') })
  await page.goto('/')

  // Wait for initial render to complete
  const activeSection = page.locator('section').filter({ hasText: 'Active Experiments' })
  await expect(activeSection.getByText('cccccccc')).toBeVisible()

  // Wait until mount-side fetches have settled (handles StrictMode double-invoke)
  await expect.poll(() => callCount, { timeout: 2000, intervals: [50, 100, 100, 200] }).toBeGreaterThanOrEqual(1)
  const baseline = callCount

  // Advance exactly 15 000 ms — the polling interval should fire once
  await page.clock.runFor(15_000)

  // Poll until exactly one more call is registered
  await expect.poll(() => callCount, { timeout: 5000 }).toBe(baseline + 1)
})

// ---------------------------------------------------------------------------
// Test 3: Polled response replaces the table row's status badge.
// Start with one running experiment; after 15 s respond with it completed.
// The row must move out of Active and into Recent Experiments.
// ---------------------------------------------------------------------------

test('polled response updates status badge from running to completed', async ({ page }) => {
  await mockApi(page)

  // Track calls; return running for mount-phase calls, completed for polls.
  // StrictMode may invoke mount twice; we capture the mount baseline then
  // switch to completed for any subsequent (poll) calls.
  let callCount = 0
  let mountBaseline = 0

  await page.route('**/api/experiments*', async (route) => {
    if (route.request().method() !== 'GET') {
      return route.fallback()
    }
    callCount++
    // While mountBaseline is 0 (not yet captured) OR this call is within the
    // mount phase, return running. After baseline is set, return completed.
    const isInitialPhase = mountBaseline === 0 || callCount <= mountBaseline
    const body = isInitialPhase ? [RUNNING_EXPERIMENT] : [COMPLETED_EXPERIMENT]
    await route.fulfill(fulfillJson(body))
  })

  await page.clock.install({ time: new Date('2026-04-27T10:00:00Z') })
  await page.goto('/')

  // Initially the experiment is in the Active section with "running" badge
  const activeSection = page.locator('section').filter({ hasText: 'Active Experiments' })
  await expect(activeSection.getByText('cccccccc')).toBeVisible()
  await expect(activeSection.locator('span', { hasText: 'running' }).first()).toBeVisible()

  // Wait until mount-side fetches have settled before locking in the baseline
  await expect.poll(() => callCount, { timeout: 2000, intervals: [50, 100, 100, 200] }).toBeGreaterThanOrEqual(1)
  mountBaseline = callCount

  // Advance 15 s to trigger the poll
  await page.clock.runFor(15_000)

  // After the polled response (status: completed), the experiment moves out of
  // Active and into Recent Experiments.
  const recentSection = page.locator('section').filter({ hasText: 'Recent Experiments' })
  await expect(recentSection.getByText('cccccccc')).toBeVisible({ timeout: 5000 })

  // The Active section no longer shows the experiment row
  await expect(activeSection.getByText('cccccccc')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 4: Polling continues — two more GETs fire at 15 s and 30 s marks.
// ---------------------------------------------------------------------------

test('polling continues to fire a third GET at 30 s', async ({ page }) => {
  await mockApi(page)

  let callCount = 0
  await page.route('**/api/experiments*', async (route) => {
    if (route.request().method() !== 'GET') {
      return route.fallback()
    }
    callCount++
    await route.fulfill(fulfillJson([RUNNING_EXPERIMENT]))
  })

  await page.clock.install({ time: new Date('2026-04-27T10:00:00Z') })
  await page.goto('/')

  // Wait for initial render
  const activeSection = page.locator('section').filter({ hasText: 'Active Experiments' })
  await expect(activeSection.getByText('cccccccc')).toBeVisible()

  // Wait until mount-side fetches have settled (handles StrictMode double-invoke)
  await expect.poll(() => callCount, { timeout: 2000, intervals: [50, 100, 100, 200] }).toBeGreaterThanOrEqual(1)
  const baseline = callCount

  // Advance to 15 s → first poll fires (+1)
  await page.clock.runFor(15_000)
  await expect.poll(() => callCount, { timeout: 5000 }).toBe(baseline + 1)

  // Advance another 15 s → second poll fires (+2 total from baseline)
  await page.clock.runFor(15_000)
  await expect.poll(() => callCount, { timeout: 5000 }).toBe(baseline + 2)
})

// ---------------------------------------------------------------------------
// Test 5: Unmount stops polling — navigating away prevents further GETs.
// ---------------------------------------------------------------------------

test('unmount stops polling — no further GETs fire after navigating away', async ({ page }) => {
  await mockApi(page)

  let callCount = 0
  await page.route('**/api/experiments*', async (route) => {
    if (route.request().method() !== 'GET') {
      return route.fallback()
    }
    callCount++
    await route.fulfill(fulfillJson([RUNNING_EXPERIMENT]))
  })

  await page.clock.install({ time: new Date('2026-04-27T10:00:00Z') })
  await page.goto('/')

  // Wait for initial render
  const activeSection = page.locator('section').filter({ hasText: 'Active Experiments' })
  await expect(activeSection.getByText('cccccccc')).toBeVisible()

  // Navigate away — this unmounts the Dashboard component and its useEffect
  // cleanup runs clearInterval on pollingRef.current
  await page.goto('/findings')

  // Wait for the Findings page to fully render before capturing the counter.
  // This ensures the Dashboard is truly unmounted and any in-flight requests
  // that started before the navigation have resolved.
  await expect(page).toHaveURL(/\/findings/)
  await expect(page.getByRole('heading', { name: /findings/i })).toBeVisible()
  const countAfterNav = callCount

  // Advance 30 s worth of clock ticks — if the interval was NOT cleared, it
  // would fire twice; if cleared correctly, callCount stays at countAfterNav
  await page.clock.runFor(30_000)

  // No additional /experiments GETs should have fired after unmount
  expect(callCount).toBe(countAfterNav)
})
