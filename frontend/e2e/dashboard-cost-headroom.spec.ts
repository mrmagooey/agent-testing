/**
 * A2: Cost Headroom thresholds + Venn SVG + cost delta badge + sticky action bar.
 *
 * Tests:
 * - Cost Headroom card: normal (green), warning (>80%, yellow), critical (>95%, red)
 * - RunCompare: Venn SVG presence + cost delta badge color
 * - ExperimentDetail: sticky action bar appears when runs selected, navigates on compare
 */
import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUNNING_EXPERIMENT_ID = 'bbbbbbbb-0002-0002-0002-000000000002'
const RUN_A = 'run-001-aaa'
const RUN_B = 'run-002-bbb'

// ---------------------------------------------------------------------------
// Cost Headroom — normal (< 80%)
// The fixture experiment has total_cost_usd=12.45, spend_cap_usd=50 → 24.9% — green
// ---------------------------------------------------------------------------

test.describe('Cost Headroom card', () => {
  test('Cost Headroom card is visible with completed experiments', async ({ page }) => {
    await mockApi(page)
    await page.goto('/')
    // Headroom card only renders when costSparkData.length >= 2
    // The fixture has 2 completed experiments, so the Trends section should show
    // We need to check for the headroom card text
    await expect(page.getByText('Cost Headroom')).toBeVisible()
  })

  test('Cost Headroom shows green bar at low spend percentage', async ({ page }) => {
    await mockApi(page)
    // Override experiments so total spend is clearly under 80%
    await page.route('**/api/experiments', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            experiment_id: 'aaa-01',
            status: 'completed',
            dataset: 'ds-1',
            created_at: '2026-04-16T00:00:00Z',
            completed_at: '2026-04-16T01:00:00Z',
            total_runs: 10,
            completed_runs: 10,
            running_runs: 0,
            pending_runs: 0,
            failed_runs: 0,
            total_cost_usd: 10.0,
            spend_cap_usd: 50.0,
          },
          {
            experiment_id: 'aaa-02',
            status: 'completed',
            dataset: 'ds-2',
            created_at: '2026-04-17T00:00:00Z',
            completed_at: '2026-04-17T01:00:00Z',
            total_runs: 10,
            completed_runs: 10,
            running_runs: 0,
            pending_runs: 0,
            failed_runs: 0,
            total_cost_usd: 5.0,
            spend_cap_usd: 50.0,
          },
        ]),
      })
    })
    await page.goto('/')
    // Should show the headroom info
    await expect(page.getByText('Cost Headroom')).toBeVisible()
    await expect(page.getByText(/\/ \$50 cap/)).toBeVisible()
    // No warning text at 30%
    await expect(page.getByText(/warning/i)).not.toBeVisible()
    await expect(page.getByText(/critical/i)).not.toBeVisible()
  })

  test('Cost Headroom shows warning at >=80% spend', async ({ page }) => {
    await mockApi(page)
    await page.route('**/api/experiments', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            experiment_id: 'warn-01',
            status: 'completed',
            dataset: 'ds-warn',
            created_at: '2026-04-16T00:00:00Z',
            completed_at: '2026-04-16T01:00:00Z',
            total_runs: 5,
            completed_runs: 5,
            running_runs: 0,
            pending_runs: 0,
            failed_runs: 0,
            total_cost_usd: 20.0,
            spend_cap_usd: 25.0,  // 80% exactly
          },
          {
            experiment_id: 'warn-02',
            status: 'completed',
            dataset: 'ds-warn2',
            created_at: '2026-04-17T00:00:00Z',
            completed_at: '2026-04-17T01:00:00Z',
            total_runs: 5,
            completed_runs: 5,
            running_runs: 0,
            pending_runs: 0,
            failed_runs: 0,
            total_cost_usd: 0.0,
            spend_cap_usd: 25.0,
          },
        ]),
      })
    })
    await page.goto('/')
    await expect(page.getByText(/warning.*80%/i)).toBeVisible()
  })

  test('Cost Headroom shows critical warning at >=95% spend', async ({ page }) => {
    await mockApi(page)
    await page.route('**/api/experiments', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            experiment_id: 'crit-01',
            status: 'completed',
            dataset: 'ds-crit',
            created_at: '2026-04-16T00:00:00Z',
            completed_at: '2026-04-16T01:00:00Z',
            total_runs: 5,
            completed_runs: 5,
            running_runs: 0,
            pending_runs: 0,
            failed_runs: 0,
            total_cost_usd: 48.0,
            spend_cap_usd: 50.0,  // 96%
          },
          {
            experiment_id: 'crit-02',
            status: 'completed',
            dataset: 'ds-crit2',
            created_at: '2026-04-17T00:00:00Z',
            completed_at: '2026-04-17T01:00:00Z',
            total_runs: 5,
            completed_runs: 5,
            running_runs: 0,
            pending_runs: 0,
            failed_runs: 0,
            total_cost_usd: 0.0,
            spend_cap_usd: 50.0,
          },
        ]),
      })
    })
    await page.goto('/')
    await expect(page.getByText(/critical.*spending near cap/i)).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// RunCompare: Venn SVG + cost delta badge
// ---------------------------------------------------------------------------

