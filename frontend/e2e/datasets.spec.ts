import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto('/datasets')
})

test('shows page heading', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Datasets' })).toBeVisible()
})

test('shows discover CVEs button', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Discover CVEs' })).toBeVisible()
})

test('clicking discover CVEs navigates to /datasets/discover', async ({ page }) => {
  await page.getByRole('button', { name: 'Discover CVEs' }).click()
  await expect(page).toHaveURL('/datasets/discover')
})

test('shows table columns', async ({ page }) => {
  await expect(page.getByRole('columnheader', { name: 'Name' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Source' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Labels' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Files' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Size' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Languages' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Created' })).toBeVisible()
})

test('shows dataset names from API', async ({ page }) => {
  await expect(page.getByText('cve-2024-python')).toBeVisible()
  await expect(page.getByText('cve-2024-js')).toBeVisible()
  await expect(page.getByText('injected-java-2024')).toBeVisible()
})

test('shows source badges', async ({ page }) => {
  const cveBadges = page.locator('span').filter({ hasText: 'cve' })
  await expect(cveBadges.first()).toBeVisible()
  await expect(page.locator('span').filter({ hasText: 'injected' })).toBeVisible()
})

test('shows label counts', async ({ page }) => {
  await expect(page.getByText('14')).toBeVisible()
  await expect(page.getByText('9')).toBeVisible()
})

test('shows language list', async ({ page }) => {
  await expect(page.getByText('python').first()).toBeVisible()
  await expect(page.getByText('javascript, typescript')).toBeVisible()
})

test('clicking dataset row navigates to detail page', async ({ page }) => {
  await page.locator('tbody tr').first().click()
  await expect(page).toHaveURL(/\/datasets\//)
})

test('shows empty state when no datasets', async ({ page }) => {
  await page.route('**/api/datasets', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })
  await page.goto('/datasets')
  await expect(page.getByText('No datasets found. Use CVE Discovery to import one.')).toBeVisible()
})
