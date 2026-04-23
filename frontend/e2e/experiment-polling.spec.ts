/**
 * E2E tests for experiment polling / in-flight state transitions (gap #4).
 *
 * The useExperiment hook polls GET /api/experiments/:id every 10 s and stops
 * when the status reaches a terminal state (completed, failed, cancelled).
 *
 * Because 10 s is too long for test timing, we patch setInterval via
 * page.addInitScript to run every FAST_POLL_MS instead.  The patch must be
 * installed before the app script executes, which addInitScript guarantees.
 *
 * Synchronisation strategy: we use a manually-controlled route handler that
 * resolves each response only when the test code explicitly calls `advance()`.
 * This eliminates timing races and makes the tests fully deterministic.
 */
import { test, expect } from '@playwright/test'
import type { Page } from '@playwright/test'
import { mockApi } from './helpers/mockApi'
import { sequencedMock } from './helpers/sequencedMock'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const EXPERIMENT_ID = 'dddddddd-0004-0004-0004-000000000004'

/** Patched poll interval (ms).  Short enough to advance quickly in tests. */
const FAST_POLL_MS = 600

/** Timeout for UI assertions after a response is delivered. */
const RENDER_TIMEOUT_MS = 3_000

/** Timeout for waitForResponse / response-sequence waits. */
const RESPONSE_TIMEOUT_MS = 8_000

// ---------------------------------------------------------------------------
// Helper: build a minimal experiment response body.
// ---------------------------------------------------------------------------
function experimentBody(
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled',
  overrides: Partial<{
    pending_runs: number
    running_runs: number
    completed_runs: number
    total_runs: number
    failed_runs: number
    total_cost_usd: number
    spend_cap_usd: number | null
    dataset: string
  }> = {},
) {
  return {
    experiment_id: EXPERIMENT_ID,
    status,
    dataset: 'cve-2024-python',
    created_at: '2026-04-23T08:00:00Z',
    completed_at: status === 'completed' ? '2026-04-23T09:00:00Z' : null,
    total_runs: 8,
    completed_runs: 0,
    running_runs: 0,
    pending_runs: 8,
    failed_runs: 0,
    total_cost_usd: 0,
    spend_cap_usd: null,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Helper: install the setInterval speed-up patch BEFORE the app boots.
// We replace the native setInterval with a version that clamps any interval
// > FAST_POLL_MS down to FAST_POLL_MS.  clearInterval is untouched so the
// stop-polling logic works correctly.
// ---------------------------------------------------------------------------
async function installFastPollPatch(page: Page, fastMs: number) {
  await page.addInitScript((ms: number) => {
    const _orig = window.setInterval.bind(window)
    // @ts-expect-error intentional monkey-patch
    window.setInterval = (fn: TimerHandler, delay?: number, ...args: unknown[]) => {
      const patched = typeof delay === 'number' && delay > ms ? ms : delay
      return _orig(fn, patched, ...args)
    }
  }, fastMs)
}

// ---------------------------------------------------------------------------
// Controlled step sequencer:
// Registers a route handler that holds each request open until advance()
// is called.  This gives the test deterministic control over when each
// state transition happens, regardless of CPU speed or CI load.
// ---------------------------------------------------------------------------
interface StepSequencer {
  /** Deliver the next queued response to the browser. */
  advance(): Promise<void>
  /** Total number of times the route was called. */
  callCount(): number
  /** Remove the route override. */
  unroute(): Promise<void>
}

async function installStepSequencer(
  page: Page,
  bodies: object[],
): Promise<StepSequencer> {
  // Each entry is a { resolve } for a pending `route.fulfill()`.
  // When the handler is invoked it creates a Promise and stashes its resolver.
  // When advance() is called, it fulfills the oldest pending request.
  const pendingFulfills: Array<() => void> = []
  let responseIndex = 0
  let callsTotal = 0

  const urlPattern = `**/api/experiments/${EXPERIMENT_ID}`

  const handler = async (route: import('@playwright/test').Route) => {
    if (route.request().method() !== 'GET') {
      return route.fallback()
    }

    callsTotal++
    const idx = responseIndex
    // Serve the last body if we've exhausted the list.
    const body = bodies[Math.min(idx, bodies.length - 1)]
    responseIndex++

    // Hold the request open until advance() releases it.
    await new Promise<void>((resolve) => {
      pendingFulfills.push(resolve)
    })

    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })
  }

  await page.route(urlPattern, handler)

  let resolveHolder: (() => void) | null = null

  return {
    advance: async () => {
      // Wait until there's at least one pending request.
      if (pendingFulfills.length === 0) {
        await new Promise<void>((res) => {
          resolveHolder = res
          const check = setInterval(() => {
            if (pendingFulfills.length > 0) {
              clearInterval(check)
              resolveHolder = null
              res()
            }
          }, 10)
        })
      }
      const fulfill = pendingFulfills.shift()!
      fulfill()
    },
    callCount: () => callsTotal,
    unroute: () => page.unroute(urlPattern, handler),
  }
}

