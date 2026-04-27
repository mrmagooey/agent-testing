import { test, expect, Page } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

async function overrideMatrix(
  page: Page,
  body: unknown,
  opts: { delayMs?: number; status?: number } = {},
) {
  await page.route('**/api/matrix/accuracy*', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    if (opts.delayMs) await new Promise((r) => setTimeout(r, opts.delayMs))
    await route.fulfill({
      status: opts.status ?? 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })
  })
}

test.describe('AccuracyHeatmap', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  // 1. Default fixture: heatmap renders with model rows and strategy columns
  test('renders heatmap table with 4 cells for 2 models × 2 strategies', async ({ page }) => {
    await page.goto('/')

    const heatmap = page.getByTestId('accuracy-heatmap')
    await expect(heatmap).toBeVisible()

    await expect(heatmap.locator('[data-testid="heatmap-cell"]')).toHaveCount(4)

    await expect(heatmap.getByText('claude-opus-4')).toBeVisible()
    await expect(heatmap.getByText('gpt-4o')).toBeVisible()

    await expect(heatmap.getByRole('columnheader', { name: 'per_file' })).toBeVisible()
    await expect(heatmap.getByRole('columnheader', { name: 'single_agent' })).toBeVisible()
  })

  // 2. PASS signal for accuracy >= 0.8 (claude-opus-4 × single_agent = 0.912)
  test('PASS signal for accuracy >= 0.8', async ({ page }) => {
    await page.goto('/')

    await expect(page.getByTestId('accuracy-heatmap')).toBeVisible()

    const passCell = page
      .getByTestId('accuracy-heatmap')
      .locator('[data-testid="heatmap-cell"][data-signal="PASS"]')
      .filter({ hasText: '0.912' })

    await expect(passCell).toBeVisible()
    await expect(passCell.getByTestId('heatmap-cell-signal')).toHaveText('PASS')
    await expect(passCell).toHaveAttribute('data-signal', 'PASS')
  })

  // 3. WARN signal for 0.6 <= accuracy < 0.8 (gpt-4o × per_file = 0.634)
  test('WARN signal for 0.6 <= accuracy < 0.8', async ({ page }) => {
    await page.goto('/')

    await expect(page.getByTestId('accuracy-heatmap')).toBeVisible()

    const warnCell = page
      .getByTestId('accuracy-heatmap')
      .locator('[data-testid="heatmap-cell"][data-signal="WARN"]')
      .filter({ hasText: '0.634' })

    await expect(warnCell).toBeVisible()
    await expect(warnCell.getByTestId('heatmap-cell-signal')).toHaveText('WARN')
    await expect(warnCell).toHaveAttribute('data-signal', 'WARN')
  })

  // 4. FAIL signal for accuracy < 0.6 (override with accuracy 0.45)
  test('FAIL signal for accuracy < 0.6', async ({ page }) => {
    await overrideMatrix(page, {
      models: ['model-a'],
      strategies: ['strategy-x'],
      cells: [{ model: 'model-a', strategy: 'strategy-x', accuracy: 0.45, run_count: 1 }],
    })

    await page.goto('/')

    const heatmap = page.getByTestId('accuracy-heatmap')
    await expect(heatmap).toBeVisible()

    const failCell = heatmap.locator('[data-testid="heatmap-cell"][data-signal="FAIL"]')
    await expect(failCell).toBeVisible()
    await expect(failCell.getByTestId('heatmap-cell-signal')).toHaveText('FAIL')
    await expect(failCell).toHaveAttribute('data-signal', 'FAIL')
  })

  // 5. EmptyCell renders em-dash for missing model×strategy combinations
  test('EmptyCell renders em-dash for unpopulated model×strategy positions', async ({ page }) => {
    await overrideMatrix(page, {
      models: ['m1', 'm2'],
      strategies: ['s1', 's2'],
      cells: [{ model: 'm1', strategy: 's1', accuracy: 0.9, run_count: 1 }],
    })

    await page.goto('/')

    const heatmap = page.getByTestId('accuracy-heatmap')
    await expect(heatmap).toBeVisible()

    // Only the one populated cell renders as heatmap-cell
    await expect(heatmap.locator('[data-testid="heatmap-cell"]')).toHaveCount(1)

    await expect(heatmap.locator('td').filter({ hasText: '—' })).toHaveCount(3)
  })

  // 6. Empty-data state
  test('shows empty-data message when cells array is empty', async ({ page }) => {
    await overrideMatrix(page, { models: [], strategies: [], cells: [] })

    await page.goto('/')

    await expect(page.getByTestId('accuracy-heatmap')).not.toBeVisible()
    await expect(
      page.getByText('No completed runs with evaluation data yet.'),
    ).toBeVisible()
  })

  // 7. Error state
  test('shows error detail when API returns 500', async ({ page }) => {
    await overrideMatrix(
      page,
      { detail: 'Matrix worker unavailable' },
      { status: 500 },
    )

    await page.goto('/')

    await expect(page.getByTestId('accuracy-heatmap')).not.toBeVisible()
    await expect(page.getByText('Matrix worker unavailable')).toBeVisible()
  })

  // 8. Loading state
  test('shows animate-pulse skeleton while fetch is in flight, then renders heatmap', async ({
    page,
  }) => {
    await overrideMatrix(
      page,
      {
        models: ['claude-opus-4', 'gpt-4o'],
        strategies: ['per_file', 'single_agent'],
        cells: [
          { model: 'claude-opus-4', strategy: 'per_file', accuracy: 0.851, run_count: 2 },
          { model: 'claude-opus-4', strategy: 'single_agent', accuracy: 0.912, run_count: 3 },
          { model: 'gpt-4o', strategy: 'per_file', accuracy: 0.634, run_count: 2 },
          { model: 'gpt-4o', strategy: 'single_agent', accuracy: 0.743, run_count: 3 },
        ],
      },
      { delayMs: 2000 },
    )

    await page.goto('/', { waitUntil: 'domcontentloaded' })

    // While fetch is in flight the heatmap must not yet be present
    await expect(page.getByTestId('accuracy-heatmap')).not.toBeVisible()

    // The AccuracyHeatmap loading skeleton uses both classes; scope to that exact pair
    await expect(page.locator('.animate-pulse.h-24')).toBeVisible()

    // After the delayed response resolves, the heatmap appears
    await expect(page.getByTestId('accuracy-heatmap')).toBeVisible({ timeout: 6000 })
  })

  // 9. Footer explanatory text
  test('footer shows accuracy formula and amber ramp explanation', async ({ page }) => {
    await page.goto('/')

    await expect(page.getByText(/Accuracy = recall/)).toBeVisible()
    await expect(page.getByText(/Amber ramp/)).toBeVisible()
  })
})
