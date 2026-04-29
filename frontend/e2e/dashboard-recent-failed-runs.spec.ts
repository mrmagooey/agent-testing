import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('annotation visible for experiment with failures', async ({ page }) => {
  await page.goto('/')
  const recentSection = page.locator('section').filter({ hasText: 'Recent Experiments' })
  const row = recentSection.locator('tr').filter({ hasText: 'cccccccc' })
  await expect(row.getByTestId('dashboard-recent-failed-count')).toBeVisible()
  await expect(row.getByTestId('dashboard-recent-failed-count')).toContainText('2 failed')
})

test('annotation absent for experiment with zero failures', async ({ page }) => {
  await page.goto('/')
  const recentSection = page.locator('section').filter({ hasText: 'Recent Experiments' })
  const row = recentSection.locator('tr').filter({ hasText: 'aaaaaaaa' })
  await expect(row.getByTestId('dashboard-recent-failed-count')).toHaveCount(0)
})

test('annotation has the descriptive title attribute', async ({ page }) => {
  await page.goto('/')
  const recentSection = page.locator('section').filter({ hasText: 'Recent Experiments' })
  const row = recentSection.locator('tr').filter({ hasText: 'cccccccc' })
  await expect(row.getByTestId('dashboard-recent-failed-count')).toHaveAttribute('title', '2 of 40 runs failed')
})

test('total runs still rendered alongside the annotation', async ({ page }) => {
  await page.goto('/')
  const recentSection = page.locator('section').filter({ hasText: 'Recent Experiments' })
  const row = recentSection.locator('tr').filter({ hasText: 'cccccccc' })
  const runsCell = row.locator('td').filter({ hasText: /40/ })
  await expect(runsCell).toContainText('40')
})

test('annotation uses the danger color class', async ({ page }) => {
  await page.goto('/')
  const recentSection = page.locator('section').filter({ hasText: 'Recent Experiments' })
  const row = recentSection.locator('tr').filter({ hasText: 'cccccccc' })
  await expect(row.getByTestId('dashboard-recent-failed-count')).toHaveClass(/text-signal-danger/)
})
