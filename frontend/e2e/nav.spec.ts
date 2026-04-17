import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('dashboard nav link is active on root route', async ({ page }) => {
  await page.goto('/')
  const dashboardLink = page.getByRole('navigation').getByRole('link', { name: 'Dashboard' })
  await expect(dashboardLink).toHaveClass(/bg-indigo-100|bg-indigo-900/)
})

test('new batch nav link is active on /batches/new', async ({ page }) => {
  await page.goto('/batches/new')
  const newBatchLink = page.getByRole('navigation').getByRole('link', { name: 'New Batch' })
  await expect(newBatchLink).toHaveClass(/bg-indigo-100|bg-indigo-900/)
})

test('datasets nav link is active on /datasets', async ({ page }) => {
  await page.goto('/datasets')
  const datasetsLink = page.getByRole('navigation').getByRole('link', { name: 'Datasets', exact: true })
  await expect(datasetsLink).toHaveClass(/bg-indigo-100|bg-indigo-900/)
})

test('cve discovery nav link is active on /datasets/discover', async ({ page }) => {
  await page.goto('/datasets/discover')
  const cveLink = page.getByRole('navigation').getByRole('link', { name: 'CVE Discovery' })
  await expect(cveLink).toHaveClass(/bg-indigo-100|bg-indigo-900/)
})

test('feedback nav link is active on /feedback', async ({ page }) => {
  await page.goto('/feedback')
  const feedbackLink = page.getByRole('navigation').getByRole('link', { name: 'Feedback' })
  await expect(feedbackLink).toHaveClass(/bg-indigo-100|bg-indigo-900/)
})

test('clicking nav links navigates to correct pages', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('navigation').getByRole('link', { name: 'New Batch' }).click()
  await expect(page).toHaveURL('/batches/new')

  await page.getByRole('navigation').getByRole('link', { name: 'Datasets', exact: true }).click()
  await expect(page).toHaveURL('/datasets')

  await page.getByRole('navigation').getByRole('link', { name: 'CVE Discovery' }).click()
  await expect(page).toHaveURL('/datasets/discover')

  await page.getByRole('navigation').getByRole('link', { name: 'Feedback' }).click()
  await expect(page).toHaveURL('/feedback')
})

test('navbar shows sec-review brand text', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('sec-review')).toBeVisible()
})

test('theme toggle button is present and clickable', async ({ page }) => {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: /switch to (dark|light) mode/i })
  await expect(toggle).toBeVisible()
  await toggle.click()
  await expect(toggle).toBeVisible()
})

test('theme toggle persists dark mode in localStorage', async ({ page }) => {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: /switch to (dark|light) mode/i })
  await toggle.click()
  await page.reload()
  const html = page.locator('html')
  await expect(html).toHaveClass(/dark/)
})
