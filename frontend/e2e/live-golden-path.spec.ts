/**
 * Live end-to-end golden-path spec.
 *
 * Only runs when E2E_LIVE=1 is set in the environment; filtered with the
 * @live tag so regular CI (mock-based) never picks it up.
 *
 * Submit approach: Phase B uses `request.post('/api/experiments')` directly rather
 * than driving the form UI. This is more reliable because:
 *  - The dataset <select> option text includes label_count / languages from the
 *    live database and cannot be predicted statically.
 *  - The model list from the live coordinator may differ from the fixture.
 * After the API call we navigate the browser to the dashboard to assert the
 * new experiment appears there.
 */

import { test, expect } from '@playwright/test'
import {
  isLive,
  uniqueExperimentId,
  LIVE_MODEL_ID,
  LIVE_DATASET_NAME,
  LIVE_DATASET_VERSION,
} from './helpers/liveMode'

test.describe('@live golden path', () => {
  test.skip(!isLive, 'Run with E2E_LIVE=1 against a kind-e2e cluster')
  // Live tests consume real LLM tokens; one browser is enough for plumbing signal.
  test.skip(({ browserName }) => isLive && browserName !== 'chromium', 'live path runs chromium-only')

  // Phase C may need up to 10 minutes for the experiment to reach a terminal state.
  test.setTimeout(10 * 60 * 1000)

  test('submit experiment, poll until terminal, inspect run detail', async ({ page, request }) => {
    let experimentId: string

    // -----------------------------------------------------------------------
    // Phase A: Dashboard loads
    // -----------------------------------------------------------------------
    await test.step('Phase A: dashboard loads', async () => {
      await page.goto('/')
      await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
      // Nav landmark is always present regardless of experiment count
      await expect(page.getByRole('navigation')).toBeVisible()
    })

    // -----------------------------------------------------------------------
    // Phase B: Submit an experiment via the API
    // -----------------------------------------------------------------------
    await test.step('Phase B: submit experiment via API', async () => {
      const bid = uniqueExperimentId('live-ui')
      const payload = {
        experiment_id: bid,
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

      const resp = await request.post('/api/experiments', { data: payload })
      // The coordinator returns 201 on success; accept both 200 and 201.
      expect(resp.status()).toBeGreaterThanOrEqual(200)
      expect(resp.status()).toBeLessThan(300)

      const body = await resp.json()
      // Grab whatever experiment_id the server assigned (may echo ours or generate one)
      experimentId = (body.experiment_id as string) ?? bid

      // Navigate to the dashboard and confirm the experiment appears. The dashboard
      // shows truncated experiment IDs (first 8 chars + ellipsis). Prior test runs
      // may leave other experiments with the same short prefix, so `.first()`
      // avoids strict-mode violations while still proving the new experiment landed.
      await page.goto('/')
      const shortId = experimentId.slice(0, 8)
      await expect(
        page.getByText(new RegExp(shortId, 'i')).first()
      ).toBeVisible({ timeout: 15_000 })
    })

    // -----------------------------------------------------------------------
    // Phase C: Poll until terminal, then inspect run detail
    // -----------------------------------------------------------------------
    await test.step('Phase C: poll until terminal and inspect run detail', async () => {
      // Poll the API (not the browser) for efficiency; the 10-minute timeout
      // on the whole test gives us the outer bound.
      const POLL_INTERVAL_MS = 15_000
      const MAX_WAIT_MS = 9 * 60 * 1000  // stay inside the 10-min test timeout
      const deadline = Date.now() + MAX_WAIT_MS
      let finalStatus = ''

      while (Date.now() < deadline) {
        const resp = await request.get(`/api/experiments/${experimentId}`)
        expect(resp.ok()).toBe(true)
        const experiment = await resp.json()
        finalStatus = experiment.status as string

        if (['completed', 'failed', 'cancelled'].includes(finalStatus)) {
          break
        }
        // Not terminal yet — wait before re-checking.
        await page.waitForTimeout(POLL_INTERVAL_MS)
      }

      // We accept completed or failed; what matters is the run detail page loads.
      expect(['completed', 'failed', 'cancelled']).toContain(finalStatus)

      // Navigate to the experiment detail page in the browser. ExperimentDetail renders
      // the experiment_id as the primary <h1> once the experiment fetch completes.
      await page.goto(`/experiments/${experimentId}`)
      await expect(
        page.getByRole('heading', { name: experimentId })
      ).toBeVisible({ timeout: 30_000 })

      // Fetch the run list from the API to get a run ID for the detail page.
      const runsResp = await request.get(`/api/experiments/${experimentId}/runs`)
      expect(runsResp.ok()).toBe(true)
      const runs = await runsResp.json() as Array<{ id: string; experiment_id?: string; status?: string; prompt_tokens?: number; completion_tokens?: number; cost_usd?: number }>
      expect(runs.length).toBeGreaterThan(0)

      const firstRun = runs[0]
      const runId = firstRun.id

      // Navigate to the run detail page.
      await page.goto(`/experiments/${experimentId}/runs/${runId}`)

      // The RunDetail page renders a Findings section header once the run loads.
      // This doubles as proof that the SPA, API, and data join all succeeded.
      await expect(
        page.getByRole('heading', { name: /findings/i })
      ).toBeVisible({ timeout: 30_000 })

      // The Duration row is always rendered once the run data loads — this
      // proves the SPA fetched /api/experiments/{id}/runs/{runId} and rendered it.
      // Cost may show as "—" when the model ID isn't in pricing.yaml, so we
      // don't rely on a $ value.
      await expect(page.locator('dt', { hasText: /^Duration$/ })).toBeVisible({ timeout: 5_000 })
    })
  })
})
