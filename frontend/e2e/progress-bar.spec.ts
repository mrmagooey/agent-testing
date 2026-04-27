import { test, expect } from '@playwright/test'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { mockApi } from './helpers/mockApi'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const EXP_A = 'aaaaaaaa-0001-0001-0001-000000000001'
const EXP_B = 'bbbbbbbb-0002-0002-0002-000000000002'

async function overrideExperiment(
  page: import('@playwright/test').Page,
  id: string,
  partial: Record<string, unknown>,
) {
  const base = JSON.parse(readFileSync(join(__dirname, 'fixtures/experiments.json'), 'utf-8')) as Array<
    Record<string, unknown>
  >
  const baseExp = base.find((e) => e.experiment_id === id) ?? base[0]
  const merged = { ...baseExp, ...partial }
  await page.route(`**/api/experiments/${id}*`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const url = new URL(route.request().url())
    if (!url.pathname.endsWith(`/${id}`)) return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(merged),
    })
  })
}

test.describe('ProgressBar on ExperimentDetail', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  test('completed experiment renders 100%', async ({ page }) => {
    await page.goto(`/experiments/${EXP_A}`)
    // The ProgressBar percentage span — scoped tightly to avoid TokenMeter confusion
    const pctSpan = page.locator('span.w-12.text-right')
    await expect(pctSpan).toHaveText('100%')

    const legend = page.locator('div.space-y-2')
    await expect(legend.getByText(/^40 completed$/)).toBeVisible()
    await expect(legend.getByText(/^0 running$/)).toBeVisible()
    await expect(legend.getByText(/^0 pending$/)).toBeVisible()
    await expect(legend.getByText(/^40 total$/)).toBeVisible()
    // failed legend entry must not appear when failed === 0
    await expect(legend.getByText(/\bfailed\b/)).toHaveCount(0)
  })

  test('running experiment renders progress percentage', async ({ page }) => {
    await page.goto(`/experiments/${EXP_B}`)
    const pctSpan = page.locator('span.w-12.text-right')
    // 15/40 = 37.5% → rounds to 38
    await expect(pctSpan).toHaveText('38%')

    const legend = page.locator('div.space-y-2')
    await expect(legend.getByText(/^15 completed$/)).toBeVisible()
    await expect(legend.getByText(/^5 running$/)).toBeVisible()
    await expect(legend.getByText(/^20 pending$/)).toBeVisible()
    await expect(legend.getByText(/^40 total$/)).toBeVisible()
  })

  test('failed legend entry appears when failed > 0', async ({ page }) => {
    await overrideExperiment(page, EXP_B, {
      failed_runs: 3,
      completed_runs: 12,
      running_runs: 5,
      pending_runs: 20,
      total_runs: 40,
    })
    await page.goto(`/experiments/${EXP_B}`)

    const legend = page.locator('div.space-y-2')
    await expect(legend.getByText(/^3 failed$/)).toBeVisible()
    // red dot is the sibling span inside the failed legend entry
    const failedEntry = legend.locator('span.flex.items-center').filter({ hasText: /^3 failed$/ })
    const redDot = failedEntry.locator('span.bg-red-500')
    await expect(redDot).toBeVisible()
  })

  test('failed legend absent when failed === 0', async ({ page }) => {
    await page.goto(`/experiments/${EXP_A}`)
    const legend = page.locator('div.space-y-2')
    // No "failed" text at all in the legend area
    await expect(legend.getByText(/\bfailed\b/)).toHaveCount(0)
  })

  test('completed bar segment width is 100.0% for fully-completed experiment', async ({ page }) => {
    await page.goto(`/experiments/${EXP_A}`)
    // 40/40 → pct(40) = "100.0%"
    const greenSegment = page.locator('div.bg-green-500.h-full')
    await expect(greenSegment).toBeVisible()
    const style = await greenSegment.getAttribute('style')
    // Browsers may normalize "100.0%" to "100%" in the computed style attribute
    expect(style).toMatch(/width:\s*100(\.0)?%/)
  })

  test('running segment has animate-pulse class when running > 0', async ({ page }) => {
    await page.goto(`/experiments/${EXP_B}`)
    const bar = page.locator('div.flex-1.h-4.rounded-full')
    const blueSegment = bar.locator('div.bg-blue-500.animate-pulse')
    await expect(blueSegment).toBeVisible()
    await expect(blueSegment).toHaveClass(/animate-pulse/)
  })

  test('bar renders only segments with count > 0', async ({ page }) => {
    await overrideExperiment(page, EXP_A, {
      completed_runs: 10,
      running_runs: 0,
      pending_runs: 0,
      failed_runs: 0,
      total_runs: 10,
    })
    await page.goto(`/experiments/${EXP_A}`)
    const bar = page.locator('div.flex-1.h-4.rounded-full')
    await expect(bar).toBeVisible()
    // Only segments with a style width are real segments (not the background track)
    const segments = bar.locator('div[style*="width"]')
    await expect(segments).toHaveCount(1)
  })

  test('total === 0 renders no progress bar', async ({ page }) => {
    await overrideExperiment(page, EXP_A, {
      total_runs: 0,
      completed_runs: 0,
      running_runs: 0,
      pending_runs: 0,
      failed_runs: 0,
    })
    await page.goto(`/experiments/${EXP_A}`)
    // Page still renders (experiment header present)
    await expect(page.getByRole('heading', { name: EXP_A })).toBeVisible()
    // The bar track element is absent
    await expect(page.locator('div.h-4.rounded-full')).toHaveCount(0)
    // No percentage display from the progress bar
    await expect(page.locator('span.w-12.text-right')).toHaveCount(0)
  })

  test('bar segment title attributes expose tooltip text', async ({ page }) => {
    await page.goto(`/experiments/${EXP_B}`)
    const bar = page.locator('div.flex-1.h-4.rounded-full')
    const greenSegment = bar.locator('div.bg-green-500')
    await expect(greenSegment).toHaveAttribute('title', '15 completed')
    const blueSegment = bar.locator('div.bg-blue-500')
    await expect(blueSegment).toHaveAttribute('title', '5 running')
  })
})
