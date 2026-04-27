import { test, expect } from '@playwright/test'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { mockApi } from './helpers/mockApi'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const EXPERIMENT_A = 'aaaaaaaa-0001-0001-0001-000000000001'
const EXPERIMENT_C = 'cccccccc-0003-0003-0003-000000000003'

async function overrideExperiment(
  page: Parameters<typeof mockApi>[0],
  partial: Record<string, unknown>,
) {
  const base = JSON.parse(
    readFileSync(join(__dirname, 'fixtures/experiments.json'), 'utf-8'),
  ) as Array<Record<string, unknown>>
  const baseA = base.find((e) => e.experiment_id === EXPERIMENT_A) ?? base[0]
  const merged = { ...baseA, ...partial }
  await page.route(`**/api/experiments/${EXPERIMENT_A}*`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const url = new URL(route.request().url())
    // Only intercept the experiment-summary endpoint, not the /results sub-resource
    if (!url.pathname.endsWith(`/${EXPERIMENT_A}`)) return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(merged),
    })
  })
}

test.describe('ExperimentDetail cost/cap warning', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  test('cost is rendered with toFixed(2) format (fixture A)', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_A}`)
    await expect(page.getByText('Cost:')).toBeVisible()
    await expect(page.getByText('$12.45')).toBeVisible()
  })

  test('cap is rendered when spend_cap_usd is truthy (fixture A)', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_A}`)
    await expect(page.getByText('Cap:')).toBeVisible()
    await expect(page.getByText('$50.00')).toBeVisible()
  })

  test('no "Near cap" warning when ratio is well below 0.8 (fixture A: ratio 0.249)', async ({
    page,
  }) => {
    await page.goto(`/experiments/${EXPERIMENT_A}`)
    await expect(page.getByText(/Near cap/)).toHaveCount(0)
  })

  test('cap section absent when spend_cap_usd is null (fixture C)', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_C}`)
    await expect(page.getByText('Cost:')).toBeVisible()
    await expect(page.getByText('Cap:')).toHaveCount(0)
    await expect(page.getByText(/Near cap/)).toHaveCount(0)
  })

  test('"Near cap" warning shown when ratio > 0.8 (45/50 = 0.9)', async ({ page }) => {
    await overrideExperiment(page, { total_cost_usd: 45.0, spend_cap_usd: 50.0 })
    await page.goto(`/experiments/${EXPERIMENT_A}`)
    await expect(page.getByText(/Near cap/)).toBeVisible()
    await expect(page.getByText('Cap:')).toBeVisible()
    await expect(page.getByText('$50.00')).toBeVisible()
  })

  test('no "Near cap" warning at exactly ratio 0.8 (strict inequality > 0.8)', async ({
    page,
  }) => {
    // 40/50 = 0.8 exactly; the condition is total_cost_usd / spend_cap_usd > 0.8
    // (strict), so 0.8 must NOT trigger the warning
    await overrideExperiment(page, { total_cost_usd: 40.0, spend_cap_usd: 50.0 })
    await page.goto(`/experiments/${EXPERIMENT_A}`)
    await expect(page.getByText('Cost:')).toBeVisible()
    await expect(page.getByText('Cap:')).toBeVisible()
    await expect(page.getByText(/Near cap/)).toHaveCount(0)
  })

  test('"Near cap" warning shown at very high ratio (49/50 = 0.98)', async ({ page }) => {
    await overrideExperiment(page, { total_cost_usd: 49.0, spend_cap_usd: 50.0 })
    await page.goto(`/experiments/${EXPERIMENT_A}`)
    await expect(page.getByText(/Near cap/)).toBeVisible()
  })

  test('warning element has orange color class when ratio > 0.8 (45/50 = 0.9)', async ({
    page,
  }) => {
    await overrideExperiment(page, { total_cost_usd: 45.0, spend_cap_usd: 50.0 })
    await page.goto(`/experiments/${EXPERIMENT_A}`)
    const warning = page.getByText(/Near cap/)
    await expect(warning).toBeVisible()
    const className = await warning.getAttribute('class')
    expect(className).toMatch(/text-orange-600|text-orange-400/)
  })
})
