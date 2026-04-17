import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('shows page heading', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
})

test('shows active batches section', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Active Batches' })).toBeVisible()
})

test('shows running batch in active batches table', async ({ page }) => {
  await page.goto('/')
  const activeSection = page.locator('section').filter({ hasText: 'Active Batches' })
  await expect(activeSection.getByText('bbbbbbbb')).toBeVisible()
  await expect(activeSection.getByText('cve-2024-js')).toBeVisible()
})

test('shows completed batches in recent batches section', async ({ page }) => {
  await page.goto('/')
  const recentSection = page.locator('section').filter({ hasText: 'Recent Batches' })
  await expect(recentSection.getByText('aaaaaaaa')).toBeVisible()
  await expect(recentSection.getByText('cve-2024-python')).toBeVisible()
})

test('shows smoke test section', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Smoke Test' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run Smoke Test' })).toBeVisible()
})

test('smoke test success shows link to batch', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Run Smoke Test' }).click()
  await expect(page.getByText('Smoke test batch created with 1 run.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'View batch' })).toBeVisible()
})

test('new batch button navigates to /batches/new', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New Batch' }).click()
  await expect(page).toHaveURL('/batches/new')
})

test('clicking active batch row navigates to batch detail', async ({ page }) => {
  await page.goto('/')
  const activeSection = page.locator('section').filter({ hasText: 'Active Batches' })
  await activeSection.locator('tbody tr').first().click()
  await expect(page).toHaveURL(/\/batches\/bbbbbbbb/)
})

test('clicking completed batch row navigates to batch detail', async ({ page }) => {
  await page.goto('/')
  const recentSection = page.locator('section').filter({ hasText: 'Recent Batches' })
  await recentSection.locator('tbody tr').first().click()
  await expect(page).toHaveURL(/\/batches\//)
})

test('shows empty active batches message when no active batches', async ({ page }) => {
  await page.route('**/api/batches', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })
  await page.goto('/')
  await expect(page.getByText('No active batches.')).toBeVisible()
  await expect(page.getByText('No completed batches yet.')).toBeVisible()
})

test('shows system health section', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'System Health' })).toBeVisible()
})
