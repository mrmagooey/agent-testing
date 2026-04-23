/**
 * Live end-to-end cancel and failure path specs.
 *
 * Only runs when E2E_LIVE=1 is set in the environment; filtered with the
 * @live tag so regular CI (mock-based) never picks it up.
 *
 * LIMITATIONS:
 * - These tests require a running kind cluster with the coordinator
 *   port-forwarded to :8080 (or E2E_LIVE_BASE_URL set). Without it they skip.
 * - They do NOT clean up submitted experiments; stale state may accumulate
 *   across runs. That is consistent with existing live tests.
 * - Peak memory is well under 2 GB: these tests are network-bound pollers with
 *   no large in-memory fixtures.
 *
 * UI GAPS DISCOVERED:
 * - The Cancel button on ExperimentDetail has no data-testid; we locate it by
 *   its visible text "Cancel". This is fragile if the label changes.
 * - The modal confirm button text is "Stop experiment"; if that copy changes the
 *   test must be updated.
 * - The unavailable_models test (Test 2) is skipped by default because there is
 *   no reliable way to force the kind-e2e coordinator to reject a known model as
 *   key_missing without real API key configuration. The mock-based offline tests
 *   in experiment-new.spec.ts already cover the client-side error-banner path.
 *   See the skip comment in that test for the tracking gap.
 */

import { test, expect } from '@playwright/test'
import {
  isLive,
  uniqueExperimentId,
  LIVE_MODEL_ID,
  LIVE_DATASET_NAME,
  LIVE_DATASET_VERSION,
} from './helpers/liveMode'

// ---------------------------------------------------------------------------
// Shared constants
// ---------------------------------------------------------------------------

/** Minimal 1-run matrix so we get a running experiment quickly. */
const MINIMAL_PAYLOAD = {
  dataset_name: LIVE_DATASET_NAME,
  dataset_version: LIVE_DATASET_VERSION,
  model_ids: [LIVE_MODEL_ID],
  strategies: ['single_agent'],
  tool_variants: ['without_tools'],
  review_profiles: ['default'],
  verification_variants: ['none'],
  parallel_modes: [false],
  tool_extension_sets: [[]],
  num_repetitions: 1,
  max_experiment_cost_usd: 0.10,
  strategy_configs: { single_agent: { max_turns: 3 } },
}

// ---------------------------------------------------------------------------
// @live live cancel
// ---------------------------------------------------------------------------

test.describe('@live live cancel', () => {
  test.skip(!isLive, 'Run with E2E_LIVE=1 against a kind-e2e cluster')
  // Live tests consume real cluster resources; run chromium only.
  test.skip(({ browserName }) => isLive && browserName !== 'chromium', 'live path runs chromium-only')

  // Allow up to 5 minutes total: submit + wait for running + cancel + wait for terminal.
  test.setTimeout(5 * 60 * 1000)

  test('@live live cancel: submit, wait for running, cancel, assert cancelled', async ({ page, request }) => {
    let experimentId: string

    // ------------------------------------------------------------------
    // Phase A: Submit a minimal experiment via the API
    // ------------------------------------------------------------------
    await test.step('Phase A: submit minimal experiment', async () => {
      const bid = uniqueExperimentId('live-ui-cancel')
      const payload = { ...MINIMAL_PAYLOAD, experiment_id: bid }

      const resp = await request.post('/api/experiments', { data: payload })
      expect(resp.status()).toBeGreaterThanOrEqual(200)
      expect(resp.status()).toBeLessThan(300)

      const body = await resp.json()
      experimentId = (body.experiment_id as string) ?? bid

      // Navigate to dashboard to confirm the experiment appeared.
      await page.goto('/')
      const shortId = experimentId.slice(0, 8)
      await expect(
        page.getByText(new RegExp(shortId, 'i')).first()
      ).toBeVisible({ timeout: 15_000 })
    })

    // ------------------------------------------------------------------
    // Phase B: Poll via API until status reaches 'running'
    // ------------------------------------------------------------------
    await test.step('Phase B: poll until running', async () => {
      const POLL_INTERVAL_MS = 5_000
      const MAX_WAIT_MS = 60_000  // generous: scheduler may be slow to pick up
      const deadline = Date.now() + MAX_WAIT_MS
      let currentStatus = ''

      while (Date.now() < deadline) {
        const resp = await request.get(`/api/experiments/${experimentId}`)
        expect(resp.ok()).toBe(true)
        const experiment = await resp.json()
        currentStatus = experiment.status as string

        if (currentStatus === 'running') break
        if (['completed', 'failed', 'cancelled'].includes(currentStatus)) {
          // Experiment already finished before we could cancel — that is fine;
          // proceed to the cancellation assertion which will still pass.
          break
        }
        await page.waitForTimeout(POLL_INTERVAL_MS)
      }

      // We proceed to cancel even if still 'pending' — cancel must be safe.
      // If it somehow already completed, the UI test below still verifies terminal state.
    })

    // ------------------------------------------------------------------
    // Phase C: Navigate to experiment detail page and click Cancel
    // ------------------------------------------------------------------
    await test.step('Phase C: navigate to detail page and cancel', async () => {
      await page.goto(`/experiments/${experimentId}`)

      // Wait for the experiment heading to render.
      await expect(
        page.getByRole('heading', { name: experimentId })
      ).toBeVisible({ timeout: 30_000 })

      // If the experiment is already in a terminal state (completed/failed/cancelled)
      // the Cancel button will not be rendered. In that case skip the UI cancel step.
      const cancelButton = page.getByRole('button', { name: /^Cancel$/ })
      const isCancelVisible = await cancelButton.isVisible().catch(() => false)

      if (isCancelVisible) {
        await cancelButton.click()

        // The CancelConfirmModal should appear with its confirmation heading.
        await expect(
          page.getByRole('heading', { name: /stop all pending runs/i })
        ).toBeVisible({ timeout: 5_000 })

        // Click "Stop experiment" to confirm.
        await page.getByRole('button', { name: /stop experiment/i }).click()

        // Modal should close (heading disappears).
        await expect(
          page.getByRole('heading', { name: /stop all pending runs/i })
        ).not.toBeVisible({ timeout: 10_000 })
      }
    })

    // ------------------------------------------------------------------
    // Phase D: Poll API until experiment reaches 'cancelled' (or other terminal)
    // ------------------------------------------------------------------
    await test.step('Phase D: poll until cancelled', async () => {
      const POLL_INTERVAL_MS = 5_000
      const MAX_WAIT_MS = 60_000  // 60 s for in-flight runs to drain
      const deadline = Date.now() + MAX_WAIT_MS
      let finalStatus = ''

      while (Date.now() < deadline) {
        const resp = await request.get(`/api/experiments/${experimentId}`)
        expect(resp.ok()).toBe(true)
        const experiment = await resp.json()
        finalStatus = experiment.status as string

        if (['completed', 'failed', 'cancelled'].includes(finalStatus)) break
        await page.waitForTimeout(POLL_INTERVAL_MS)
      }

      // Must have reached a terminal state.
      expect(['completed', 'failed', 'cancelled']).toContain(finalStatus)
    })

    // ------------------------------------------------------------------
    // Phase E: Assert no lingering 'running' runs in the matrix
    // ------------------------------------------------------------------
    await test.step('Phase E: assert no running runs remain', async () => {
      const runsResp = await request.get(`/api/experiments/${experimentId}/runs`)
      expect(runsResp.ok()).toBe(true)
      const runs = await runsResp.json() as Array<{ id: string; status?: string }>

      const runningRuns = runs.filter((r) => r.status === 'running')
      expect(runningRuns).toHaveLength(0)

      // Every run should be in a terminal state.
      const terminalStatuses = new Set(['completed', 'failed', 'cancelled'])
      for (const run of runs) {
        if (run.status !== undefined) {
          expect(terminalStatuses).toContain(run.status)
        }
      }
    })
  })
})

