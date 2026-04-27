/**
 * e2e spec: subagents-picker
 *
 * Covers the user story:
 *   As a security researcher authoring a hierarchical strategy, I want to select
 *   another strategy from the registry as a subagent, set the dispatch caps
 *   (max depth / invocations / batch size), pick a dispatch-fallback policy, and
 *   save — so the POST body carries the chosen subagents and caps for the
 *   supervisor agent to dispatch to.
 *
 * Component under test:
 *   The subagents picker + dispatch-fallback + output-type sub-region of
 *   BundleDefaultForm inside frontend/src/pages/StrategyEditor.tsx (lines ~250-390).
 *
 * Mocking strategy:
 *   mockApi() registers a legacy GET /api/strategies handler returning a string
 *   array. We override it with page.route() handlers registered AFTER mockApi()
 *   — Playwright routes are LIFO so our handlers win.
 *
 *   The subagents picker only renders when registryStrategies.length > 0, so
 *   GET /api/strategies MUST return at least one StrategySummary.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const STRATEGY_HELPER: import('../src/api/strategies').StrategySummary = {
  id: 'strat-builtin-helper-001',
  name: 'Helper Strategy',
  orchestration_shape: 'single_agent',
  is_builtin: true,
  parent_strategy_id: null,
}

const STRATEGY_CLASSIFIER: import('../src/api/strategies').StrategySummary = {
  id: 'strat-builtin-classifier-002',
  name: 'Classifier Strategy',
  orchestration_shape: 'per_vuln_class',
  is_builtin: true,
  parent_strategy_id: null,
}

const ALL_REGISTRY_STRATEGIES = [STRATEGY_HELPER, STRATEGY_CLASSIFIER]

// Returned by POST /api/strategies for the happy-path save tests
const CREATED_HIERARCHY: import('../src/api/strategies').UserStrategy = {
  id: 'strat-new-hierarchy-001',
  name: 'Hierarchy Test',
  parent_strategy_id: null,
  orchestration_shape: 'single_agent',
  is_builtin: false,
  created_at: '2026-04-27T00:00:00Z',
  default: {
    system_prompt: '',
    user_prompt_template: '',
    profile_modifier: '',
    model_id: '',
    tools: [],
    verification: 'none',
    max_turns: 10,
    tool_extensions: [],
    subagents: ['strat-builtin-helper-001'],
    max_subagent_depth: 5,
    max_subagent_invocations: 200,
    max_subagent_batch_size: 16,
    dispatch_fallback: 'programmatic',
    output_type_name: 'finding_list',
  },
  overrides: [],
}

// Minimal strategy returned for the "no-subagents save" test
const CREATED_MINIMAL: import('../src/api/strategies').UserStrategy = {
  id: 'strat-new-hierarchy-001',
  name: 'Minimal Strategy',
  parent_strategy_id: null,
  orchestration_shape: 'single_agent',
  is_builtin: false,
  created_at: '2026-04-27T00:00:00Z',
  default: {
    system_prompt: '',
    user_prompt_template: '',
    profile_modifier: '',
    model_id: '',
    tools: [],
    verification: 'none',
    max_turns: 10,
    tool_extensions: [],
    subagents: [],
    max_subagent_depth: 3,
    max_subagent_invocations: 100,
    max_subagent_batch_size: 32,
    dispatch_fallback: 'reprompt',
    output_type_name: null,
  },
  overrides: [],
}

// ---------------------------------------------------------------------------
// Helper: wire up strategy mock routes AFTER mockApi()
// Routes are LIFO in Playwright — these take priority over the legacy handler.
// ---------------------------------------------------------------------------

async function mockStrategiesRoutes(page: import('@playwright/test').Page) {
  // GET /api/strategies → StrategySummary[]  (overrides the legacy string-array handler)
  // MUST return at least one entry so the subagents picker renders.
  await page.route('**/api/strategies', (route) => {
    const method = route.request().method()
    if (method !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(ALL_REGISTRY_STRATEGIES),
    })
  })

  // POST /api/strategies/__new__/validate → { valid: true, errors: [] }
  await page.route('**/api/strategies/__new__/validate', (route) => {
    const method = route.request().method()
    if (method !== 'POST') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ valid: true, errors: [] }),
    })
  })

  // POST /api/strategies → return CREATED_HIERARCHY (default handler, overridden
  // per-test in tests that need to capture the body).
  await page.route('**/api/strategies', (route) => {
    const method = route.request().method()
    if (method !== 'POST') return route.fallback()
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(CREATED_HIERARCHY),
    })
  })

  // GET /api/strategies/<id> → return CREATED_HIERARCHY so the post-save redirect works
  await page.route('**/api/strategies/*', (route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()
    if (url.pathname.endsWith('/validate')) return route.fallback()
    if (method !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(CREATED_HIERARCHY),
    })
  })
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await mockStrategiesRoutes(page)
})

// ---------------------------------------------------------------------------
// Test 1: Subagents heading and container are visible at /strategies/new
// ---------------------------------------------------------------------------

