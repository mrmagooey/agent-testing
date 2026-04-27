import { test, expect } from '@playwright/test'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { mockApi } from './helpers/mockApi'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

async function overrideExperiments(page: Parameters<typeof mockApi>[0], partials: unknown[]) {
  await page.route('**/api/experiments', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(partials),
    })
  })
}

const BASE_FIXTURE = JSON.parse(
  readFileSync(join(__dirname, 'fixtures/experiments.json'), 'utf-8'),
) as unknown[]

const EXPERIMENT_A = BASE_FIXTURE[0] as Record<string, unknown>
const EXPERIMENT_B = BASE_FIXTURE[1] as Record<string, unknown>

test.describe('Dashboard Trends sparkline', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  test('Trends section heading is visible when 2 completed experiments exist', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByRole('heading', { name: /Trends/ })).toBeVisible({ timeout: 5000 })
  })

  test('Cost per experiment label is visible alongside the sparkline', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByText('Cost per experiment (USD)')).toBeVisible({ timeout: 5000 })
  })

  test('SparklineChart renders an SVG with a recharts line path', async ({ page }) => {
    await page.goto('/')
    const container = page.locator('.recharts-responsive-container').first()
    await expect(container).toBeVisible({ timeout: 5000 })
    const surface = container.locator('svg.recharts-surface')
    await expect(surface).toBeVisible({ timeout: 5000 })
    const linePath = surface.locator('path.recharts-curve')
    await expect(linePath).toHaveCount(1, { timeout: 5000 })
  })

  test('Trends section is hidden when only 1 completed experiment exists', async ({ page }) => {
    await overrideExperiments(page, [EXPERIMENT_A])
    await page.goto('/')
    await expect(page.getByRole('heading', { name: /Trends/ })).not.toBeVisible()
    await expect(page.getByText('Cost per experiment (USD)')).not.toBeVisible()
  })

  test('Trends section is hidden when no completed experiments exist', async ({ page }) => {
    await overrideExperiments(page, [EXPERIMENT_B])
    await page.goto('/')
    await expect(page.getByRole('heading', { name: /Trends/ })).not.toBeVisible()
    await expect(page.getByText('Cost per experiment (USD)')).not.toBeVisible()
  })

  test('CostHeadroomCard renders inside the same section as the sparkline', async ({ page }) => {
    await page.goto('/')
    const trendsSection = page.locator('section').filter({ has: page.getByRole('heading', { name: /Trends/ }) })
    await expect(trendsSection.getByText('Cost Headroom')).toBeVisible({ timeout: 5000 })
  })

  test('XAxis and YAxis are not rendered in the sparkline', async ({ page }) => {
    await page.goto('/')
    const sparkline = page.locator('section')
      .filter({ has: page.getByRole('heading', { name: /Trends/ }) })
      .locator('.recharts-responsive-container')
    await expect(sparkline).toBeVisible({ timeout: 5000 })
    await expect(sparkline.locator('.recharts-xAxis')).toHaveCount(0)
    await expect(sparkline.locator('.recharts-yAxis')).toHaveCount(0)
  })

  test('sparkline line stroke is the amber accent color #F5A524', async ({ page }) => {
    await page.goto('/')
    const linePath = page.locator('.recharts-responsive-container').first().locator('path.recharts-curve')
    await expect(linePath).toBeVisible({ timeout: 5000 })
    const stroke = await linePath.getAttribute('stroke')
    expect(stroke).toMatch(/^#f5a524$/i)
  })
})
