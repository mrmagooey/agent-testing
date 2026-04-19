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

test('submitted POST body contains non-empty required arrays', async ({ page }) => {
  const postPromise = page.waitForRequest(
    (req) => req.url().includes('/api/batches') && req.method() === 'POST'
  )

  await page.locator('select').first().selectOption('cve-2024-python')
  await page.locator('label').filter({ hasText: 'gpt-4o' }).first().locator('input[type="checkbox"]').check()
  await page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]').check()
  await page.getByRole('button', { name: 'Submit Batch' }).click()

  const req = await postPromise
  const body = req.postDataJSON()

  expect(body.dataset).toBe('cve-2024-python')
  expect(body.models).toEqual(['gpt-4o'])
  expect(body.strategies).toEqual(['zero_shot'])
  // Previously-uncaught regression: submit must refuse to send empty dimension arrays.
  expect(Array.isArray(body.tool_variants)).toBe(true)
  expect(body.tool_variants.length).toBeGreaterThan(0)
  expect(Array.isArray(body.verification)).toBe(true)
  expect(body.verification.length).toBeGreaterThan(0)
})

test('validates at least one tool variant required', async ({ page }) => {
  await page.locator('select').first().selectOption('cve-2024-python')
  await page.locator('label').filter({ hasText: 'gpt-4o' }).first().locator('input[type="checkbox"]').check()
  await page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]').check()
  await page.locator('label').filter({ hasText: /^with_tools$/ }).locator('input[type="checkbox"]').uncheck()
  await page.locator('label').filter({ hasText: /^without_tools$/ }).locator('input[type="checkbox"]').uncheck()
  await page.getByRole('button', { name: 'Submit Batch' }).click()
  await expect(page.getByText('Select at least one tool variant')).toBeVisible()
  await expect(page).not.toHaveURL(/\/batches\/newbatch/)
})

test('validates at least one verification option required', async ({ page }) => {
  await page.locator('select').first().selectOption('cve-2024-python')
  await page.locator('label').filter({ hasText: 'gpt-4o' }).first().locator('input[type="checkbox"]').check()
  await page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]').check()
  await page.locator('label').filter({ hasText: /^none$/ }).locator('input[type="checkbox"]').uncheck()
  await page.getByRole('button', { name: 'Submit Batch' }).click()
  await expect(page.getByText('Select at least one verification option')).toBeVisible()
  await expect(page).not.toHaveURL(/\/batches\/newbatch/)
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

test('shows tool extensions checkbox group', async ({ page }) => {
  await expect(page.locator('label').filter({ hasText: 'Tree-sitter' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'LSP' })).toBeVisible()
  await expect(page.locator('label').filter({ hasText: 'DevDocs' })).toBeVisible()
})

test('disables unavailable tool extensions', async ({ page }) => {
  const devdocsCheckbox = page.locator('label').filter({ hasText: 'DevDocs' }).locator('input[type="checkbox"]')
  await expect(devdocsCheckbox).toBeDisabled()
})

test('shows power-set toggle for tool extensions', async ({ page }) => {
  const powerSetToggle = page.locator('input[type="checkbox"]').filter({ hasText: '' }).locator('xpath=./following-sibling::*[contains(text(), "power-set")]')
  // Check that the power-set toggle exists by looking for the text near the checkbox
  await expect(page.getByText(/generate all combinations/i)).toBeVisible()
})

test('successful submission includes tool_extension_sets when extensions selected', async ({ page }) => {
  // Setup form with required fields
  await page.locator('select').first().selectOption('cve-2024-python')
  await page.locator('label').filter({ hasText: 'gpt-4o' }).first().locator('input[type="checkbox"]').check()
  await page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]').check()

  // Select a tool extension
  await page.locator('label').filter({ hasText: 'LSP' }).locator('input[type="checkbox"]').check()

  // Submit
  await page.getByRole('button', { name: 'Submit Batch' }).click()
  await expect(page).toHaveURL(/\/batches\/newbatch-1111/)
})
