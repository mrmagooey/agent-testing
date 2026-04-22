import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// Helper: wait until the dataset <select> has real options loaded (beyond the
// placeholder "Select a dataset…" option).
async function waitForDatasetOptions(page: import('@playwright/test').Page) {
  await page.waitForFunction(() => {
    const sel = document.querySelector('select')
    return sel !== null && sel.options.length > 1
  })
}

// Helper: wait for the ModelSearchPicker to be ready (search input visible).
async function waitForPickerReady(page: import('@playwright/test').Page) {
  await page.waitForSelector('[placeholder="Search models…"]', { state: 'visible' })
}

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto('/experiments/new')
  await waitForDatasetOptions(page)
  await waitForPickerReady(page)
})

// ---------------------------------------------------------------------------
// 1. Dataset <select>: pick first dataset and assert estimate is refetched
// ---------------------------------------------------------------------------
test('selecting a dataset triggers an estimate refetch', async ({ page }) => {
  // useEstimate requires at least one model OR strategy before it will fire.
  // Pre-fill a model and strategy so the hook is already active, then change
  // the dataset to trigger a fresh request.
  await page.getByText('GPT-4o').first().click()
  await page
    .locator('label')
    .filter({ hasText: 'zero_shot' })
    .locator('input[type="checkbox"]')
    .check()

  // Register the promise BEFORE the triggering action so we don't miss the request.
  const estimateRequestPromise = page.waitForRequest(
    (req) => req.url().includes('/api/experiments/estimate') && req.method() === 'POST',
    { timeout: 5000 }
  )

  await page.locator('select').first().selectOption('cve-2024-python')

  const estimateReq = await estimateRequestPromise
  expect(estimateReq).toBeTruthy()
  const body = estimateReq.postDataJSON()
  expect(body.dataset).toBe('cve-2024-python')
})

// ---------------------------------------------------------------------------
// 2. Model picker: click each row, verify pill tray + estimate refetch
// ---------------------------------------------------------------------------
const MODELS = [
  { id: 'gpt-4o', display: 'GPT-4o' },
  { id: 'gpt-4o-mini', display: 'GPT-4o Mini' },
  { id: 'claude-3-5-sonnet-20241022', display: 'Claude 3.5 Sonnet' },
  { id: 'claude-3-haiku-20240307', display: 'Claude 3 Haiku' },
  { id: 'gemini-1.5-pro', display: 'Gemini 1.5 Pro' },
]

for (const model of MODELS) {
  test(`model "${model.display}" can be toggled via picker and triggers estimate refetch`, async ({ page }) => {
    const estimateRequestPromise = page.waitForRequest(
      (req) => req.url().includes('/api/experiments/estimate') && req.method() === 'POST'
    )

    // Click the model row to select it (the display name span inside the Command.Item)
    await page.getByText(model.display).first().click()

    // Pill tray should appear (aria-live region with "selected" in its label)
    await expect(page.locator('[aria-live="polite"]')).toBeVisible()

    await estimateRequestPromise
  })
}

// ---------------------------------------------------------------------------
// 3. Strategy checkboxes: toggle each one, verify checked state + estimate
// ---------------------------------------------------------------------------
const STRATEGIES = ['zero_shot', 'chain_of_thought', 'few_shot', 'agent']

for (const strategy of STRATEGIES) {
  test(`strategy checkbox "${strategy}" can be toggled and triggers estimate refetch`, async ({ page }) => {
    const estimateRequestPromise = page.waitForRequest(
      (req) => req.url().includes('/api/experiments/estimate') && req.method() === 'POST'
    )

    const checkbox = page
      .locator('label')
      .filter({ hasText: strategy })
      .locator('input[type="checkbox"]')

    await checkbox.check()
    await expect(checkbox).toBeChecked()

    await estimateRequestPromise

    await checkbox.uncheck()
    await expect(checkbox).not.toBeChecked()
  })
}

