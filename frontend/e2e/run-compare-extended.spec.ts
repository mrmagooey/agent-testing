/**
 * Extended RunCompare tests covering gaps from the audit:
 * - DatasetMismatchBanner multi-warning rendering
 * - Cost delta badge green/red color class
 * - VennDiagram SVG count text (number text nodes inside SVG)
 * - DeltaBadge colors for precision/recall (green when B>A, red when B<A)
 * - RunPicker URL state persistence across simulated refresh
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_A = 'run-001-aaa'
const RUN_B = 'run-002-bbb'

// Fixture: run_a.cost_usd=0.312, run_b.cost_usd=0.245 → diff = -0.0670 (B cheaper → emerald)
// run_a.precision=0.812, run_b.precision=0.756 → diff = -0.056 (B worse → red)
// run_a.recall=0.743, run_b.recall=0.698 → diff = -0.045 (B worse → red)

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// DatasetMismatchBanner
// ---------------------------------------------------------------------------

test.describe('DatasetMismatchBanner', () => {
  test('banner is NOT shown when dataset_mismatch is false', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)
    // Default fixture has no dataset_mismatch field → should not show banner
    await expect(page.getByTestId('dataset-mismatch-banner')).not.toBeVisible()
  })

  test('banner shows "Dataset mismatch" heading when dataset_mismatch=true', async ({ page }) => {
    await page.route('**/api/compare-runs**', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          run_a: {
            run_id: 'run-001-aaa',
            experiment_id: EXPERIMENT_ID,
            model: 'gpt-4o',
            strategy: 'zero_shot',
            tool_variant: 'with_tools',
            profile: 'default',
            precision: 0.812,
            recall: 0.743,
            f1: 0.776,
            cost_usd: 0.312,
            dataset: 'cve-2024-python',
          },
          run_b: {
            run_id: 'run-002-bbb',
            experiment_id: EXPERIMENT_ID,
            model: 'gpt-4o',
            strategy: 'zero_shot',
            tool_variant: 'without_tools',
            profile: 'default',
            precision: 0.756,
            recall: 0.698,
            f1: 0.726,
            cost_usd: 0.245,
            dataset: 'cve-2024-js',
          },
          found_by_both: [],
          only_in_a: [],
          only_in_b: [],
          dataset_mismatch: true,
          warnings: ['Run A used cve-2024-python; Run B used cve-2024-js'],
        }),
      })
    })
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)
    await expect(page.getByTestId('dataset-mismatch-banner')).toBeVisible()
    await expect(page.getByText('Dataset mismatch')).toBeVisible()
  })

  test('banner renders all warning strings when multiple warnings present', async ({ page }) => {
    await page.route('**/api/compare-runs**', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          run_a: {
            run_id: 'run-001-aaa',
            experiment_id: EXPERIMENT_ID,
            model: 'gpt-4o',
            strategy: 'zero_shot',
            tool_variant: 'with_tools',
            profile: 'default',
            precision: 0.812,
            recall: 0.743,
            f1: 0.776,
            cost_usd: 0.312,
          },
          run_b: {
            run_id: 'run-002-bbb',
            experiment_id: EXPERIMENT_ID,
            model: 'gpt-4o',
            strategy: 'zero_shot',
            tool_variant: 'without_tools',
            profile: 'default',
            precision: 0.756,
            recall: 0.698,
            f1: 0.726,
            cost_usd: 0.245,
          },
          found_by_both: [],
          only_in_a: [],
          only_in_b: [],
          dataset_mismatch: true,
          warnings: ['Run A used dataset X; Run B used dataset Y', 'Run A had 14 labels; Run B had 9 labels'],
        }),
      })
    })
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)

    const banner = page.getByTestId('dataset-mismatch-banner')
    await expect(banner).toBeVisible()
    await expect(banner.getByText('Run A used dataset X; Run B used dataset Y')).toBeVisible()
    await expect(banner.getByText('Run A had 14 labels; Run B had 9 labels')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// VennDiagram SVG count text
// ---------------------------------------------------------------------------

test.describe('VennDiagram SVG count text', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)
  })

  test('SVG renders with aria-label "Venn diagram"', async ({ page }) => {
    await expect(page.locator('svg[aria-label="Venn diagram"]')).toBeVisible()
  })

  test('SVG shows count "1" for only_in_a (left circle)', async ({ page }) => {
    // fixture: only_in_a has 1 finding → left text = 1
    const svg = page.locator('svg[aria-label="Venn diagram"]')
    // The first <text> element shows aOnly count
    const texts = svg.locator('text')
    // First text element is aOnly=1
    await expect(texts.first()).toContainText('1')
  })

  test('SVG shows count "1" for found_by_both (overlap)', async ({ page }) => {
    // fixture: found_by_both has 1 finding → overlap text = 1
    const svg = page.locator('svg[aria-label="Venn diagram"]')
    const texts = svg.locator('text')
    // Second text element is overlap=1
    await expect(texts.nth(1)).toContainText('1')
  })

  test('SVG shows count "0" for only_in_b (right circle)', async ({ page }) => {
    // fixture: only_in_b is [] → right text = 0
    const svg = page.locator('svg[aria-label="Venn diagram"]')
    const texts = svg.locator('text')
    // Third text element is bOnly=0
    await expect(texts.nth(2)).toContainText('0')
  })
})