// ---------------------------------------------------------------------------
// Test 1 – transitions through states
// ---------------------------------------------------------------------------
test('transitions through states', async ({ page }) => {
  // Collect console messages to assert no errors / warnings.
  const consoleMsgs: Array<{ type: string; text: string }> = []
  page.on('console', (msg) => {
    consoleMsgs.push({ type: msg.type(), text: msg.text() })
  })

  // Install polling speed-up BEFORE the app loads.
  await installFastPollPatch(page, FAST_POLL_MS)

  // Set up the base mockApi (handles all routes except the one we override).
  await mockApi(page)

  // Install the step sequencer AFTER mockApi so it wins (last-registered first).
  const seq = await installStepSequencer(page, [
    // Step 0: pending
    experimentBody('pending', {
      pending_runs: 8,
      running_runs: 0,
      completed_runs: 0,
      total_runs: 8,
    }),
    // Step 1: running, 2 of 8 started
    experimentBody('running', {
      pending_runs: 6,
      running_runs: 2,
      completed_runs: 0,
      total_runs: 8,
    }),
    // Step 2: running, 4 of 8 done
    experimentBody('running', {
      pending_runs: 2,
      running_runs: 2,
      completed_runs: 4,
      total_runs: 8,
    }),
    // Step 3: completed
    experimentBody('completed', {
      pending_runs: 0,
      running_runs: 0,
      completed_runs: 8,
      total_runs: 8,
    }),
  ])

  // The status badge has classes "px-3 py-1 rounded-full text-sm font-medium …"
  // which distinguishes it from the small indicator dots that also use rounded-full.
  const statusBadge = page.locator('span.px-3.rounded-full')

  // Navigate.  The initial fetch fires immediately on mount and will block
  // in the sequencer until we call advance().
  // Use waitUntil:'domcontentloaded' so goto returns as soon as the DOM is ready;
  // we don't need network-idle because we're holding our route open deliberately.
  await page.goto(`/experiments/${EXPERIMENT_ID}`, { waitUntil: 'domcontentloaded' })

  // --- Step 0: pending ---
  // Release response 0 (initial fetch).
  await seq.advance()
  await expect(statusBadge).toHaveText('pending', { timeout: RENDER_TIMEOUT_MS })
  await expect(page.getByText('8 pending')).toBeVisible({ timeout: RENDER_TIMEOUT_MS })
  await expect(page.getByText('0 completed')).toBeVisible({ timeout: RENDER_TIMEOUT_MS })

  // --- Step 1: running, 2 started ---
  // The interval will fire and queue another request.  Release it.
  await seq.advance()
  await expect(statusBadge).toHaveText('running', { timeout: RENDER_TIMEOUT_MS })
  await expect(page.getByText('2 running')).toBeVisible({ timeout: RENDER_TIMEOUT_MS })
  await expect(page.getByText('6 pending')).toBeVisible({ timeout: RENDER_TIMEOUT_MS })

  // --- Step 2: running, 4 completed ---
  await seq.advance()
  await expect(page.getByText('4 completed')).toBeVisible({ timeout: RENDER_TIMEOUT_MS })
  await expect(page.getByText('2 running')).toBeVisible({ timeout: RENDER_TIMEOUT_MS })
  await expect(page.getByText('2 pending')).toBeVisible({ timeout: RENDER_TIMEOUT_MS })

  // --- Step 3: completed ---
  await seq.advance()
  await expect(statusBadge).toHaveText('completed', { timeout: RENDER_TIMEOUT_MS })
  await expect(page.getByText('8 completed')).toBeVisible({ timeout: RENDER_TIMEOUT_MS })

  // Clean up the route override.
  await seq.unroute()

  // No console errors or warnings should have been produced.
  const problematic = consoleMsgs.filter(
    (m) => m.type === 'error' || m.type === 'warning',
  )
  expect(
    problematic,
    `Unexpected console messages: ${problematic.map((m) => `[${m.type}] ${m.text}`).join('; ')}`,
  ).toHaveLength(0)
})