// ---------------------------------------------------------------------------
// 4. Profile radio: pick a non-default profile
// ---------------------------------------------------------------------------
test('picking a non-default profile radio selects it', async ({ page }) => {
  // "default" is pre-selected by the component; pick "strict"
  const strictRadio = page
    .locator('label')
    .filter({ hasText: 'strict' })
    .locator('input[type="radio"]')

  await strictRadio.check()
  await expect(strictRadio).toBeChecked()

  // The previously-default one should now be deselected
  const defaultRadio = page
    .locator('label')
    .filter({ hasText: 'default' })
    .locator('input[type="radio"]')
  await expect(defaultRadio).not.toBeChecked()

  // Also verify "lenient" works
  const lenientRadio = page
    .locator('label')
    .filter({ hasText: 'lenient' })
    .locator('input[type="radio"]')
  await lenientRadio.check()
  await expect(lenientRadio).toBeChecked()
  await expect(strictRadio).not.toBeChecked()
})

// ---------------------------------------------------------------------------
// 5. Tool-variant and verification checkboxes
// ---------------------------------------------------------------------------
test('tool variant checkboxes can be individually toggled', async ({ page }) => {
  const withTools = page
    .locator('label')
    .filter({ hasText: /^with_tools$/ })
    .locator('input[type="checkbox"]')
  const withoutTools = page
    .locator('label')
    .filter({ hasText: /^without_tools$/ })
    .locator('input[type="checkbox"]')

  // Both start checked (component default)
  await expect(withTools).toBeChecked()
  await expect(withoutTools).toBeChecked()

  // Uncheck one
  await withTools.uncheck()
  await expect(withTools).not.toBeChecked()
  await expect(withoutTools).toBeChecked()

  // Re-check it
  await withTools.check()
  await expect(withTools).toBeChecked()
})

test('verification checkboxes can be individually toggled', async ({ page }) => {
  const noneCheckbox = page
    .locator('label')
    .filter({ hasText: /^none$/ })
    .locator('input[type="checkbox"]')
  const withVerification = page
    .locator('label')
    .filter({ hasText: /^with_verification$/ })
    .locator('input[type="checkbox"]')

  // "none" starts checked, "with_verification" starts unchecked
  await expect(noneCheckbox).toBeChecked()
  await expect(withVerification).not.toBeChecked()

  // Toggle on with_verification
  await withVerification.check()
  await expect(withVerification).toBeChecked()

  // Toggle off none
  await noneCheckbox.uncheck()
  await expect(noneCheckbox).not.toBeChecked()

  // Restore both
  await noneCheckbox.check()
  await expect(noneCheckbox).toBeChecked()
})

// ---------------------------------------------------------------------------
// 6. Tool extensions: toggle an available one; confirm power-set checkbox is
//    visible/clickable when an extension is selected
// ---------------------------------------------------------------------------
test('selecting an available tool extension exposes the power-set checkbox', async ({ page }) => {
  // The power-set checkbox is always rendered; confirm it is visible
  const powerSetCheckbox = page.locator('label').filter({ hasText: /generate all combinations/i }).locator('input[type="checkbox"]')
  await expect(powerSetCheckbox).toBeVisible()
  await expect(powerSetCheckbox).toBeChecked() // defaults to true

  // Select "Tree-sitter" (available: true)
  const treeSitterCheckbox = page
    .locator('label')
    .filter({ hasText: 'Tree-sitter' })
    .locator('input[type="checkbox"]')
  await treeSitterCheckbox.check()
  await expect(treeSitterCheckbox).toBeChecked()

  // Power-set checkbox remains visible and clickable
  await expect(powerSetCheckbox).toBeVisible()
  await powerSetCheckbox.uncheck()
  await expect(powerSetCheckbox).not.toBeChecked()

  // Can re-check it
  await powerSetCheckbox.check()
  await expect(powerSetCheckbox).toBeChecked()
})

test('selecting LSP tool extension triggers estimate refetch', async ({ page }) => {
  // useEstimate requires at least one model OR strategy before it will fire.
  await page.getByText('GPT-4o').first().click()
  await page
    .locator('label')
    .filter({ hasText: 'zero_shot' })
    .locator('input[type="checkbox"]')
    .check()

  const lspCheckbox = page
    .locator('label')
    .filter({ hasText: 'LSP' })
    .locator('input[type="checkbox"]')

  // Register the promise BEFORE the triggering action so we don't miss the request.
  const estimateRequestPromise = page.waitForRequest(
    (req) => req.url().includes('/api/experiments/estimate') && req.method() === 'POST',
    { timeout: 5000 }
  )

  await lspCheckbox.check()
  await expect(lspCheckbox).toBeChecked()

  await estimateRequestPromise
})