// ---------------------------------------------------------------------------
// DeltaBadge colors for precision / recall
// ---------------------------------------------------------------------------

test.describe('DeltaBadge colors', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)
  })

  test('precision DeltaBadge is red (B worse than A)', async ({ page }) => {
    // precision: A=0.812, B=0.756 → delta=-0.056 → red badge
    // The badge shows "-0.056"
    const badge = page.locator('span.font-mono').filter({ hasText: /\-0\.0/ }).first()
    await expect(badge).toBeVisible()
    await expect(badge).toHaveClass(/text-red/)
  })

  test('cost delta badge is emerald (B cheaper than A)', async ({ page }) => {
    // cost: A=0.312, B=0.245 → delta=-0.0670 → emerald badge
    const costBadge = page.locator('text=Cost delta (B − A)').locator('..')
    const badge = costBadge.locator('[class*="emerald"]')
    await expect(badge).toBeVisible()
  })

  test('cost delta badge shows red when B is more expensive', async ({ page }) => {
    await page.route('**/api/compare-runs**', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          run_a: {
            run_id: 'run-001-aaa',
            experiment_id: EXPERIMENT_ID,
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
            experiment_id: EXPERIMENT_ID,
            model: 'gpt-4o',
            strategy: 'zero_shot',
            tool_variant: 'without_tools',
            profile: 'default',
            precision: 0.900,
            recall: 0.850,
            f1: 0.874,
            cost_usd: 0.500,
          },
          found_by_both: [],
          only_in_a: [],
          only_in_b: [],
        }),
      })
    })
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)

    // B is more expensive: delta = +$0.4000 → red badge
    const redBadge = page.locator('[class*="red"]').filter({ hasText: /\+\$/ }).first()
    await expect(redBadge).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// RunPicker URL state persistence
// ---------------------------------------------------------------------------

test.describe('RunPicker URL state persistence', () => {
  test('URL params a= and b= are present on legacy route compare page', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)

    // Comparison should load because URL has run IDs — check for a heading
    await expect(page.getByRole('heading', { name: 'Run A' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Run B' })).toBeVisible()

    // URL still has the params
    const url = new URL(page.url())
    expect(url.searchParams.get('a')).toBe(RUN_A)
    expect(url.searchParams.get('b')).toBe(RUN_B)
  })

  test('navigating to cross-experiment /compare?a_experiment=&a_run=&b_experiment=&b_run= loads comparison', async ({ page }) => {
    const params = new URLSearchParams({
      a_experiment: EXPERIMENT_ID,
      a_run: RUN_A,
      b_experiment: EXPERIMENT_ID,
      b_run: RUN_B,
    })
    await page.goto(`/compare?${params}`)

    // Should load correctly — check for Run A/B headings (CardTitle)
    await expect(page.getByRole('heading', { name: 'Run A' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Run B' })).toBeVisible()
  })

  test('URL params persist after comparison loads (no redirect away)', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)

    // Verify content loaded
    await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()

    // URL still has the original params
    expect(page.url()).toContain(`a=${RUN_A}`)
    expect(page.url()).toContain(`b=${RUN_B}`)
  })
})