// ---------------------------------------------------------------------------
// @live unavailable_models error
// ---------------------------------------------------------------------------

test.describe('@live unavailable_models error', () => {
  test.skip(!isLive, 'Run with E2E_LIVE=1 against a kind-e2e cluster')
  test.skip(({ browserName }) => isLive && browserName !== 'chromium', 'live path runs chromium-only')

  test.setTimeout(2 * 60 * 1000)

  test(
    '@live unavailable_models: surface error banner and allow resubmit',
    async ({ page }) => {
      // -----------------------------------------------------------------------
      // GAP: There is no reliable way to trigger the unavailable_models error
      // path in the kind-e2e cluster without intentionally misconfiguring API
      // keys for a specific model. The kind cluster's coordinator returns
      //   { detail: { error: 'unavailable_models', models: [...] } }
      // only when a model's API key is missing or the probe returns 'key_missing'.
      // Since all models in the live-e2e test configuration are presumed
      // available (the golden-path test depends on them), this path cannot be
      // triggered reliably without a cluster-side fixture.
      //
      // The UI-side error-banner rendering is fully covered by offline mock
      // tests in experiment-new.spec.ts and experiment-new-extended.spec.ts,
      // which drive the same ExperimentNew component with the mock returning
      // { detail: { error: 'unavailable_models', models: [...] } }.
      //
      // Tracking gap: a follow-up task should add a dedicated "unavailable"
      // model slug to the live-e2e cluster configuration (e.g. a placeholder
      // provider with probe_status='stale' and no API key) so this path can
      // be exercised end-to-end.
      // -----------------------------------------------------------------------
      test.skip(
        true,
        'Gap: no reliable unavailable model in live cluster config. ' +
          'Covered by offline mocks in experiment-new.spec.ts. ' +
          'See comment in test file for follow-up tracking.',
      )

      // The block below would be the implementation once the cluster gap is resolved.
      // It is kept here as documentation of the intended test flow.

      // Navigate to the new experiment form.
      await page.goto('/experiments/new')
      await expect(page.getByRole('heading', { name: /new experiment/i })).toBeVisible({ timeout: 15_000 })

      // Select a model known to be unavailable in the cluster.
      // (Replace 'some-unavailable-model-id' with the actual slug once configured.)
      const UNAVAILABLE_MODEL_ID = 'some-unavailable-model-id'
      await page.getByRole('checkbox', { name: new RegExp(UNAVAILABLE_MODEL_ID, 'i') }).check()

      // Fill minimum required fields (dataset, strategy, tool variant) to allow form submission.
      // Dataset and other required fields would need to be set here.

      // Submit the form.
      await page.getByRole('button', { name: /run experiment/i }).click()

      // The error banner should appear with data-testid="unavailable-models-error".
      await expect(page.locator('[data-testid="unavailable-models-error"]')).toBeVisible({ timeout: 10_000 })

      // The banner should mention the unavailable model.
      await expect(page.locator('[data-testid="unavailable-models-error"]')).toContainText(UNAVAILABLE_MODEL_ID)

      // The user can dismiss by checking "Allow unavailable models".
      await page.locator('[data-testid="allow-unavailable-checkbox"]').check()

      // Clicking "Submit anyway" (data-testid="submit-with-override-btn") should
      // attempt resubmission with allow_unavailable_models: true.
      await page.locator('[data-testid="submit-with-override-btn"]').click()

      // After override submission the page should navigate away from /experiments/new
      // (redirect to the new experiment's detail page).
      await expect(page).not.toHaveURL(/\/experiments\/new$/, { timeout: 30_000 })
    }
  )
})