test('subagents heading and container are visible when registry returns strategies', async ({ page }) => {
  await page.goto('/strategies/new')
  await expect(page.getByRole('heading', { name: 'New Strategy' })).toBeVisible()

  // The "Subagents" label renders as a <label> element (not a heading)
  await expect(page.getByText('Subagents', { exact: true })).toBeVisible()

  // The multi-select container must be present (only rendered when registry is non-empty)
  await expect(page.getByTestId('subagents-list')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 2: Checkbox for each fixture strategy; all initially unchecked
// ---------------------------------------------------------------------------

test('subagents list contains a checkbox for each registry strategy, all initially unchecked', async ({ page }) => {
  await page.goto('/strategies/new')
  await expect(page.getByTestId('subagents-list')).toBeVisible()

  const helperCheckbox = page.getByTestId(`subagent-checkbox-${STRATEGY_HELPER.id}`)
  const classifierCheckbox = page.getByTestId(`subagent-checkbox-${STRATEGY_CLASSIFIER.id}`)

  await expect(helperCheckbox).toBeVisible()
  await expect(classifierCheckbox).toBeVisible()

  await expect(helperCheckbox).not.toBeChecked()
  await expect(classifierCheckbox).not.toBeChecked()
})

// ---------------------------------------------------------------------------
// Test 3: "N subagents selected" notice tracks selections / deselections
// ---------------------------------------------------------------------------

test('"N subagents selected" notice appears and updates as checkboxes are toggled', async ({ page }) => {
  await page.goto('/strategies/new')
  await expect(page.getByTestId('subagents-list')).toBeVisible()

  const helperCheckbox = page.getByTestId(`subagent-checkbox-${STRATEGY_HELPER.id}`)
  const classifierCheckbox = page.getByTestId(`subagent-checkbox-${STRATEGY_CLASSIFIER.id}`)
  const notice = page.getByText(/\d+ subagents? selected/i)

  // Initially no notice
  await expect(notice).not.toBeVisible()

  // Check first → "1 subagent selected"
  await helperCheckbox.check()
  await expect(notice).toBeVisible()
  await expect(notice).toContainText('1 subagent selected')

  // Check second → "2 subagents selected"
  await classifierCheckbox.check()
  await expect(notice).toContainText('2 subagents selected')

  // Uncheck both → notice disappears
  await helperCheckbox.uncheck()
  await classifierCheckbox.uncheck()
  await expect(notice).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 4: Subagent Caps section heading, default values, and hint visibility
// ---------------------------------------------------------------------------

test('Subagent Caps heading, default values (3/100/32), and unused-hint toggling', async ({ page }) => {
  await page.goto('/strategies/new')
  await expect(page.getByRole('heading', { name: 'Subagent Caps' })).toBeVisible()

  // Default cap values
  await expect(page.getByTestId('max-subagent-depth-input')).toHaveValue('3')
  await expect(page.getByTestId('max-subagent-invocations-input')).toHaveValue('100')
  await expect(page.getByTestId('max-subagent-batch-size-input')).toHaveValue('32')

  // Hint is visible with no subagents selected
  await expect(page.getByText('(unused when no subagents selected)')).toBeVisible()

  // Select one subagent → hint disappears
  await page.getByTestId(`subagent-checkbox-${STRATEGY_HELPER.id}`).check()
  await expect(page.getByText('(unused when no subagents selected)')).not.toBeVisible()

  // Deselect → hint reappears
  await page.getByTestId(`subagent-checkbox-${STRATEGY_HELPER.id}`).uncheck()
  await expect(page.getByText('(unused when no subagents selected)')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 5: Caps inputs accept new values via .fill()
// ---------------------------------------------------------------------------

test('caps inputs accept new numeric values', async ({ page }) => {
  await page.goto('/strategies/new')
  await expect(page.getByRole('heading', { name: 'Subagent Caps' })).toBeVisible()

  const depthInput = page.getByTestId('max-subagent-depth-input')
  const invocationsInput = page.getByTestId('max-subagent-invocations-input')
  const batchSizeInput = page.getByTestId('max-subagent-batch-size-input')

  await depthInput.fill('5')
  await invocationsInput.fill('200')
  await batchSizeInput.fill('16')

  await expect(depthInput).toHaveValue('5')
  await expect(invocationsInput).toHaveValue('200')
  await expect(batchSizeInput).toHaveValue('16')
})

// ---------------------------------------------------------------------------
// Test 6: Dispatch-fallback select: default reprompt, switching to other values
// ---------------------------------------------------------------------------

test('dispatch-fallback select defaults to reprompt and updates on change', async ({ page }) => {
  await page.goto('/strategies/new')

  const fallbackSelect = page.getByTestId('dispatch-fallback-select')

  // Default is reprompt
  await expect(fallbackSelect).toHaveValue('reprompt')

  // Switch to programmatic
  await fallbackSelect.selectOption('programmatic')
  await expect(fallbackSelect).toHaveValue('programmatic')

  // Switch to none
  await fallbackSelect.selectOption('none')
  await expect(fallbackSelect).toHaveValue('none')
})

// ---------------------------------------------------------------------------
// Test 7: Output-type-name select defaults to empty, updates on change
// ---------------------------------------------------------------------------

test('output-type-name select defaults to empty and accepts finding_list', async ({ page }) => {
  await page.goto('/strategies/new')

  const outputTypeSelect = page.getByTestId('output-type-name-select')

  // Default is empty (none / free-form text option)
  await expect(outputTypeSelect).toHaveValue('')

  // Select finding_list
  await outputTypeSelect.selectOption('finding_list')
  await expect(outputTypeSelect).toHaveValue('finding_list')
})

// ---------------------------------------------------------------------------
// Test 8: Full happy path — subagent + custom caps + fallback + output type → save
// ---------------------------------------------------------------------------

test('save with subagent + custom caps + fallback + output type posts correct body and navigates', async ({ page }) => {
  let capturedBody: Record<string, unknown> | null = null

  // Per-test LIFO override to capture the POST body
  await page.route('**/api/strategies', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    capturedBody = route.request().postDataJSON() as Record<string, unknown>
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(CREATED_HIERARCHY),
    })
  })

  await page.goto('/strategies/new')
  await expect(page.getByRole('heading', { name: 'New Strategy' })).toBeVisible()
  await expect(page.getByTestId('subagents-list')).toBeVisible()

  // Fill name
  await page.getByTestId('name-input').fill('Hierarchy Test')
  await expect(page.getByTestId('save-btn')).toBeEnabled()

  // Check the first subagent
  await page.getByTestId(`subagent-checkbox-${STRATEGY_HELPER.id}`).check()
  await expect(page.getByText('1 subagent selected')).toBeVisible()

  // Set custom caps
  await page.getByTestId('max-subagent-depth-input').fill('5')
  await page.getByTestId('max-subagent-invocations-input').fill('200')
  await page.getByTestId('max-subagent-batch-size-input').fill('16')

  // Set dispatch fallback
  await page.getByTestId('dispatch-fallback-select').selectOption('programmatic')

  // Set output type
  await page.getByTestId('output-type-name-select').selectOption('finding_list')

  // Save
  await page.getByTestId('save-btn').click()

  // Should navigate to the new strategy's viewer
  await expect(page).toHaveURL(/\/strategies\/strat-new-hierarchy-001/)

  // Assert POST body shape
  expect(capturedBody).not.toBeNull()
  expect(capturedBody!['name']).toBe('Hierarchy Test')

  const defaultBundle = capturedBody!['default'] as Record<string, unknown>
  expect(defaultBundle).toBeTruthy()

  // Subagents array contains the chosen strategy ID
  expect(Array.isArray(defaultBundle['subagents'])).toBe(true)
  expect((defaultBundle['subagents'] as string[])).toContain(STRATEGY_HELPER.id)

  // Custom caps
  expect(defaultBundle['max_subagent_depth']).toBe(5)
  expect(defaultBundle['max_subagent_invocations']).toBe(200)
  expect(defaultBundle['max_subagent_batch_size']).toBe(16)

  // Dispatch fallback
  expect(defaultBundle['dispatch_fallback']).toBe('programmatic')

  // Output type name
  expect(defaultBundle['output_type_name']).toBe('finding_list')
})

// ---------------------------------------------------------------------------
// Test 9: No-subagents save uses defaults and still navigates correctly
// ---------------------------------------------------------------------------

test('saving with no subagents selected and default caps posts subagents=[] and navigates', async ({ page }) => {
  let capturedBody: Record<string, unknown> | null = null

  // Per-test LIFO override returning CREATED_MINIMAL
  await page.route('**/api/strategies', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    capturedBody = route.request().postDataJSON() as Record<string, unknown>
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(CREATED_MINIMAL),
    })
  })

  await page.goto('/strategies/new')
  await expect(page.getByRole('heading', { name: 'New Strategy' })).toBeVisible()

  // Fill name only — do not check any subagents, keep default caps
  await page.getByTestId('name-input').fill('Minimal Strategy')
  await expect(page.getByTestId('save-btn')).toBeEnabled()

  // Verify defaults are intact before saving
  await expect(page.getByTestId('max-subagent-depth-input')).toHaveValue('3')
  await expect(page.getByTestId('max-subagent-invocations-input')).toHaveValue('100')
  await expect(page.getByTestId('max-subagent-batch-size-input')).toHaveValue('32')

  // Save
  await page.getByTestId('save-btn').click()

  // Should navigate to the new strategy's viewer
  await expect(page).toHaveURL(/\/strategies\/strat-new-hierarchy-001/)

  // Assert subagents is an empty array in the POST body
  expect(capturedBody).not.toBeNull()
  const defaultBundle = capturedBody!['default'] as Record<string, unknown>
  expect(Array.isArray(defaultBundle['subagents'])).toBe(true)
  expect((defaultBundle['subagents'] as string[]).length).toBe(0)
})