test('DevDocs tool extension is disabled (unavailable)', async ({ page }) => {
  const devdocsCheckbox = page
    .locator('label')
    .filter({ hasText: 'DevDocs' })
    .locator('input[type="checkbox"]')
  await expect(devdocsCheckbox).toBeDisabled()
})

// ---------------------------------------------------------------------------
// 7. Repetitions input: fill "3"; Spend Cap: fill "20"
// ---------------------------------------------------------------------------
test('repetitions input accepts value "3"', async ({ page }) => {
  const repetitionsInput = page.locator('input[type="number"][min="1"][max="10"]')
  await repetitionsInput.fill('3')
  await expect(repetitionsInput).toHaveValue('3')
})

test('spend cap input accepts value "20"', async ({ page }) => {
  const spendCapInput = page.locator('input[type="number"][step="0.01"]')
  await spendCapInput.fill('20')
  await expect(spendCapInput).toHaveValue('20')
})

// ---------------------------------------------------------------------------
// 8. Submit: fully valid form → navigates to /experiments/<new-id>
// ---------------------------------------------------------------------------
test('complete valid form submission navigates to the new experiment detail page', async ({ page }) => {
  // Dataset
  await page.locator('select').first().selectOption('cve-2024-python')

  // Model via picker
  await page.getByText('GPT-4o').first().click()

  // Strategy
  await page
    .locator('label')
    .filter({ hasText: 'zero_shot' })
    .locator('input[type="checkbox"]')
    .check()

  // Fill optional numeric fields for completeness
  await page.locator('input[type="number"][min="1"][max="10"]').fill('3')
  await page.locator('input[type="number"][step="0.01"]').fill('20')

  // Click submit
  await page.getByRole('button', { name: 'Submit Experiment' }).click()

  // Should navigate to the mocked experiment id returned by POST /api/experiments
  await expect(page).toHaveURL(/\/experiments\/newexperiment-1111-1111-1111-111111111111/)
})

test('POST /api/experiments is called with correct payload on valid submission', async ({ page }) => {
  const postPromise = page.waitForRequest(
    (req) => req.url().includes('/api/experiments') && req.method() === 'POST'
  )

  await page.locator('select').first().selectOption('cve-2024-python')
  await page.getByText('GPT-4o').first().click()
  await page
    .locator('label')
    .filter({ hasText: 'zero_shot' })
    .locator('input[type="checkbox"]')
    .check()

  await page.getByRole('button', { name: 'Submit Experiment' }).click()

  const req = await postPromise
  const body = req.postDataJSON()

  expect(body.dataset).toBe('cve-2024-python')
  expect(body.models).toContain('gpt-4o')
  expect(body.strategies).toContain('zero_shot')
  expect(Array.isArray(body.tool_variants)).toBe(true)
  expect(body.tool_variants.length).toBeGreaterThan(0)
  expect(Array.isArray(body.verification)).toBe(true)
  expect(body.verification.length).toBeGreaterThan(0)
})

// ---------------------------------------------------------------------------
// 9. Submit with incomplete form: stays on /experiments/new with error message
// ---------------------------------------------------------------------------
test('clicking submit with no dataset shows required-fields message and stays on /experiments/new', async ({ page }) => {
  // Do NOT select a dataset; click submit immediately
  await page.getByRole('button', { name: 'Submit Experiment' }).click()

  // Error message should appear
  await expect(page.getByText('Fill in all required fields above')).toBeVisible()

  // URL should still be /experiments/new
  await expect(page).toHaveURL(/\/experiments\/new$/)
})

test('submit button is initially enabled before any interaction', async ({ page }) => {
  const submitBtn = page.getByRole('button', { name: 'Submit Experiment' })
  await expect(submitBtn).toBeEnabled()
})

test('submit button becomes disabled after failed submit attempt with invalid form', async ({ page }) => {
  // Trigger a failed submit (no dataset selected)
  await page.getByRole('button', { name: 'Submit Experiment' }).click()

  // After submitAttempted=true and isValid=false, button should be disabled
  const submitBtn = page.getByRole('button', { name: 'Submit Experiment' })
  await expect(submitBtn).toBeDisabled()
})
