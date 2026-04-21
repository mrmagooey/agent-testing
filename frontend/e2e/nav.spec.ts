import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('dashboard nav link is active on root route', async ({ page }) => {
  await page.goto('/')
  const dashboardLink = page.getByRole('navigation').getByRole('link', { name: 'Dashboard' })
  await expect(dashboardLink).toHaveClass(/nav-cursor/)
})

test('new experiment nav link is active on /experiments/new', async ({ page }) => {
  await page.goto('/experiments/new')
  const newExperimentLink = page.getByRole('navigation').getByRole('link', { name: 'New Experiment' })
  await expect(newExperimentLink).toHaveClass(/nav-cursor/)
})

test('datasets nav link is active on /datasets', async ({ page }) => {
  await page.goto('/datasets')
  const datasetsLink = page.getByRole('navigation').getByRole('link', { name: /Datasets/ })
  await expect(datasetsLink).toHaveClass(/nav-cursor/)
})

test('cve discovery nav link is active on /datasets/discover', async ({ page }) => {
  await page.goto('/datasets/discover')
  const cveLink = page.getByRole('navigation').getByRole('link', { name: 'CVE Discovery' })
  await expect(cveLink).toHaveClass(/nav-cursor/)
})

test('feedback nav link is active on /feedback', async ({ page }) => {
  await page.goto('/feedback')
  const feedbackLink = page.getByRole('navigation').getByRole('link', { name: 'Feedback' })
  await expect(feedbackLink).toHaveClass(/nav-cursor/)
})

test('clicking nav links navigates to correct pages', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('navigation').getByRole('link', { name: 'New Experiment' }).click()
  await expect(page).toHaveURL('/experiments/new')

  await page.getByRole('navigation').getByRole('link', { name: /Datasets/ }).click()
  await expect(page).toHaveURL('/datasets')

  await page.getByRole('navigation').getByRole('link', { name: 'CVE Discovery' }).click()
  await expect(page).toHaveURL('/datasets/discover')

  await page.getByRole('navigation').getByRole('link', { name: 'Feedback' }).click()
  await expect(page).toHaveURL('/feedback')
})

test('navbar shows sec-review brand text', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('SEC·REVIEW')).toBeVisible()
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
