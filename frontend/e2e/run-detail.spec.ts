import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const BATCH_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto(`/batches/${BATCH_ID}/runs/${RUN_ID}`)
})

test('shows experiment id in header', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'gpt-4o__zero_shot__with_tools__default__none' })).toBeVisible()
})

test('shows run id', async ({ page }) => {
  await expect(page.getByText(`Run ID: ${RUN_ID}`)).toBeVisible()
})

test('shows model metadata', async ({ page }) => {
  await expect(page.getByText('gpt-4o').first()).toBeVisible()
  await expect(page.getByRole('definition').filter({ hasText: 'zero_shot' })).toBeVisible()
  await expect(page.getByText('with_tools').first()).toBeVisible()
})

test('shows metric cards', async ({ page }) => {
  await expect(page.getByText('Precision').first()).toBeVisible()
  await expect(page.getByText('Recall').first()).toBeVisible()
  await expect(page.getByText('F1').first()).toBeVisible()
  await expect(page.getByText('FPR', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('0.812').first()).toBeVisible()
  await expect(page.getByText('0.743').first()).toBeVisible()
  await expect(page.getByText('0.776').first()).toBeVisible()
})

test('shows findings table with correct count', async ({ page }) => {
  await expect(page.getByRole('heading', { name: /Findings \(2\)/ })).toBeVisible()
})

test('shows finding titles in table', async ({ page }) => {
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
  await expect(page.getByText('Reflected XSS in search results')).toBeVisible()
})

test('shows finding severity badges', async ({ page }) => {
  await expect(page.getByText('critical').first()).toBeVisible()
  await expect(page.getByText('high').first()).toBeVisible()
})

test('shows finding match status badges', async ({ page }) => {
  await expect(page.getByText('tp').first()).toBeVisible()
  await expect(page.getByText('fp').first()).toBeVisible()
})

test('shows file paths in findings table', async ({ page }) => {
  await expect(page.getByRole('cell', { name: 'src/auth/login.py', exact: true })).toBeVisible()
})

test('clicking finding row expands description', async ({ page }) => {
  const findingRow = page.locator('tbody tr').filter({ hasText: 'SQL Injection in user login handler' }).first()
  await findingRow.click()
  await expect(page.getByText('User-supplied input is concatenated directly')).toBeVisible()
})

test('shows tool call audit section', async ({ page }) => {
  await expect(page.getByRole('heading', { name: /Tool Call Audit/ })).toBeVisible()
  await expect(page.getByText('read_file')).toBeVisible()
  await expect(page.getByText('search_code')).toBeVisible()
})

test('shows conversation transcript collapsible', async ({ page }) => {
  await expect(page.getByRole('button', { name: /Conversation Transcript/ })).toBeVisible()
})

test('expanding conversation transcript shows messages', async ({ page }) => {
  await page.getByRole('button', { name: /Conversation Transcript/ }).click()
  await expect(page.getByText('Analyze the codebase for security vulnerabilities.')).toBeVisible()
})

test('shows prompt snapshot collapsible', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Prompt Snapshot' })).toBeVisible()
})
