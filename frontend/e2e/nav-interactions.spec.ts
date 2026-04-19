import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// Cross-route navigation from /batches/new
// ---------------------------------------------------------------------------

test('from /batches/new: clicking Dashboard navigates to /', async ({ page }) => {
  await page.goto('/batches/new')
  await page.getByRole('navigation').getByRole('link', { name: 'Dashboard', exact: true }).click()
  await expect(page).toHaveURL('/')
})

test('from /batches/new: clicking Datasets navigates to /datasets', async ({ page }) => {
  await page.goto('/batches/new')
  await page.getByRole('navigation').getByRole('link', { name: 'Datasets', exact: true }).click()
  await expect(page).toHaveURL('/datasets')
})

test('from /batches/new: clicking CVE Discovery navigates to /datasets/discover', async ({ page }) => {
  await page.goto('/batches/new')
  await page.getByRole('navigation').getByRole('link', { name: 'CVE Discovery', exact: true }).click()
  await expect(page).toHaveURL('/datasets/discover')
})

test('from /batches/new: clicking Feedback navigates to /feedback', async ({ page }) => {
  await page.goto('/batches/new')
  await page.getByRole('navigation').getByRole('link', { name: 'Feedback', exact: true }).click()
  await expect(page).toHaveURL('/feedback')
})

// ---------------------------------------------------------------------------
// Cross-route navigation from /datasets
// ---------------------------------------------------------------------------

test('from /datasets: clicking Dashboard navigates to /', async ({ page }) => {
  await page.goto('/datasets')
  await page.getByRole('navigation').getByRole('link', { name: 'Dashboard', exact: true }).click()
  await expect(page).toHaveURL('/')
})

test('from /datasets: clicking New Batch navigates to /batches/new', async ({ page }) => {
  await page.goto('/datasets')
  await page.getByRole('navigation').getByRole('link', { name: 'New Batch', exact: true }).click()
  await expect(page).toHaveURL('/batches/new')
})

test('from /datasets: clicking CVE Discovery navigates to /datasets/discover', async ({ page }) => {
  await page.goto('/datasets')
  await page.getByRole('navigation').getByRole('link', { name: 'CVE Discovery', exact: true }).click()
  await expect(page).toHaveURL('/datasets/discover')
})

test('from /datasets: clicking Feedback navigates to /feedback', async ({ page }) => {
  await page.goto('/datasets')
  await page.getByRole('navigation').getByRole('link', { name: 'Feedback', exact: true }).click()
  await expect(page).toHaveURL('/feedback')
})

// ---------------------------------------------------------------------------
// Cross-route navigation from /datasets/discover
// ---------------------------------------------------------------------------

test('from /datasets/discover: clicking Dashboard navigates to /', async ({ page }) => {
  await page.goto('/datasets/discover')
  await page.getByRole('navigation').getByRole('link', { name: 'Dashboard', exact: true }).click()
  await expect(page).toHaveURL('/')
})

test('from /datasets/discover: clicking New Batch navigates to /batches/new', async ({ page }) => {
  await page.goto('/datasets/discover')
  await page.getByRole('navigation').getByRole('link', { name: 'New Batch', exact: true }).click()
  await expect(page).toHaveURL('/batches/new')
})

test('from /datasets/discover: clicking Datasets navigates to /datasets', async ({ page }) => {
  await page.goto('/datasets/discover')
  await page.getByRole('navigation').getByRole('link', { name: 'Datasets', exact: true }).click()
  await expect(page).toHaveURL('/datasets')
})

test('from /datasets/discover: clicking Feedback navigates to /feedback', async ({ page }) => {
  await page.goto('/datasets/discover')
  await page.getByRole('navigation').getByRole('link', { name: 'Feedback', exact: true }).click()
  await expect(page).toHaveURL('/feedback')
})

// ---------------------------------------------------------------------------
// Cross-route navigation from /feedback
// ---------------------------------------------------------------------------

test('from /feedback: clicking Dashboard navigates to /', async ({ page }) => {
  await page.goto('/feedback')
  await page.getByRole('navigation').getByRole('link', { name: 'Dashboard', exact: true }).click()
  await expect(page).toHaveURL('/')
})

test('from /feedback: clicking New Batch navigates to /batches/new', async ({ page }) => {
  await page.goto('/feedback')
  await page.getByRole('navigation').getByRole('link', { name: 'New Batch', exact: true }).click()
  await expect(page).toHaveURL('/batches/new')
})

test('from /feedback: clicking Datasets navigates to /datasets', async ({ page }) => {
  await page.goto('/feedback')
  await page.getByRole('navigation').getByRole('link', { name: 'Datasets', exact: true }).click()
  await expect(page).toHaveURL('/datasets')
})

test('from /feedback: clicking CVE Discovery navigates to /datasets/discover', async ({ page }) => {
  await page.goto('/feedback')
  await page.getByRole('navigation').getByRole('link', { name: 'CVE Discovery', exact: true }).click()
  await expect(page).toHaveURL('/datasets/discover')
})

// ---------------------------------------------------------------------------
// Theme toggle round-trip
// ---------------------------------------------------------------------------

test('theme toggle round-trip: light → dark → light removes dark class from html', async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => localStorage.removeItem('theme'))
  await page.reload()
  await expect(page.locator('html')).not.toHaveClass(/dark/)
  const toggle = page.getByRole('button', { name: /switch to (dark|light) mode/i })
  await toggle.click()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await toggle.click()
  await expect(page.locator('html')).not.toHaveClass(/dark/)
})

// ---------------------------------------------------------------------------
// Theme toggle aria-label flip
// ---------------------------------------------------------------------------

test('theme toggle aria-label changes to "Switch to light mode" after clicking from light', async ({ page }) => {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: 'Switch to dark mode' })
  await toggle.click()
  await expect(page.getByRole('button', { name: 'Switch to light mode' })).toBeVisible()
})

test('theme toggle aria-label returns to "Switch to dark mode" after clicking back to light', async ({ page }) => {
  await page.goto('/')
  const toggle = page.getByRole('button', { name: 'Switch to dark mode' })
  await toggle.click()
  await page.getByRole('button', { name: 'Switch to light mode' }).click()
  await expect(page.getByRole('button', { name: 'Switch to dark mode' })).toBeVisible()
})
