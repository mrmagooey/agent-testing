/**
 * e2e spec: strategy-fork-edit
 *
 * Covers the user story:
 *   As a security researcher, I want to fork a builtin strategy from its viewer
 *   page, change the strategy name and one or two `default` fields, then save —
 *   landing on the new strategy's viewer with the updated values persisted.
 *
 * Mocking strategy:
 *   mockApi() registers a legacy GET /api/strategies handler returning a string
 *   array. We override it with page.route() handlers registered AFTER mockApi()
 *   — Playwright routes are LIFO so our handlers win.
 *
 *   The validate endpoint (POST /api/strategies/__new__/validate) and the
 *   create endpoint (POST /api/strategies) are also mocked. For the error-path
 *   test we layer an additional per-test override on top.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const STRATEGY_BUILTIN_SINGLE: import('../src/api/strategies').StrategySummary = {
  id: 'strat-builtin-single-001',
  name: 'Zero Shot Single Agent',
  orchestration_shape: 'single_agent',
  is_builtin: true,
  parent_strategy_id: null,
}

const STRATEGY_USER_PER_VULN: import('../src/api/strategies').StrategySummary = {
  id: 'strat-user-per-vuln-002',
  name: 'Custom Per-VulnClass Strategy',
  orchestration_shape: 'per_vuln_class',
  is_builtin: false,
  parent_strategy_id: 'strat-builtin-single-001',
}

const ALL_STRATEGY_SUMMARIES = [STRATEGY_BUILTIN_SINGLE, STRATEGY_USER_PER_VULN]

// Full UserStrategy for the builtin single_agent strategy — parent for fork tests.
// Note: max_subagent_depth/invocations/batch_size must be >= 1 to satisfy the
// min={1} attribute on the number inputs in BundleDefaultForm; values of 0
// would cause HTML5 form validation to block submission.
const FULL_BUILTIN_SINGLE: import('../src/api/strategies').UserStrategy = {
  id: 'strat-builtin-single-001',
  name: 'Zero Shot Single Agent',
  parent_strategy_id: null,
  orchestration_shape: 'single_agent',
  is_builtin: true,
  created_at: '2025-01-01T00:00:00Z',
  default: {
    system_prompt: 'You are a security expert.',
    user_prompt_template: 'Review the following code for vulnerabilities:\n\n{{code}}',
    profile_modifier: '',
    model_id: 'gpt-4o',
    tools: ['read_file', 'search_code'],
    verification: 'none',
    max_turns: 20,
    tool_extensions: [],
    subagents: [],
    max_subagent_depth: 3,
    max_subagent_invocations: 100,
    max_subagent_batch_size: 32,
    dispatch_fallback: 'none',
    output_type_name: 'finding_list',
  },
  overrides: [],
}

// The newly created strategy returned by POST /api/strategies
const CREATED_FORK: import('../src/api/strategies').UserStrategy = {
  id: 'strat-new-fork-001',
  name: 'My Custom Strategy',
  parent_strategy_id: 'strat-builtin-single-001',
  orchestration_shape: 'single_agent',
  is_builtin: false,
  created_at: '2026-01-01T00:00:00Z',
  default: {
    system_prompt: 'You are a security expert.',
    user_prompt_template: 'Review the following code for vulnerabilities:\n\n{{code}}',
    profile_modifier: '',
    model_id: 'claude-3-5-sonnet-20241022',
    tools: ['read_file', 'search_code'],
    verification: 'none',
    max_turns: 20,
    tool_extensions: [],
    subagents: [],
    max_subagent_depth: 3,
    max_subagent_invocations: 100,
    max_subagent_batch_size: 32,
    dispatch_fallback: 'none',
    output_type_name: 'finding_list',
  },
  overrides: [],
}

// Map used by the GET /api/strategies/<id> handler
const STRATEGY_FULL_BY_ID: Record<string, import('../src/api/strategies').UserStrategy> = {
  'strat-builtin-single-001': FULL_BUILTIN_SINGLE,
  // The viewer for the newly-created fork needs to load after redirect
  'strat-new-fork-001': CREATED_FORK,
}

// ---------------------------------------------------------------------------
// Helper: wire up strategy mock routes AFTER mockApi()
// Routes are LIFO in Playwright — these take priority over the legacy handler.
// ---------------------------------------------------------------------------

async function mockStrategiesRoutes(page: import('@playwright/test').Page) {
  // GET /api/strategies → StrategySummary[]  (overrides the legacy string-array handler)
  await page.route('**/api/strategies', (route) => {
    const method = route.request().method()
    if (method !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(ALL_STRATEGY_SUMMARIES),
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

  // POST /api/strategies → echo body's name/shape into a full UserStrategy
  await page.route('**/api/strategies', (route) => {
    const method = route.request().method()
    if (method !== 'POST') return route.fallback()
    const body = route.request().postDataJSON() as Partial<import('../src/api/strategies').UserStrategy>
    const created: import('../src/api/strategies').UserStrategy = {
      ...CREATED_FORK,
      name: body.name ?? CREATED_FORK.name,
      orchestration_shape: body.orchestration_shape ?? CREATED_FORK.orchestration_shape,
    }
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(created),
    })
  })

  // GET /api/strategies/<id> → UserStrategy  (single resource)
  await page.route('**/api/strategies/*', (route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()

    // Skip the validate sub-resource — handled by the more specific route above
    if (url.pathname.endsWith('/validate')) return route.fallback()

    if (method !== 'GET') return route.fallback()

    const id = decodeURIComponent(url.pathname.split('/').pop() ?? '')
    const strategy = STRATEGY_FULL_BY_ID[id]
    if (strategy) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(strategy),
      })
    }
    return route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Strategy not found' }),
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
// Test 1: Fork button navigates to /strategies/<id>/fork with correct heading
//         and pre-seeded form state
// ---------------------------------------------------------------------------

