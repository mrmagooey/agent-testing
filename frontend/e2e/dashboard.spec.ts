import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('shows page heading', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
})

test('shows active experiments section', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /Active Experiments/ })).toBeVisible()
})

test('shows running experiment in active experiments table', async ({ page }) => {
  await page.goto('/')
  const activeSection = page.locator('section').filter({ hasText: 'Active Experiments' })
  await expect(activeSection.getByText('bbbbbbbb')).toBeVisible()
  await expect(activeSection.getByText('cve-2024-js')).toBeVisible()
})

test('shows completed experiments in recent experiments section', async ({ page }) => {
  await page.goto('/')
  const recentSection = page.locator('section').filter({ hasText: 'Recent Experiments' })
  await expect(recentSection.getByText('aaaaaaaa')).toBeVisible()
  await expect(recentSection.getByText('cve-2024-python')).toBeVisible()
})

test('shows smoke test section', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Smoke Test' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run Smoke Test' })).toBeVisible()
})

test('smoke test success shows link to experiment', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Run Smoke Test' }).click()
  await expect(page.getByText('Smoke test experiment created with 1 run.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'View experiment →' })).toBeVisible()
})

test('new experiment button navigates to /experiments/new', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New Experiment' }).click()
  await expect(page).toHaveURL('/experiments/new')
})

test('clicking active experiment row navigates to experiment detail', async ({ page }) => {
  await page.goto('/')
  const activeSection = page.locator('section').filter({ hasText: 'Active Experiments' })
  await activeSection.locator('tbody tr').first().click()
  await expect(page).toHaveURL(/\/experiments\/bbbbbbbb/)
})

test('clicking completed experiment row navigates to experiment detail', async ({ page }) => {
  await page.goto('/')
  const recentSection = page.locator('section').filter({ hasText: 'Recent Experiments' })
  await recentSection.locator('tbody tr').first().click()
  await expect(page).toHaveURL(/\/experiments\//)
})

test('shows empty active experiments message when no active experiments', async ({ page }) => {
  await page.route('**/api/experiments', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })
  await page.goto('/')
  await expect(page.getByText('No active experiments.')).toBeVisible()
  await expect(page.getByText('No completed experiments yet.')).toBeVisible()
})

test('shows system health section', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'System Health' })).toBeVisible()
})

test('shows model x strategy accuracy heatmap', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Model × Strategy Accuracy' })).toBeVisible()
  await expect(page.getByTestId('accuracy-heatmap')).toBeVisible()
})

test('active experiments table has separate Cost and Status header cells', async ({ page }) => {
  await page.goto('/')
  const activeSection = page.locator('section').filter({ hasText: 'Active Experiments' })
  const headerCells = activeSection.locator('thead th')

  // Verify we have 6 header cells
  await expect(headerCells).toHaveCount(6)

  // Find Cost and Status cells specifically
  const allHeaders = await headerCells.allTextContents()
  const costIndex = allHeaders.findIndex(text => text.includes('Cost'))
  const statusIndex = allHeaders.findIndex(text => text.includes('Status'))

  // Both should exist
  expect(costIndex).toBeGreaterThanOrEqual(0)
  expect(statusIndex).toBeGreaterThanOrEqual(0)

  // They should be in different cells (Cost at index 3, Status at index 4)
  expect(costIndex).toBe(3)
  expect(statusIndex).toBe(4)

  // Verify header text contains them as separate words (CSS uppercases the display text).
  const headerText = await activeSection.locator('thead').innerText()
  expect(headerText).toMatch(/Cost\s+Status/i)
})
