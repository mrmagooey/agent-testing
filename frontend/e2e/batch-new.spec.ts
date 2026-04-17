import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto('/batches/new')
})

test('shows page heading', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'New Batch' })).toBeVisible()
})

test('shows dataset selector', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Dataset' })).toBeVisible()
  const select = page.locator('select').first()
  await expect(select).toBeVisible()
  await expect(select.locator('option', { hasText: 'cve-2024-python' })).toBeAttached()
})

test('shows model checkboxes from API', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Models' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'gpt-4o' }).first()).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'claude-3-5-sonnet-20241022' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'gemini-1.5-pro' })).toBeVisible()
})

test('shows strategy checkboxes from API', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Strategies' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'zero_shot' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'chain_of_thought' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'few_shot' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'agent' })).toBeVisible()
})

test('shows tool variant checkboxes with both options', async ({ page }) => {
  await expect(page.locator('label').filter({ hasText: 'with_tools' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'without_tools' })).toBeVisible()
})

test('shows verification checkboxes', async ({ page }) => {
  await expect(page.locator('label').filter({ hasText: 'none' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'with_verification' })).toBeVisible()
})

test('shows profile radio buttons from API', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Profile' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'default' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'strict' })).toBeVisible()
})

test('shows repetitions input', async ({ page }) => {
  const repetitionsInput = page.locator('input[type="number"][min="1"][max="10"]')
  await expect(repetitionsInput).toBeVisible()
  await expect(repetitionsInput).toHaveValue('1')
})

test('shows submit batch button', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Submit Batch' })).toBeVisible()
})

test('validates dataset required', async ({ page }) => {
  await page.getByRole('button', { name: 'Submit Batch' }).click()
  await expect(page.getByText('Please select a dataset')).toBeVisible()
})

test('validates at least one model required', async ({ page }) => {
  await page.locator('select').first().selectOption('cve-2024-python')
  await page.getByRole('button', { name: 'Submit Batch' }).click()
  await expect(page.getByText('Select at least one model')).toBeVisible()
})

test('validates at least one strategy required', async ({ page }) => {
  await page.locator('select').first().selectOption('cve-2024-python')
  await page.locator('label').filter({ hasText: 'gpt-4o' }).first().locator('input[type="checkbox"]').check()
  await page.getByRole('button', { name: 'Submit Batch' }).click()
  await expect(page.getByText('Select at least one strategy')).toBeVisible()
})

test('successful form submission navigates to batch detail', async ({ page }) => {
  await page.locator('select').first().selectOption('cve-2024-python')
  await page.locator('label').filter({ hasText: 'gpt-4o' }).first().locator('input[type="checkbox"]').check()
  await page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]').check()
  await page.getByRole('button', { name: 'Submit Batch' }).click()
  await expect(page).toHaveURL(/\/batches\/newbatch-1111/)
})

test('can select multiple models and strategies', async ({ page }) => {
  const gpt4oCheckbox = page.locator('label').filter({ hasText: 'gpt-4o' }).first().locator('input[type="checkbox"]')
  const claudeCheckbox = page.locator('label').filter({ hasText: 'claude-3-5-sonnet-20241022' }).locator('input[type="checkbox"]')
  await gpt4oCheckbox.check()
  await claudeCheckbox.check()
  await expect(gpt4oCheckbox).toBeChecked()
  await expect(claudeCheckbox).toBeChecked()

  const zeroShotCheckbox = page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]')
  const cotCheckbox = page.locator('label').filter({ hasText: 'chain_of_thought' }).locator('input[type="checkbox"]')
  await zeroShotCheckbox.check()
  await cotCheckbox.check()
  await expect(zeroShotCheckbox).toBeChecked()
  await expect(cotCheckbox).toBeChecked()
})

test('spend cap input accepts decimal values', async ({ page }) => {
  const spendCapInput = page.locator('input[type="number"][step="0.01"]')
  await spendCapInput.fill('25.50')
  await expect(spendCapInput).toHaveValue('25.50')
})
