import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const DATASET_NAME = 'cve-2024-python'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto(`/datasets/${DATASET_NAME}`)
})

test('shows dataset name as heading', async ({ page }) => {
  await expect(page.getByRole('heading', { name: DATASET_NAME })).toBeVisible()
})

test('shows inject vulnerability button', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Inject Vulnerability' })).toBeVisible()
})

test('shows file tree panel', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Files' })).toBeVisible()
})

test('shows labels table with correct count', async ({ page }) => {
  await expect(page.getByRole('heading', { name: /Labels \(3\)/ })).toBeVisible()
})

test('shows labels table columns', async ({ page }) => {
  await expect(page.getByRole('columnheader', { name: 'File' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Lines' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Vuln Class' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Severity' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'CWE' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Source' })).toBeVisible()
})

test('shows label file paths', async ({ page }) => {
  await expect(page.getByText('src/auth/login.py').first()).toBeVisible()
})

test('shows label vuln classes', async ({ page }) => {
  await expect(page.getByText('sqli')).toBeVisible()
  await expect(page.getByText('ssrf')).toBeVisible()
  await expect(page.getByText('path_traversal')).toBeVisible()
})

test('shows label CWEs', async ({ page }) => {
  await expect(page.getByText('CWE-89')).toBeVisible()
  await expect(page.getByText('CWE-918')).toBeVisible()
})

test('shows file viewer placeholder when no file selected', async ({ page }) => {
  await expect(page.getByText('Select a file to view')).toBeVisible()
})

test('clicking label row selects file and shows viewer', async ({ page }) => {
  await page.locator('tbody tr').first().click()
  await expect(page.getByText('src/auth/login.py').first()).toBeVisible()
})

test('inject button opens modal on click', async ({ page }) => {
  await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
  await expect(page.getByText('Inject Vulnerability — Step 1/5')).toBeVisible()
})

test('injection modal shows template list in step 1', async ({ page }) => {
  await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
  await expect(page.getByText('Select a vulnerability template:')).toBeVisible()
  await expect(page.getByRole('button', { name: /sqli/ })).toBeVisible()
})

test('injection modal can be closed', async ({ page }) => {
  await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
  await expect(page.getByText('Inject Vulnerability — Step 1/5')).toBeVisible()
  await page.getByRole('button', { name: '×' }).click()
  await expect(page.getByText('Inject Vulnerability — Step 1/5')).not.toBeVisible()
})
