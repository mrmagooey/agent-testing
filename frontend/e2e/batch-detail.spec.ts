import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const BATCH_ID = 'aaaaaaaa-0001-0001-0001-000000000001'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('shows batch id in header', async ({ page }) => {
  await page.goto(`/batches/${BATCH_ID}`)
  await expect(page.getByRole('heading', { name: BATCH_ID })).toBeVisible()
})

test('shows dataset name', async ({ page }) => {
  await page.goto(`/batches/${BATCH_ID}`)
  await expect(page.getByText('Dataset: cve-2024-python')).toBeVisible()
})

test('shows completed status badge', async ({ page }) => {
  await page.goto(`/batches/${BATCH_ID}`)
  await expect(page.getByText('completed').first()).toBeVisible()
})

test('shows cost information', async ({ page }) => {
  await page.goto(`/batches/${BATCH_ID}`)
  await expect(page.getByText('$12.45')).toBeVisible()
})

test('shows comparative matrix section', async ({ page }) => {
  await page.goto(`/batches/${BATCH_ID}`)
  await expect(page.getByRole('heading', { name: 'Comparative Matrix' })).toBeVisible()
})

test('shows cost analysis section', async ({ page }) => {
  await page.goto(`/batches/${BATCH_ID}`)
  await expect(page.getByRole('heading', { name: 'Cost Analysis' })).toBeVisible()
})

test('shows findings section', async ({ page }) => {
  await page.goto(`/batches/${BATCH_ID}`)
  await expect(page.getByRole('heading', { name: 'Findings' })).toBeVisible()
})

test('cancel button visible for running batch', async ({ page }) => {
  const RUNNING_BATCH_ID = 'bbbbbbbb-0002-0002-0002-000000000002'
  await page.goto(`/batches/${RUNNING_BATCH_ID}`)
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeVisible()
})

test('cancel button not visible for completed batch', async ({ page }) => {
  await page.goto(`/batches/${BATCH_ID}`)
  await expect(page.getByRole('button', { name: 'Cancel' })).not.toBeVisible()
})

test('shows model names in cost table', async ({ page }) => {
  await page.goto(`/batches/${BATCH_ID}`)
  await expect(page.getByText('gpt-4o').first()).toBeVisible()
  await expect(page.getByText('claude-3-5-sonnet-20241022').first()).toBeVisible()
})

test('compare selected button appears when two runs are selected', async ({ page }) => {
  await page.goto(`/batches/${BATCH_ID}`)
  const matrixSection = page.locator('section').filter({ hasText: 'Comparative Matrix' })
  const checkboxes = matrixSection.locator('input[type="checkbox"]')
  const count = await checkboxes.count()
  if (count >= 2) {
    await checkboxes.nth(0).check()
    await checkboxes.nth(1).check()
    await expect(page.getByRole('button', { name: 'Compare Selected' })).toBeVisible()
  }
})