test('fork button navigates to /strategies/<id>/fork, heading reads "Fork Strategy", form is pre-seeded', async ({ page }) => {
  await page.goto('/strategies/strat-builtin-single-001')
  await expect(page.getByRole('heading', { name: 'Zero Shot Single Agent' })).toBeVisible()

  await page.getByTestId('fork-btn').click()

  await expect(page).toHaveURL(/\/strategies\/strat-builtin-single-001\/fork/)
  await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

  // Name field pre-seeded as "Fork of <parent name>"
  await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

  // Parent ID visible in the "Parent: <id>" metadata line (uses font-mono class).
  // Use a text regex to avoid strict-mode collision with the breadcrumb which
  // renders "Fork strat-builtin-single-001".
  await expect(page.getByText(/Parent:.*strat-builtin-single-001/)).toBeVisible()

  // Save button is enabled (name is non-empty)
  await expect(page.getByTestId('save-btn')).toBeEnabled()
})

// ---------------------------------------------------------------------------
// Test 2: Shape select reflects parent's orchestration_shape
// ---------------------------------------------------------------------------

test('shape select in fork editor reflects the parent strategy orchestration_shape', async ({ page }) => {
  await page.goto('/strategies/strat-builtin-single-001/fork')
  await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

  // Parent is single_agent — the shape select should show that
  await expect(page.getByTestId('shape-select')).toHaveValue('single_agent')
})

// ---------------------------------------------------------------------------
// Test 3: Empty name keeps Save button disabled (required attr)
// ---------------------------------------------------------------------------

test('clearing the name input disables the Save button while empty', async ({ page }) => {
  await page.goto('/strategies/strat-builtin-single-001/fork')
  await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

  const nameInput = page.getByTestId('name-input')
  const saveBtn = page.getByTestId('save-btn')

  // Initially non-empty → Save enabled
  await expect(saveBtn).toBeEnabled()

  // Clear the name
  await nameInput.fill('')

  // Save must now be disabled
  await expect(saveBtn).toBeDisabled()
})

// ---------------------------------------------------------------------------
// Test 4: Happy path — fork, change name, save → redirect to viewer
// ---------------------------------------------------------------------------

