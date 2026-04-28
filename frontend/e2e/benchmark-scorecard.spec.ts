/**
 * Playwright e2e tests for the BenchmarkScorecardPanel on the experiment
 * results page.
 *
 * Strategy: override the /api/experiments/:id/results route on a per-test
 * basis to inject or omit benchmark_scorecards.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RESULTS_PATH = `**/api/experiments/${EXPERIMENT_ID}/results`

// ---------------------------------------------------------------------------
// Benchmark scorecard fixture data (mirrors
// frontend/src/__tests__/fixtures/benchmark_scorecard.fixture.ts)
// ---------------------------------------------------------------------------

const benchmarkResultsWithScorecards = {
  runs: [
    {
      run_id: 'run-001-aaa',
      experiment_id: EXPERIMENT_ID,
      model: 'gpt-4o',
      strategy: 'zero_shot',
      tool_variant: 'with_tools',
      profile: 'default',
      verification: 'none',
      status: 'completed',
      precision: 0.812,
      recall: 0.743,
      f1: 0.776,
      fpr: 0.045,
      tp_count: 52,
      fp_count: 12,
      fn_count: 18,
      cost_usd: 0.312,
      duration_seconds: 145,
    },
  ],
  findings: [],
  benchmark_scorecards: [
    {
      dataset_name: 'BenchmarkPython-1.0',
      per_cwe: [
        {
          cwe_id: 'CWE-89',
          tp: 38,
          fp: 4,
          tn: 46,
          fn: 12,
          precision: 0.905,
          recall: 0.76,
          f1: 0.826,
          fp_rate: 0.08,
          owasp_score: 0.68,
          warning: null,
        },
        {
          cwe_id: 'CWE-22',
          tp: 3,
          fp: 1,
          tn: 4,
          fn: 2,
          precision: null,
          recall: null,
          f1: null,
          fp_rate: null,
          owasp_score: null,
          warning: 'insufficient sample size (n<25 per polarity)',
        },
      ],
      aggregate: {
        tp: 41,
        fp: 5,
        tn: 50,
        fn: 14,
        precision: 0.891,
        recall: 0.746,
        f1: 0.812,
        fp_rate: 0.091,
        owasp_score: 0.655,
        warning: null,
      },
    },
  ],
}

const benchmarkResultsWithoutScorecards = {
  runs: benchmarkResultsWithScorecards.runs,
  findings: [],
  // benchmark_scorecards field intentionally absent
}

function jsonBody(body: unknown) {
  return {
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// Panel renders when scorecards are present
// ---------------------------------------------------------------------------

test.describe('BenchmarkScorecardPanel — with scorecards', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(RESULTS_PATH, (route) =>
      route.fulfill(jsonBody(benchmarkResultsWithScorecards))
    )
  })

  test('renders the Benchmark Scorecard section heading', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Benchmark Scorecard' })).toBeVisible()
  })

  test('shows the dataset name as a collapsible section', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByText('BenchmarkPython-1.0')).toBeVisible()
  })

  test('shows the aggregate OWASP score as a headline percentage', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    // aggregate owasp_score = 0.655 → "65.5%"
    await expect(page.getByTestId('owasp-headline')).toContainText('65.5%')
  })

  test('renders the per-CWE table with correct column headers', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    const panel = page.getByTestId('benchmark-scorecard-panel')
    await expect(panel.getByText('CWE')).toBeVisible()
    await expect(panel.getByText('Precision')).toBeVisible()
    await expect(panel.getByText('Recall')).toBeVisible()
    await expect(panel.getByText('F1')).toBeVisible()
    await expect(panel.getByText('FP-rate')).toBeVisible()
    await expect(panel.getByText('OWASP Score')).toBeVisible()
  })

  test('shows CWE-89 row in the table', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByTestId('benchmark-scorecard-panel').getByText('CWE-89')).toBeVisible()
  })

  test('warning badge appears on rows where warning is non-null', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    const panel = page.getByTestId('benchmark-scorecard-panel')

    // CWE-22 has a warning, CWE-89 does not
    const cwe22Row = panel.locator('tr').filter({ hasText: 'CWE-22' })
    const warningIcon = cwe22Row.getByRole('img', { name: 'warning' })
    await expect(warningIcon).toBeVisible()
  })

  test('warning icon has tooltip text matching warning string', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    const panel = page.getByTestId('benchmark-scorecard-panel')

    const cwe22Row = panel.locator('tr').filter({ hasText: 'CWE-22' })
    const warningIcon = cwe22Row.getByRole('img', { name: 'warning' })
    const title = await warningIcon.getAttribute('title')
    expect(title).toBe('insufficient sample size (n<25 per polarity)')
  })

  test('no warning badge on rows where warning is null', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    const panel = page.getByTestId('benchmark-scorecard-panel')

    const cwe89Row = panel.locator('tr').filter({ hasText: 'CWE-89' })
    await expect(cwe89Row.getByRole('img', { name: 'warning' })).not.toBeVisible()
  })

  test('null metrics render as em-dash (—) for CWE-22', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    const panel = page.getByTestId('benchmark-scorecard-panel')

    // CWE-22 has all numeric metrics as null — the row should contain multiple '—'
    const cwe22Row = panel.locator('tr').filter({ hasText: 'CWE-22' })
    // At least one cell with '—' must be present in that row
    const dashCells = cwe22Row.locator('td').filter({ hasText: '—' })
    await expect(dashCells).not.toHaveCount(0)
  })

  test('collapsible: clicking section header collapses the table', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)

    // Table is visible initially
    const panel = page.getByTestId('benchmark-scorecard-panel')
    await expect(panel.locator('table')).toBeVisible()

    // Click the header button to collapse
    const headerBtn = panel.getByRole('button', { name: /BenchmarkPython-1\.0/ })
    await headerBtn.click()

    // Table should be gone
    await expect(panel.locator('table')).not.toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Panel hidden when scorecards absent
// ---------------------------------------------------------------------------

test.describe('BenchmarkScorecardPanel — without scorecards', () => {
  test.beforeEach(async ({ page }) => {
    await page.route(RESULTS_PATH, (route) =>
      route.fulfill(jsonBody(benchmarkResultsWithoutScorecards))
    )
  })

  test('panel is not rendered when benchmark_scorecards is absent', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    // Wait for results to load (matrix heading should appear)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()
    // Scorecard panel must not be present
    await expect(page.getByTestId('benchmark-scorecard-panel')).not.toBeVisible()
  })

  test('Benchmark Scorecard heading is absent when no scorecards', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Benchmark Scorecard' })).not.toBeVisible()
  })
})
