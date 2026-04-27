/**
 * e2e spec: strategy-rule-list
 *
 * Covers the user story:
 *   As a security researcher authoring a per_file strategy, I want to add multiple
 *   glob-pattern override rules, edit each rule's key, reorder rules with ↑/↓
 *   buttons, and remove rules — so I can express first-match-wins routing of files
 *   to different bundle overrides before saving.
 *
 * Mocking strategy:
 *   mockApi() registers a legacy GET /api/strategies handler returning a string
 *   array. We override it with page.route() handlers registered AFTER mockApi()
 *   — Playwright routes are LIFO so our handlers win.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CREATED_STRATEGY: import('../src/api/strategies').UserStrategy = {
  id: 'strat-new-per-file-001',
  name: 'My Per-File Strategy',
  parent_strategy_id: null,
  orchestration_shape: 'per_file',
  is_builtin: false,
  created_at: '2026-01-01T00:00:00Z',
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
  // GET /api/strategies → StrategySummary[] (overrides legacy string-array handler)
  await page.route('**/api/strategies', (route) => {
    const method = route.request().method()
    if (method !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
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

  // POST /api/strategies → echo body into a full UserStrategy
  await page.route('**/api/strategies', (route) => {
    const method = route.request().method()
    if (method !== 'POST') return route.fallback()
    const body = route.request().postDataJSON() as Partial<import('../src/api/strategies').UserStrategy>
    const created: import('../src/api/strategies').UserStrategy = {
      ...CREATED_STRATEGY,
      name: body.name ?? CREATED_STRATEGY.name,
      orchestration_shape: body.orchestration_shape ?? CREATED_STRATEGY.orchestration_shape,
      overrides: body.overrides ?? [],
    }
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(created),
    })
  })

  // GET /api/strategies/<id> → return CREATED_STRATEGY so the post-save redirect works
  await page.route('**/api/strategies/*', (route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()
    if (url.pathname.endsWith('/validate')) return route.fallback()
    if (method !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(CREATED_STRATEGY),
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
// Test 1: Switching shape to per_file reveals the Overrides section
// ---------------------------------------------------------------------------

test('switching shape to per_file reveals Overrides heading and Add rule button', async ({ page }) => {
  await page.goto('/strategies/new')
  await expect(page.getByRole('heading', { name: 'New Strategy' })).toBeVisible()

  // Overrides section must not exist for the default single_agent shape
  await expect(page.getByRole('heading', { name: /Overrides/ })).not.toBeVisible()

  // Switch to per_file
  await page.getByTestId('shape-select').selectOption('per_file')

  // Overrides heading should now be visible
  await expect(page.getByRole('heading', { name: /Overrides/ })).toBeVisible()

  // Add rule button should be visible
  await expect(page.getByTestId('add-rule-btn')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 2: Initially zero rules; Add rule adds rows one at a time
// ---------------------------------------------------------------------------

test('initially zero rule rows; clicking Add rule once adds one row, twice adds two', async ({ page }) => {
  await page.goto('/strategies/new')
  await page.getByTestId('shape-select').selectOption('per_file')

  // No rules yet
  await expect(page.getByTestId('override-rule')).toHaveCount(0)

  // Add first rule
  await page.getByTestId('add-rule-btn').click()
  await expect(page.getByTestId('override-rule')).toHaveCount(1)

  // The first rule's key input should be empty
  const firstRule = page.getByTestId('override-rule').nth(0)
  await expect(firstRule.getByTestId('rule-key-input')).toHaveValue('')

  // Add second rule
  await page.getByTestId('add-rule-btn').click()
  await expect(page.getByTestId('override-rule')).toHaveCount(2)
})

// ---------------------------------------------------------------------------
// Test 3: Typing into rule-key-input updates value and GlobPreview shows match count
// ---------------------------------------------------------------------------

test('typing a glob pattern into rule-key-input updates the value and GlobPreview shows match count', async ({ page }) => {
  await page.goto('/strategies/new')
  await page.getByTestId('shape-select').selectOption('per_file')
  await page.getByTestId('add-rule-btn').click()

  const rule = page.getByTestId('override-rule').nth(0)
  const keyInput = rule.getByTestId('rule-key-input')

  // Type a pattern that matches sample files
  await keyInput.fill('src/auth/**')
  await expect(keyInput).toHaveValue('src/auth/**')

  // GlobPreview should show a non-zero match count
  // src/auth/login.py and src/auth/oauth.py match src/auth/**  → "Matches 2 sample files"
  await expect(rule.getByText(/Matches \d+ sample file/)).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 4: Single rule — both move-up and move-down are disabled
// ---------------------------------------------------------------------------

test('with a single rule, move-up and move-down buttons are both disabled', async ({ page }) => {
  await page.goto('/strategies/new')
  await page.getByTestId('shape-select').selectOption('per_file')
  await page.getByTestId('add-rule-btn').click()

  const rule = page.getByTestId('override-rule').nth(0)

  await expect(rule.getByTestId('move-up-btn')).toBeDisabled()
  await expect(rule.getByTestId('move-down-btn')).toBeDisabled()
})

// ---------------------------------------------------------------------------
// Test 5: Two rules A, B — move-up on second reorders to B, A; move-down on first restores A, B
// ---------------------------------------------------------------------------

test('two rules: move-up on second reorders to B,A; move-down on first restores A,B', async ({ page }) => {
  await page.goto('/strategies/new')
  await page.getByTestId('shape-select').selectOption('per_file')

  // Add two rules
  await page.getByTestId('add-rule-btn').click()
  await page.getByTestId('add-rule-btn').click()

  const rules = page.getByTestId('override-rule')

  // Set key values A and B
  await rules.nth(0).getByTestId('rule-key-input').fill('A')
  await rules.nth(1).getByTestId('rule-key-input').fill('B')

  // Verify initial order: A, B
  await expect(rules.nth(0).getByTestId('rule-key-input')).toHaveValue('A')
  await expect(rules.nth(1).getByTestId('rule-key-input')).toHaveValue('B')

  // Click move-up on the second rule → order becomes B, A
  await rules.nth(1).getByTestId('move-up-btn').click()
  await expect(rules.nth(0).getByTestId('rule-key-input')).toHaveValue('B')
  await expect(rules.nth(1).getByTestId('rule-key-input')).toHaveValue('A')

  // Click move-down on the first rule → order returns to A, B
  await rules.nth(0).getByTestId('move-down-btn').click()
  await expect(rules.nth(0).getByTestId('rule-key-input')).toHaveValue('A')
  await expect(rules.nth(1).getByTestId('rule-key-input')).toHaveValue('B')
})

// ---------------------------------------------------------------------------
// Test 6: Three rules — first move-up and last move-down are disabled; middle both enabled
// ---------------------------------------------------------------------------

test('three rules: first move-up disabled, last move-down disabled, middle both enabled', async ({ page }) => {
  await page.goto('/strategies/new')
  await page.getByTestId('shape-select').selectOption('per_file')

  // Add three rules
  await page.getByTestId('add-rule-btn').click()
  await page.getByTestId('add-rule-btn').click()
  await page.getByTestId('add-rule-btn').click()

  const rules = page.getByTestId('override-rule')

  // Set keys A, B, C
  await rules.nth(0).getByTestId('rule-key-input').fill('A')
  await rules.nth(1).getByTestId('rule-key-input').fill('B')
  await rules.nth(2).getByTestId('rule-key-input').fill('C')

  // First rule: move-up disabled, move-down enabled
  await expect(rules.nth(0).getByTestId('move-up-btn')).toBeDisabled()
  await expect(rules.nth(0).getByTestId('move-down-btn')).toBeEnabled()

  // Middle rule: both enabled
  await expect(rules.nth(1).getByTestId('move-up-btn')).toBeEnabled()
  await expect(rules.nth(1).getByTestId('move-down-btn')).toBeEnabled()

  // Last rule: move-up enabled, move-down disabled
  await expect(rules.nth(2).getByTestId('move-up-btn')).toBeEnabled()
  await expect(rules.nth(2).getByTestId('move-down-btn')).toBeDisabled()
})

// ---------------------------------------------------------------------------
// Test 7: Remove button removes only that row; surviving keys are preserved
// ---------------------------------------------------------------------------

test('removing the middle rule from three leaves two rows with the non-removed keys', async ({ page }) => {
  await page.goto('/strategies/new')
  await page.getByTestId('shape-select').selectOption('per_file')

  // Add three rules
  await page.getByTestId('add-rule-btn').click()
  await page.getByTestId('add-rule-btn').click()
  await page.getByTestId('add-rule-btn').click()

  const rules = page.getByTestId('override-rule')

  // Set distinct keys
  await rules.nth(0).getByTestId('rule-key-input').fill('src/auth/**')
  await rules.nth(1).getByTestId('rule-key-input').fill('src/db/**')
  await rules.nth(2).getByTestId('rule-key-input').fill('tests/**')

  // Remove the middle rule (index 1)
  await rules.nth(1).getByTestId('remove-rule-btn').click()

  // Should now have 2 rules
  await expect(page.getByTestId('override-rule')).toHaveCount(2)

  // Surviving rules should hold the first and third keys
  const remaining = page.getByTestId('override-rule')
  await expect(remaining.nth(0).getByTestId('rule-key-input')).toHaveValue('src/auth/**')
  await expect(remaining.nth(1).getByTestId('rule-key-input')).toHaveValue('tests/**')
})

// ---------------------------------------------------------------------------
// Test 8: Save with 2 rules — POST body contains overrides with correct keys in order
// ---------------------------------------------------------------------------

test('saving with 2 rules sends POST body with overrides array containing both keys in order', async ({ page }) => {
  // Per-test LIFO handler captures POST body
  let capturedBody: Record<string, unknown> | null = null

  await page.route('**/api/strategies', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    capturedBody = route.request().postDataJSON() as Record<string, unknown>
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(CREATED_STRATEGY),
    })
  })

  await page.goto('/strategies/new')
  await expect(page.getByRole('heading', { name: 'New Strategy' })).toBeVisible()

  // Fill strategy name (required — Save is disabled while empty)
  await page.getByTestId('name-input').fill('My Per-File Strategy')

  // Switch to per_file
  await page.getByTestId('shape-select').selectOption('per_file')

  // Add two rules
  await page.getByTestId('add-rule-btn').click()
  await page.getByTestId('add-rule-btn').click()

  const rules = page.getByTestId('override-rule')
  await rules.nth(0).getByTestId('rule-key-input').fill('src/auth/**')
  await rules.nth(1).getByTestId('rule-key-input').fill('*.py')

  // Save button should now be enabled
  await expect(page.getByTestId('save-btn')).toBeEnabled()
  await page.getByTestId('save-btn').click()

  // Wait for navigation to the new strategy viewer
  await expect(page).toHaveURL(/\/strategies\/strat-new-per-file-001/)

  // Assert the POST body shape
  expect(capturedBody).not.toBeNull()
  const overrides = capturedBody!['overrides'] as Array<{ key: string }>
  expect(overrides).toHaveLength(2)
  expect(overrides[0].key).toBe('src/auth/**')
  expect(overrides[1].key).toBe('*.py')
})

// ---------------------------------------------------------------------------
// Test 9: Switching from per_file back to single_agent hides the Overrides section
// ---------------------------------------------------------------------------

test('switching shape from per_file to single_agent hides the Overrides section', async ({ page }) => {
  await page.goto('/strategies/new')
  await expect(page.getByRole('heading', { name: 'New Strategy' })).toBeVisible()

  // Switch to per_file — Overrides should appear
  await page.getByTestId('shape-select').selectOption('per_file')
  await expect(page.getByRole('heading', { name: /Overrides/ })).toBeVisible()

  // Add a rule to verify it actually gets cleared
  await page.getByTestId('add-rule-btn').click()
  await expect(page.getByTestId('override-rule')).toHaveCount(1)

  // Switch back to single_agent — Overrides section must disappear
  await page.getByTestId('shape-select').selectOption('single_agent')
  await expect(page.getByRole('heading', { name: /Overrides/ })).not.toBeVisible()

  // Switching back to per_file should start with zero rules (overrides were cleared)
  await page.getByTestId('shape-select').selectOption('per_file')
  await expect(page.getByTestId('override-rule')).toHaveCount(0)
})
