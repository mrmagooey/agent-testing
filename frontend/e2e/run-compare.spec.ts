import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const BATCH_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_A = 'run-001-aaa'
const RUN_B = 'run-002-bbb'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto(`/batches/${BATCH_ID}/compare?a=${RUN_A}&b=${RUN_B}`)
})

test('shows run A card with model', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Run A' })).toBeVisible()
})

test('shows run B card with model', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Run B' })).toBeVisible()
})

test('shows model info in run A card', async ({ page }) => {
  const runASection = page.locator('div').filter({ hasText: /Run A/ }).first().locator('..')
  await expect(runASection.getByText('gpt-4o').first()).toBeVisible()
  await expect(runASection.getByText('zero_shot').first()).toBeVisible()
})

test('shows metrics in both run cards', async ({ page }) => {
  await expect(page.getByText('0.812').first()).toBeVisible()
  await expect(page.getByText('0.756').first()).toBeVisible()
})

test('shows summary text with counts', async ({ page }) => {
  await expect(page.getByText(/Run A found/)).toBeVisible()
  await expect(page.getByText(/Run B found/)).toBeVisible()
  await expect(page.getByText(/found by both/)).toBeVisible()
})

test('shows Found by Both tab with count', async ({ page }) => {
  await expect(page.getByRole('button', { name: /Found by Both \(1\)/ })).toBeVisible()
})

test('shows Only in A tab with count', async ({ page }) => {
  await expect(page.getByRole('button', { name: /Only in A \(1\)/ })).toBeVisible()
})

test('shows Only in B tab with count', async ({ page }) => {
  await expect(page.getByRole('button', { name: /Only in B \(0\)/ })).toBeVisible()
})

test('found by both tab shows shared findings', async ({ page }) => {
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
})

test('only in A tab shows findings unique to A', async ({ page }) => {
  await page.getByRole('button', { name: /Only in A/ }).click()
  await expect(page.getByText('Path traversal in file download endpoint')).toBeVisible()
})

test('only in B tab shows empty state', async ({ page }) => {
  await page.getByRole('button', { name: /Only in B/ }).click()
  await expect(page.getByText('No findings in this category.')).toBeVisible()
})

test('clicking finding card expands description', async ({ page }) => {
  await page.getByText('SQL Injection in user login handler').click()
  await expect(page.getByText('User-supplied input is concatenated directly')).toBeVisible()
})

test('clicking finding card again collapses it', async ({ page }) => {
  await page.getByText('SQL Injection in user login handler').click()
  await expect(page.getByText('User-supplied input is concatenated directly')).toBeVisible()
  await page.getByText('SQL Injection in user login handler').click()
  await expect(page.getByText('User-supplied input is concatenated directly')).not.toBeVisible()
})
