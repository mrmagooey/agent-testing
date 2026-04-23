import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto('/findings')
})

test('shows page heading', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Findings' })).toBeVisible()
})

test('does not show error state', async ({ page }) => {
  await expect(page.getByText('Failed to load findings')).not.toBeVisible()
})

test('shows finding title from mock data', async ({ page }) => {
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
})

test('shows multiple finding rows', async ({ page }) => {
  await expect(page.getByText('Reflected XSS in search results')).toBeVisible()
  await expect(page.getByText('Path traversal in file download endpoint')).toBeVisible()
})

test('shows table column headers', async ({ page }) => {
  await expect(page.getByRole('columnheader', { name: 'Status' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Title' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Severity' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Vuln Class' })).toBeVisible()
})

test('shows total findings count', async ({ page }) => {
  await expect(page.getByText('3 findings')).toBeVisible()
})

test('no /findings requests result in 500 errors', async ({ page }) => {
  const failedRequests: string[] = []

  page.on('response', (response) => {
    if (response.url().includes('/findings') && response.status() >= 500) {
      failedRequests.push(`${response.status()} ${response.url()}`)
    }
  })

  await page.goto('/findings')
  await page.waitForLoadState('networkidle')
  expect(failedRequests).toHaveLength(0)
})

test('no console errors on /findings page load', async ({ page }) => {
  const consoleErrors: string[] = []

  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text())
    }
  })

  await page.goto('/findings')
  await page.waitForLoadState('networkidle')

  // Filter out known benign browser/extension errors
  const relevantErrors = consoleErrors.filter(
    (e) =>
      !e.includes('favicon') &&
      !e.includes('Extension') &&
      !e.includes('ResizeObserver'),
  )
  expect(relevantErrors).toHaveLength(0)
})