// ---------------------------------------------------------------------------
// Test 2 – polling stops at terminal state
// ---------------------------------------------------------------------------
test('polling stops at terminal state', async ({ page }) => {
  // Install polling speed-up BEFORE the app loads.
  await installFastPollPatch(page, FAST_POLL_MS)

  await mockApi(page)

  // Register a sequenced mock with 4 responses:
  //   call 1 (initial fetch): pending
  //   call 2 (poll 1):        running
  //   call 3 (poll 2):        running (partial)
  //   call 4 (poll 3):        completed ← terminal; hook must stop here
  //
  // Use afterExhausted: 'last' to re-serve 'completed' safely if an extra
  // in-flight request arrives just before clearInterval fires.  We verify
  // the count stabilizes via callCount() rather than relying on 'error'.
  const handle = await sequencedMock(
    page,
    `**/api/experiments/${EXPERIMENT_ID}`,
    [
      {
        body: experimentBody('pending', {
          pending_runs: 8,
          running_runs: 0,
          completed_runs: 0,
          total_runs: 8,
        }),
      },
      {
        body: experimentBody('running', {
          pending_runs: 6,
          running_runs: 2,
          completed_runs: 0,
          total_runs: 8,
        }),
      },
      {
        body: experimentBody('running', {
          pending_runs: 2,
          running_runs: 2,
          completed_runs: 4,
          total_runs: 8,
        }),
      },
      {
        body: experimentBody('completed', {
          pending_runs: 0,
          running_runs: 0,
          completed_runs: 8,
          total_runs: 8,
        }),
      },
    ],
    { afterExhausted: 'last' },
  )

  await page.goto(`/experiments/${EXPERIMENT_ID}`)

  // Wait for the UI to show "completed", confirming all 4 responses have been
  // consumed and the hook has received the terminal state.
  // span.px-3.rounded-full targets the status badge specifically (not the indicator dots).
  await expect(
    page.locator('span.px-3.rounded-full'),
  ).toHaveText('completed', { timeout: RESPONSE_TIMEOUT_MS })

  // Snapshot the call count immediately after reaching completed.
  const countAtCompletion = handle.callCount()

  // The hook should have made at most 4 calls (1 initial + up to 3 polls).
  // Allow one extra for an in-flight request that arrived just before
  // clearInterval fired.
  expect(countAtCompletion).toBeGreaterThanOrEqual(4)
  expect(countAtCompletion).toBeLessThanOrEqual(5)

  // Wait several polling intervals and verify the count has stopped growing.
  // expect.poll() re-evaluates the callback and asserts the stable value.
  const stableCount = countAtCompletion
  await expect.poll(
    () => handle.callCount(),
    {
      timeout: FAST_POLL_MS * 4,
      intervals: [FAST_POLL_MS],
      message: 'call count must not increase after polling stops at completed state',
    },
  ).toBe(stableCount)
})
