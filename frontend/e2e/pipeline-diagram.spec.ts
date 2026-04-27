import { test, expect } from '@playwright/test'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { mockApi } from './helpers/mockApi'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

async function noActiveExperiments(page: Parameters<typeof mockApi>[0]) {
  await page.route('**/api/experiments', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const base = JSON.parse(readFileSync(join(__dirname, 'fixtures/experiments.json'), 'utf-8'))
    const completedOnly = base.filter((e: { status: string }) => e.status === 'completed')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(completedOnly),
    })
  })
}

test.describe('PipelineDiagram on Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  test('PipelineDiagram is hidden when a running experiment exists', async ({ page }) => {
    await page.goto('/')
    await expect(
      page.getByText("No experiments running — here's what happens when you start one."),
    ).not.toBeVisible()
  })

  test('PipelineDiagram renders when no experiments are active', async ({ page }) => {
    await noActiveExperiments(page)
    await page.goto('/')
    await expect(page.getByText('// Experiment pipeline')).toBeVisible()
    await expect(
      page.getByText("No experiments running — here's what happens when you start one."),
    ).toBeVisible()
  })

  test('all 5 stage labels render in document order', async ({ page }) => {
    await noActiveExperiments(page)
    await page.goto('/')

    const expectedLabels = [
      'Configure',
      'Expand Matrix',
      'Schedule',
      'Execute',
      'Aggregate & Report',
    ]

    for (const label of expectedLabels) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible()
    }

    const stageCards = page.locator('[data-stage-index]')
    await expect(stageCards).toHaveCount(5)

    for (let i = 0; i < expectedLabels.length; i++) {
      const card = stageCards.nth(i)
      await expect(card).toHaveAttribute('data-stage-index', String(i))
      await expect(card.getByText(expectedLabels[i], { exact: true })).toBeVisible()
    }
  })

  test('each stage shows its description text', async ({ page }) => {
    await noActiveExperiments(page)
    await page.goto('/')

    const descriptions = [
      'Pick models, strategies, dimensions',
      'Cartesian product → N runs',
      'Queue K8s Jobs respecting concurrency caps',
      'Workers review code in parallel',
      'Findings indexed, matrix report rendered',
    ]

    for (const desc of descriptions) {
      await expect(page.getByText(desc, { exact: true })).toBeVisible()
    }
  })

  test('Configure stage is a link to /experiments/new', async ({ page }) => {
    await noActiveExperiments(page)
    await page.goto('/')

    const configureLink = page.getByRole('link', {
      name: 'Configure: Pick models, strategies, dimensions',
    })
    await expect(configureLink).toBeVisible()
    const href = await configureLink.getAttribute('href')
    expect(href).toMatch(/\/experiments\/new$/)
  })

  test('non-Configure stages are not links', async ({ page }) => {
    await noActiveExperiments(page)
    await page.goto('/')

    const nonLinkLabels = ['Expand Matrix', 'Schedule', 'Execute', 'Aggregate & Report']
    for (const label of nonLinkLabels) {
      await expect(
        page.getByRole('link', { name: new RegExp(label.replace('&', '\\&')) }),
      ).toHaveCount(0)
    }
  })

  test('clicking Configure navigates to /experiments/new', async ({ page }) => {
    await noActiveExperiments(page)
    await page.goto('/')

    const configureLink = page.getByRole('link', {
      name: 'Configure: Pick models, strategies, dimensions',
    })
    await configureLink.click()
    await expect(page).toHaveURL('/experiments/new')
    await expect(page.getByRole('heading', { name: /New Experiment/i })).toBeVisible()
  })

  test('4 arrows render between the 5 stages (not 5)', async ({ page }) => {
    await noActiveExperiments(page)
    await page.goto('/')

    const arrowRight = page.locator('svg.lucide-arrow-right')
    const arrowDown = page.locator('svg.lucide-arrow-down')

    await expect(arrowRight).toHaveCount(4)
    await expect(arrowDown).toHaveCount(4)
  })
})
