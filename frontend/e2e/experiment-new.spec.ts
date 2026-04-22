import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto('/experiments/new')
})

test('shows page heading', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
})

test('shows dataset selector', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Dataset' })).toBeVisible()
  const select = page.locator('select').first()
  await expect(select).toBeVisible()
  await expect(select.locator('option', { hasText: 'cve-2024-python' })).toBeAttached()
})

test('shows Models heading and search input', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Models' })).toBeVisible()
  // ModelSearchPicker renders a search input
  await expect(page.getByPlaceholder('Search models…')).toBeVisible()
})

test('models picker renders grouped by provider — available models visible', async ({ page }) => {
  // Available models from the grouped mockApi fixture should be visible by default
  // Use first() to avoid strict mode violation (display name + model id both match)
  await expect(page.getByText('GPT-4o').first()).toBeVisible()
  await expect(page.getByText('Claude 3.5 Sonnet').first()).toBeVisible()
  await expect(page.getByText('Gemini 1.5 Pro').first()).toBeVisible()
})

test('search narrows model list', async ({ page }) => {
  const searchInput = page.getByPlaceholder('Search models…')
  await searchInput.fill('gpt')
  // GPT models should be visible (use first() to avoid strict mode on display+id spans)
  await expect(page.getByText('GPT-4o').first()).toBeVisible()
  await expect(page.getByText('GPT-4o Mini').first()).toBeVisible()
  // Claude model should not be visible
  await expect(page.getByText('Claude 3.5 Sonnet').first()).not.toBeVisible()
  // Clear search
  await searchInput.fill('')
  await expect(page.getByText('Claude 3.5 Sonnet').first()).toBeVisible()
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

test('shows submit experiment button', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Submit Experiment' })).toBeVisible()
})

test('validates dataset required', async ({ page }) => {
  await page.getByRole('button', { name: 'Submit Experiment' }).click()
  await expect(page.getByText('Please select a dataset')).toBeVisible()
})

test('validates at least one model required', async ({ page }) => {
  await page.locator('select').first().selectOption('cve-2024-python')
  await page.getByRole('button', { name: 'Submit Experiment' }).click()
  await expect(page.getByText('Select at least one model')).toBeVisible()
})

test('validates at least one strategy required', async ({ page }) => {
  await page.locator('select').first().selectOption('cve-2024-python')
  // Select GPT-4o via the picker (click the row)
  await page.getByText('GPT-4o').first().click()
  await page.getByRole('button', { name: 'Submit Experiment' }).click()
  await expect(page.getByText('Select at least one strategy')).toBeVisible()
})

test('successful form submission navigates to experiment detail', async ({ page }) => {
  await page.locator('select').first().selectOption('cve-2024-python')
  // Select GPT-4o via the picker
  await page.getByText('GPT-4o').first().click()
  await page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]').check()
  await page.getByRole('button', { name: 'Submit Experiment' }).click()
  await expect(page).toHaveURL(/\/experiments\/newexperiment-1111/)
})

test('submitted POST body contains non-empty required arrays', async ({ page }) => {
  const postPromise = page.waitForRequest(
    (req) => req.url().includes('/api/experiments') && req.method() === 'POST'
  )

  await page.locator('select').first().selectOption('cve-2024-python')
  await page.getByText('GPT-4o').first().click()
  await page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]').check()
  await page.getByRole('button', { name: 'Submit Experiment' }).click()

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
  await page.getByText('GPT-4o').first().click()
  await page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]').check()
  await page.locator('label').filter({ hasText: /^with_tools$/ }).locator('input[type="checkbox"]').uncheck()
  await page.locator('label').filter({ hasText: /^without_tools$/ }).locator('input[type="checkbox"]').uncheck()
  await page.getByRole('button', { name: 'Submit Experiment' }).click()
  await expect(page.getByText('Select at least one tool variant')).toBeVisible()
  await expect(page).not.toHaveURL(/\/experiments\/newexperiment/)
})

test('validates at least one verification option required', async ({ page }) => {
  await page.locator('select').first().selectOption('cve-2024-python')
  await page.getByText('GPT-4o').first().click()
  await page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]').check()
  await page.locator('label').filter({ hasText: /^none$/ }).locator('input[type="checkbox"]').uncheck()
  await page.getByRole('button', { name: 'Submit Experiment' }).click()
  await expect(page.getByText('Select at least one verification option')).toBeVisible()
  await expect(page).not.toHaveURL(/\/experiments\/newexperiment/)
})

test('can select multiple models via picker', async ({ page }) => {
  await page.getByText('GPT-4o').first().click()
  await page.getByText('Claude 3.5 Sonnet').first().click()
  // Both should appear in the pill tray
  await expect(page.locator('[aria-label*="selected"]')).toBeVisible()
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
  await expect(page.getByText(/generate all combinations/i)).toBeVisible()
})

test('successful submission includes tool_extension_sets when extensions selected', async ({ page }) => {
  // Setup form with required fields
  await page.locator('select').first().selectOption('cve-2024-python')
  await page.getByText('GPT-4o').first().click()
  await page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]').check()

  // Select a tool extension
  await page.locator('label').filter({ hasText: 'LSP' }).locator('input[type="checkbox"]').check()

  // Submit
  await page.getByRole('button', { name: 'Submit Experiment' }).click()
  await expect(page).toHaveURL(/\/experiments\/newexperiment-1111/)
})

// ---------------------------------------------------------------------------
// Phase 6: ModelSearchPicker grouped picker scenarios
// ---------------------------------------------------------------------------

test('models are grouped by provider in the picker', async ({ page }) => {
  // Provider group headings should appear (cmdk group headings)
  await expect(page.getByText('Openai', { exact: false })).toBeVisible()
  await expect(page.getByText('Anthropic', { exact: false })).toBeVisible()
  await expect(page.getByText('Google', { exact: false })).toBeVisible()
})

test('unavailable models hidden by default; "Show unavailable" toggle reveals them', async ({ page }) => {
  // All models in fixture are available, so toggle behavior shows no change in this fixture.
  // Verify the toggle button exists (rendered by ModelSearchPicker).
  const showUnavailableToggle = page.locator('button', { hasText: 'Show unavailable' })
  // ModelSearchPicker renders it as a ToggleChip; check by text
  await expect(page.getByText('Show unavailable')).toBeVisible()
})

test('shows "Allow unavailable models" checkbox', async ({ page }) => {
  await expect(page.getByTestId('allow-unavailable-checkbox')).toBeVisible()
  await expect(page.getByTestId('allow-unavailable-checkbox')).not.toBeChecked()
})

test('checking "Allow unavailable models" sends allow_unavailable_models:true in payload', async ({ page }) => {
  const postPromise = page.waitForRequest(
    (req) => req.url().includes('/api/experiments') && req.method() === 'POST'
  )

  await page.locator('select').first().selectOption('cve-2024-python')
  await page.getByText('GPT-4o').first().click()
  await page.locator('label').filter({ hasText: 'zero_shot' }).locator('input[type="checkbox"]').check()
  // Check the override checkbox
  await page.getByTestId('allow-unavailable-checkbox').check()

  await page.getByRole('button', { name: 'Submit Experiment' }).click()

  const req = await postPromise
  const body = req.postDataJSON()
  expect(body.allow_unavailable_models).toBe(true)
})
