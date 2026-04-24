import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'
import { sequencedMock } from './helpers/sequencedMock'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto('/feedback')
})

test('shows page heading', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Feedback' })).toBeVisible()
})

test('shows experiment comparison section', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Experiment Comparison' })).toBeVisible()
})

test('shows experiment A and experiment B selects', async ({ page }) => {
  await expect(page.getByText('Experiment A (baseline)')).toBeVisible()
  await expect(page.getByText('Experiment B (new)')).toBeVisible()
  const selects = page.locator('select')
  await expect(selects).toHaveCount(5)
})

test('shows compare button initially disabled', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Compare' })).toBeDisabled()
})

test('compare button enabled when both experiments selected', async ({ page }) => {
  const selects = page.locator('select')
  await selects.nth(2).selectOption({ index: 1 })
  await selects.nth(3).selectOption({ index: 2 })
  await expect(page.getByRole('button', { name: 'Compare' })).toBeEnabled()
})

test('shows FP pattern browser section', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'FP Pattern Browser' })).toBeVisible()
})

test('FP pattern load button disabled with no experiment selected', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Load Patterns' })).toBeDisabled()
})

test('completed experiments appear in selects', async ({ page }) => {
  const experimentASelect = page.locator('select').nth(2)
  await expect(experimentASelect.locator('option').nth(1)).toContainText('aaaaaaaa')
  await expect(experimentASelect.locator('option').nth(1)).toContainText('cve-2024-python')
})

test('comparing two experiments shows metric deltas table', async ({ page }) => {
  const selects = page.locator('select')
  await selects.nth(2).selectOption({ index: 1 })
  await selects.nth(3).selectOption({ index: 2 })
  await page.getByRole('button', { name: 'Compare' }).click()
  await expect(page.getByText('Metric Deltas')).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Experiment' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Precision Δ' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Recall Δ' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'F1 Δ' })).toBeVisible()
})

test('load patterns shows FP patterns table', async ({ page }) => {
  const fpSelect = page.locator('select').nth(4)
  await fpSelect.selectOption({ index: 1 })
  await page.getByRole('button', { name: 'Load Patterns' }).click()
  await expect(page.getByRole('columnheader', { name: 'Model' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Vuln Class' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Pattern' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Count' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Suggested Action' })).toBeVisible()
})