test('happy path: fork builtin, change name and model_id, save navigates to new strategy viewer', async ({ page }) => {
  // Intercept POST /api/strategies to capture the request body and assert its shape.
  // This per-test handler is registered last (LIFO = highest priority).
  let capturedBody: Record<string, unknown> | null = null

  await page.route('**/api/strategies', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    capturedBody = route.request().postDataJSON() as Record<string, unknown>
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(CREATED_FORK),
    })
  })

  await page.goto('/strategies/strat-builtin-single-001/fork')
  await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()
  // Wait for the form to be fully pre-seeded before interacting
  await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

  // Change name
  await page.getByTestId('name-input').fill('My Custom Strategy')
  await expect(page.getByTestId('name-input')).toHaveValue('My Custom Strategy')

  // Change model_id via the placeholder-identified input in BundleDefaultForm
  const modelInput = page.getByPlaceholder('e.g. claude-sonnet-4-5')
  await modelInput.fill('claude-3-5-sonnet-20241022')

  // Click Save
  await page.getByTestId('save-btn').click()

  // Should navigate to the new strategy's viewer
  await expect(page).toHaveURL(/\/strategies\/strat-new-fork-001/)
  await expect(page.getByRole('heading', { name: 'My Custom Strategy' })).toBeVisible()

  // Assert the POST body had the expected shape
  expect(capturedBody).not.toBeNull()
  expect(capturedBody!['name']).toBe('My Custom Strategy')
  expect(capturedBody!['orchestration_shape']).toBe('single_agent')
  expect(capturedBody!['parent_strategy_id']).toBe('strat-builtin-single-001')
  expect(capturedBody!['default']).toBeTruthy()
  expect(capturedBody!['overrides']).toBeTruthy()
})

// ---------------------------------------------------------------------------
// Test 5: /strategies/new loads editor with heading "New Strategy", empty name,
//         single_agent default shape
// ---------------------------------------------------------------------------

test('/strategies/new loads editor with "New Strategy" heading and empty name field', async ({ page }) => {
  await page.goto('/strategies/new')

  await expect(page.getByRole('heading', { name: 'New Strategy' })).toBeVisible()

  // Name input is empty
  await expect(page.getByTestId('name-input')).toHaveValue('')

  // Default shape is single_agent
  await expect(page.getByTestId('shape-select')).toHaveValue('single_agent')

  // Save is disabled because name is empty
  await expect(page.getByTestId('save-btn')).toBeDisabled()
})

// ---------------------------------------------------------------------------
// Test 6: API 400 error surfaces in the red error block
// ---------------------------------------------------------------------------

test('API 400 on save surfaces the error detail in the error block', async ({ page }) => {
  // Override just POST /api/strategies to return 400 for this test.
  // Use route.fallback() for non-POST so earlier GET handlers still work.
  await page.route('**/api/strategies', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    return route.fulfill({
      status: 400,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Strategy name already exists' }),
    })
  })

  await page.goto('/strategies/strat-builtin-single-001/fork')
  await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()
  // Wait for form to be fully pre-seeded before attempting save
  await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

  // Name is pre-seeded — keep it and attempt save
  await page.getByTestId('save-btn').click()

  // The red error block should appear with the API's detail message.
  // apiFetch extracts body.detail as the error message for non-2xx responses.
  await expect(page.getByText('Strategy name already exists')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 7: Switching shape to per_vuln_class reveals the Overrides section
//         with vuln-class tabs; at minimum sqli tab should appear
// ---------------------------------------------------------------------------

test('switching shape to per_vuln_class reveals Overrides section with vuln-class tabs', async ({ page }) => {
  await page.goto('/strategies/strat-builtin-single-001/fork')
  await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

  // Overrides section must not exist for single_agent
  await expect(page.getByRole('heading', { name: /Overrides/ })).not.toBeVisible()

  // Change shape to per_vuln_class
  await page.getByTestId('shape-select').selectOption('per_vuln_class')

  // Overrides section should now be visible
  await expect(page.getByRole('heading', { name: /Overrides/ })).toBeVisible()

  // The sqli tab (from VULN_CLASSES) should be present
  await expect(page.getByRole('tab', { name: 'sqli' })).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 8: Strategies breadcrumb in the editor navigates back to /strategies
// ---------------------------------------------------------------------------

test('Strategies breadcrumb button in the editor navigates back to /strategies', async ({ page }) => {
  await page.goto('/strategies/strat-builtin-single-001/fork')
  await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

  // The breadcrumb renders as a button with accessible text "Strategies"
  await page.getByRole('button', { name: 'Strategies' }).click()

  await expect(page).toHaveURL('/strategies')
  await expect(page.getByRole('heading', { name: 'Strategies' })).toBeVisible()
})