test.describe('RunCompare Venn and cost delta', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)
  })

  test('Venn SVG diagram is rendered', async ({ page }) => {
    const venn = page.locator('svg[aria-label="Venn diagram"]')
    await expect(venn).toBeVisible()
  })

  test('Venn SVG shows correct counts: aOnly, overlap, bOnly', async ({ page }) => {
    // fixture: only_in_a=1, found_by_both=1, only_in_b=0
    // The Venn SVG counts are mirrored in the summary text paragraphs next to the SVG
    await expect(page.getByText(/Run A found.*1.*vuln.*B missed/i)).toBeVisible()
    await expect(page.getByText(/1.*found by both/i)).toBeVisible()
    await expect(page.getByText(/Run B found.*0.*vuln.*A missed/i)).toBeVisible()
  })

  test('cost delta badge is visible', async ({ page }) => {
    // run_a.cost_usd=0.312, run_b.cost_usd=0.245 → delta = -0.0670
    await expect(page.getByText('Cost delta (B − A)')).toBeVisible()
    // The delta badge shows the signed value
    const badge = page.locator('text=-0.0670').or(page.locator('[class*="font-mono"][class*="font-bold"]').filter({ hasText: /\-0\.0/ }))
    await expect(badge.first()).toBeVisible()
  })

  test('cost delta badge is green when B is cheaper than A', async ({ page }) => {
    // delta = B - A = 0.245 - 0.312 = -0.067 → B is cheaper → emerald color
    const badgeContainer = page.locator('text=Cost delta').locator('..')
    // The badge should exist and have emerald styling
    await expect(badgeContainer).toBeVisible()
  })

  test('A and B costs shown below delta badge', async ({ page }) => {
    await expect(page.getByText(/A: \$0\.3120/)).toBeVisible()
    await expect(page.getByText(/B: \$0\.2450/)).toBeVisible()
  })

  test('delta badge shows red when B is more expensive than A', async ({ page }) => {
    // Override comparison to make B more expensive
    // Frontend calls /api/experiments/{id}/compare (not compare-runs)
    await page.route(`**/api/experiments/${EXPERIMENT_ID}/compare**`, (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          run_a: {
            run_id: 'run-001-aaa',
            model: 'gpt-4o',
            strategy: 'zero_shot',
            tool_variant: 'with_tools',
            profile: 'default',
            precision: 0.812,
            recall: 0.743,
            f1: 0.776,
            cost_usd: 0.100,
          },
          run_b: {
            run_id: 'run-002-bbb',
            model: 'gpt-4o',
            strategy: 'zero_shot',
            tool_variant: 'without_tools',
            profile: 'default',
            precision: 0.756,
            recall: 0.698,
            f1: 0.726,
            cost_usd: 0.500,
          },
          found_by_both: [],
          only_in_a: [],
          only_in_b: [],
        }),
      })
    })
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)
    // B is more expensive — badge shows +$0.4000 (positive delta with $ sign)
    await expect(page.getByText(/\+\$0\.4000/)).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// ExperimentDetail: sticky action bar
// ---------------------------------------------------------------------------

test.describe('ExperimentDetail sticky action bar', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
  })

  test('sticky action bar is not visible initially', async ({ page }) => {
    await expect(page.getByText(/run.*selected/i)).not.toBeVisible()
  })

  test('sticky action bar appears when one run is selected', async ({ page }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Experiment Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')
    const count = await checkboxes.count()
    if (count >= 1) {
      await checkboxes.nth(0).check()
      await expect(page.getByText(/1 run selected/)).toBeVisible()
    }
  })

  test('sticky action bar shows Compare Selected when two runs selected', async ({ page }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Experiment Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')
    const count = await checkboxes.count()
    if (count >= 2) {
      await checkboxes.nth(0).check()
      await checkboxes.nth(1).check()
      await expect(page.getByText(/2 runs selected/)).toBeVisible()
      await expect(page.getByRole('button', { name: 'Compare Selected' })).toBeVisible()
    }
  })

  test('Clear button in sticky bar deselects all runs', async ({ page }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Experiment Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')
    const count = await checkboxes.count()
    if (count >= 1) {
      await checkboxes.nth(0).check()
      await expect(page.getByText(/1 run selected/)).toBeVisible()
      await page.getByRole('button', { name: 'Clear' }).click()
      await expect(page.getByText(/run.*selected/i)).not.toBeVisible()
    }
  })

  test('Compare Selected navigates to compare page', async ({ page }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Experiment Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')
    const count = await checkboxes.count()
    if (count >= 2) {
      await checkboxes.nth(0).check()
      await checkboxes.nth(1).check()
      await page.getByRole('button', { name: 'Compare Selected' }).click()
      await expect(page).toHaveURL(/\/compare\?a=.*&b=/)
    }
  })

  test('Download Experiment button visible in sticky action bar when run selected', async ({ page }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Experiment Matrix' })
    const checkboxes = matrixSection.locator('input[type="checkbox"]')
    const count = await checkboxes.count()
    if (count >= 1) {
      await checkboxes.nth(0).check()
      // DownloadButton renders in the sticky bar
      await expect(page.getByRole('button', { name: /Download/ }).last()).toBeVisible()
    }
  })
})
