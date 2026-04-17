import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto('/feedback')
})

test('shows page heading', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Feedback' })).toBeVisible()
})

test('shows batch comparison section', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Batch Comparison' })).toBeVisible()
})

test('shows batch A and batch B selects', async ({ page }) => {
  await expect(page.getByText('Batch A (baseline)')).toBeVisible()
  await expect(page.getByText('Batch B (new)')).toBeVisible()
  const selects = page.locator('select')
  await expect(selects).toHaveCount(3)
})

test('shows compare button initially disabled', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Compare' })).toBeDisabled()
})

test('compare button enabled when both batches selected', async ({ page }) => {
  const selects = page.locator('select')
  await selects.nth(0).selectOption({ index: 1 })
  await selects.nth(1).selectOption({ index: 2 })
  await expect(page.getByRole('button', { name: 'Compare' })).toBeEnabled()
})

test('shows FP pattern browser section', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'FP Pattern Browser' })).toBeVisible()
})

test('FP pattern load button disabled with no batch selected', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Load Patterns' })).toBeDisabled()
})

test('completed batches appear in selects', async ({ page }) => {
  const firstSelect = page.locator('select').first()
  await expect(firstSelect.locator('option').nth(1)).toContainText('aaaaaaaa')
  await expect(firstSelect.locator('option').nth(1)).toContainText('cve-2024-python')
})

test('comparing two batches shows metric deltas table', async ({ page }) => {
  const selects = page.locator('select')
  await selects.nth(0).selectOption({ index: 1 })
  await selects.nth(1).selectOption({ index: 2 })
  await page.getByRole('button', { name: 'Compare' }).click()
  await expect(page.getByText('Metric Deltas')).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Experiment' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Precision Δ' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Recall Δ' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'F1 Δ' })).toBeVisible()
})

test('load patterns shows FP patterns table', async ({ page }) => {
  const fpSelect = page.locator('select').nth(2)
  await fpSelect.selectOption({ index: 1 })
  await page.getByRole('button', { name: 'Load Patterns' }).click()
  await expect(page.getByRole('columnheader', { name: 'Model' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Vuln Class' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Pattern' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Count' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Suggested Action' })).toBeVisible()
})

test('FP patterns table shows data rows', async ({ page }) => {
  const fpSelect = page.locator('select').nth(2)
  await fpSelect.selectOption({ index: 1 })
  await page.getByRole('button', { name: 'Load Patterns' }).click()
  await expect(page.getByText('Template engine auto-escaping misidentified as XSS')).toBeVisible()
})