test('FP patterns table shows data rows', async ({ page }) => {
  const fpSelect = page.locator('select').nth(4)
  await fpSelect.selectOption({ index: 1 })
  await page.getByRole('button', { name: 'Load Patterns' }).click()
  await expect(page.getByText('Template engine auto-escaping misidentified as XSS')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Gap #10: reclassify → feedback downstream effect
// ---------------------------------------------------------------------------

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'

// Three FP patterns returned before any reclassify action.
const FP_PATTERNS_BEFORE = [
  {
    model: 'gpt-4o',
    vuln_class: 'xss',
    pattern: 'Template engine auto-escaping misidentified as XSS',
    count: 5,
    suggested_action: 'Add template engine context to system prompt',
  },
  {
    model: 'claude-3-5-sonnet-20241022',
    vuln_class: 'sqli',
    pattern: 'ORM query builder flagged as raw SQL',
    count: 3,
    suggested_action: 'Include ORM documentation in context',
  },
  {
    model: 'gpt-4o',
    vuln_class: 'xss',
    pattern: 'XSS in auth layer misclassified',
    count: 2,
    suggested_action: 'Review auth layer context',
  },
]

// Two FP patterns returned after reclassify removes the third pattern.
const FP_PATTERNS_AFTER = [
  {
    model: 'gpt-4o',
    vuln_class: 'xss',
    pattern: 'Template engine auto-escaping misidentified as XSS',
    count: 5,
    suggested_action: 'Add template engine context to system prompt',
  },
  {
    model: 'claude-3-5-sonnet-20241022',
    vuln_class: 'sqli',
    pattern: 'ORM query builder flagged as raw SQL',
    count: 3,
    suggested_action: 'Include ORM documentation in context',
  },
]

test.describe('reclassify → feedback update', () => {
  // Each test in this describe manages its own navigation; the outer beforeEach
  // still runs (mockApi + goto /feedback) but we navigate away as needed.

  test('reclassifying an FP finding causes feedback page to show one fewer FP pattern', async ({ page }) => {
    // Register the sequenced mock AFTER mockApi so it wins (Playwright: last-registered first).
    // Step 0 → 3 patterns (pre-reclassify baseline loaded first on feedback page).
    // Step 1 → 2 patterns (post-reclassify: third pattern removed).
    const handle = await sequencedMock(
      page,
      `**/api/experiments/${EXPERIMENT_ID}/fp-patterns`,
      [
        { status: 200, body: FP_PATTERNS_BEFORE },
        { status: 200, body: FP_PATTERNS_AFTER },
      ],
      { afterExhausted: 'last' },
    )

    // ── Step 0: load patterns on the feedback page first (consumes sequence[0]) ──
    // The outer beforeEach already navigated to /feedback.
    await page.locator('[data-testid="fp-pattern-browser-section"] select').selectOption(EXPERIMENT_ID)
    await page.getByRole('button', { name: 'Load Patterns' }).click()
    // Confirm 3 patterns are visible before reclassify.
    await expect(page.getByText('XSS in auth layer misclassified')).toBeVisible()
    await expect(handle.callCount()).toBe(1)

    // ── Step 1: reclassify an FP finding in RunDetail ──
    await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)
    const findingRow = page
      .locator('tbody tr')
      .filter({ hasText: 'Reflected XSS in search results' })
      .first()
    await findingRow.click()
    await page.getByRole('button', { name: /Reclassify as Unlabeled Real/i }).click()

    // ── Step 2: return to feedback and reload patterns (consumes sequence[1]) ──
    await page.goto('/feedback')
    await page.locator('[data-testid="fp-pattern-browser-section"] select').selectOption(EXPERIMENT_ID)
    await page.getByRole('button', { name: 'Load Patterns' }).click()

    // After reclassify the server returns 2 patterns — the third is gone.
    await expect(page.getByText('Template engine auto-escaping misidentified as XSS')).toBeVisible()
    await expect(page.getByText('ORM query builder flagged as raw SQL')).toBeVisible()
    await expect(page.getByText('XSS in auth layer misclassified')).not.toBeVisible()

    // Confirm row count: 2 data rows in the FP patterns table.
    const dataRows = page.locator('[data-testid="fp-pattern-browser-section"] tbody tr')
    await expect(dataRows).toHaveCount(2)
    await expect(handle.callCount()).toBe(2)
  })

  test('without reclassify, feedback page still shows the original pattern count', async ({ page }) => {
    // Register the sequenced mock — but this test never clicks reclassify, so only
    // sequence[0] (3 patterns) should ever be returned.
    const handle = await sequencedMock(
      page,
      `**/api/experiments/${EXPERIMENT_ID}/fp-patterns`,
      [
        { status: 200, body: FP_PATTERNS_BEFORE },
        { status: 200, body: FP_PATTERNS_AFTER },
      ],
      { afterExhausted: 'last' },
    )

    // The outer beforeEach already navigated to /feedback — load patterns directly.
    await page.locator('[data-testid="fp-pattern-browser-section"] select').selectOption(EXPERIMENT_ID)
    await page.getByRole('button', { name: 'Load Patterns' }).click()

    // This is step 0 of the sequence → 3 patterns should be visible.
    await expect(page.getByText('Template engine auto-escaping misidentified as XSS')).toBeVisible()
    await expect(page.getByText('ORM query builder flagged as raw SQL')).toBeVisible()
    await expect(page.getByText('XSS in auth layer misclassified')).toBeVisible()

    const dataRows = page.locator('[data-testid="fp-pattern-browser-section"] tbody tr')
    await expect(dataRows).toHaveCount(3)
    // Exactly one call — no spurious second request.
    await expect(handle.callCount()).toBe(1)
  })
})
