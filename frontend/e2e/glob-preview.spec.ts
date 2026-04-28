/**
 * e2e spec: glob-preview
 *
 * Covers the user story:
 *   As a security researcher authoring a per_file strategy override, I want a
 *   live glob-match preview that shows exactly how many of the sample files my
 *   pattern matches, lists up to 3 examples (with a +N-more suffix when there
 *   are more), and uses singular/plural grammar correctly — so I can validate
 *   my pattern before saving.
 *
 * Mocking strategy:
 *   mockApi() registers a legacy GET /api/strategies handler. We override it
 *   with page.route() handlers registered AFTER mockApi() — Playwright routes
 *   are LIFO so our handlers win.
 *
 * Sample files used by GlobPreview (from frontend/src/api/strategies.ts):
 *   src/auth/login.py
 *   src/auth/oauth.py
 *   src/api/endpoints.py
 *   src/db/queries.py
 *   src/utils/crypto.py
 *   tests/test_auth.py
 *   tests/test_api.py
 *   frontend/src/App.tsx
 *   frontend/src/components/Login.tsx
 *   README.md
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CREATED_STRATEGY: import('../src/api/strategies').UserStrategy = {
  id: 'strat-glob-preview-001',
  name: 'Glob Preview Test Strategy',
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

async function mockStrategyRoutes(page: import('@playwright/test').Page) {
  await page.route('**/api/strategies', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  await page.route('**/api/strategies/__new__/validate', (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ valid: true, errors: [] }),
    })
  })

  await page.route('**/api/strategies', (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify(CREATED_STRATEGY),
    })
  })

  await page.route('**/api/strategies/*', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/validate')) return route.fallback()
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(CREATED_STRATEGY),
    })
  })
}

// ---------------------------------------------------------------------------
// Helper: navigate to /strategies/new, switch to per_file, add one rule,
// and return a locator scoped to that rule row.
// ---------------------------------------------------------------------------

async function setupPerFileRuleRow(page: import('@playwright/test').Page) {
  await page.goto('/strategies/new')
  await expect(page.getByRole('heading', { name: 'New Strategy' })).toBeVisible()
  await page.getByTestId('shape-select').selectOption('per_file')
  await page.getByTestId('add-rule-btn').click()
  await expect(page.getByTestId('override-rule')).toHaveCount(1)
  return page.getByTestId('override-rule').nth(0)
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await mockStrategyRoutes(page)
})

// ---------------------------------------------------------------------------
// Test 1: Empty pattern — preview element is absent
// ---------------------------------------------------------------------------

test('empty pattern shows no preview text', async ({ page }) => {
  const rule = await setupPerFileRuleRow(page)

  // The input starts empty — no preview paragraph should exist
  await expect(rule.locator('p').filter({ hasText: /Matches \d+ sample/ })).toHaveCount(0)
})

// ---------------------------------------------------------------------------
// Test 2: Whitespace-only pattern — preview element is absent
// ---------------------------------------------------------------------------

test('whitespace-only pattern shows no preview text', async ({ page }) => {
  const rule = await setupPerFileRuleRow(page)

  await rule.getByTestId('rule-key-input').fill('   ')

  // GlobPreview returns null for whitespace — no paragraph should appear
  await expect(rule.locator('p').filter({ hasText: /Matches \d+ sample/ })).toHaveCount(0)
})

// ---------------------------------------------------------------------------
// Test 3: Singular grammar — exactly 1 match
// README.md matches only 'README.md' from the sample list
// ---------------------------------------------------------------------------

test('pattern matching exactly 1 file uses singular grammar', async ({ page }) => {
  const rule = await setupPerFileRuleRow(page)

  await rule.getByTestId('rule-key-input').fill('README.md')

  const preview = rule.locator('p').filter({ hasText: /Matches \d+ sample/ })
  await expect(preview).toBeVisible()
  // Singular: "1 sample file" (no trailing 's'), followed by the file name
  await expect(preview).toContainText('Matches 1 sample file: README.md')
  // Must NOT contain "files" (plural)
  await expect(preview).not.toContainText('Matches 1 sample files')
})

// ---------------------------------------------------------------------------
// Test 4: Plural grammar with 0 matches — count shown, no listing
// ---------------------------------------------------------------------------

test('pattern matching 0 files shows plural grammar and no file listing', async ({ page }) => {
  const rule = await setupPerFileRuleRow(page)

  await rule.getByTestId('rule-key-input').fill('nonexistent/*.go')

  const preview = rule.locator('p').filter({ hasText: /Matches \d+ sample/ })
  await expect(preview).toBeVisible()
  await expect(preview).toContainText('Matches 0 sample files')
  // With 0 matches there should be no colon + file listing
  await expect(preview).not.toContainText(':')
})

// ---------------------------------------------------------------------------
// Test 5: Plural grammar with 2 matches
// **/*.tsx matches frontend/src/App.tsx and frontend/src/components/Login.tsx
// ---------------------------------------------------------------------------

test('pattern matching 2 files shows plural grammar and lists both files', async ({ page }) => {
  const rule = await setupPerFileRuleRow(page)

  await rule.getByTestId('rule-key-input').fill('**/*.tsx')

  const preview = rule.locator('p').filter({ hasText: /Matches \d+ sample/ })
  await expect(preview).toBeVisible()
  await expect(preview).toContainText('Matches 2 sample files: frontend/src/App.tsx, frontend/src/components/Login.tsx')
})

// ---------------------------------------------------------------------------
// Test 6: Truncation — more than 3 matches shows first 3 and a "+N more" suffix
// **/*.py matches 7 files → first 3 listed, "+4 more" suffix
//   src/auth/login.py, src/auth/oauth.py, src/api/endpoints.py (first 3)
//   src/db/queries.py, src/utils/crypto.py, tests/test_auth.py, tests/test_api.py (remaining 4)
// ---------------------------------------------------------------------------

test('pattern matching 7 files shows first 3 examples and +4 more suffix', async ({ page }) => {
  const rule = await setupPerFileRuleRow(page)

  await rule.getByTestId('rule-key-input').fill('**/*.py')

  const preview = rule.locator('p').filter({ hasText: /Matches \d+ sample/ })
  await expect(preview).toBeVisible()
  await expect(preview).toContainText('Matches 7 sample files')
  // First three in sample-file order
  await expect(preview).toContainText('src/auth/login.py, src/auth/oauth.py, src/api/endpoints.py')
  // Truncation suffix
  await expect(preview).toContainText('+4 more')
})

// ---------------------------------------------------------------------------
// Test 7: Live update — changing the pattern updates the preview immediately
// Start with README.md (1 match), change to **/*.tsx (2 matches)
// ---------------------------------------------------------------------------

test('changing the glob pattern live-updates the preview without page reload', async ({ page }) => {
  const rule = await setupPerFileRuleRow(page)
  const input = rule.getByTestId('rule-key-input')
  const preview = rule.locator('p').filter({ hasText: /Matches \d+ sample/ })

  // First pattern: 1 match
  await input.fill('README.md')
  await expect(preview).toContainText('Matches 1 sample file: README.md')

  // Change to a different pattern: 2 matches
  await input.fill('**/*.tsx')
  await expect(preview).toContainText('Matches 2 sample files')
  await expect(preview).toContainText('frontend/src/App.tsx')
  // Old text must be gone
  await expect(preview).not.toContainText('Matches 1 sample file')
})

// ---------------------------------------------------------------------------
// Test 8: Single-segment wildcard (*) does not cross directory separators
//
// src/*.py — none of the sample .py files live directly in src/, they all
// live one level deeper (src/auth/, src/api/, etc.) so this should match 0.
//
// src/*/*.py — a two-segment pattern captures exactly those 5 files:
//   src/auth/login.py, src/auth/oauth.py, src/api/endpoints.py,
//   src/db/queries.py, src/utils/crypto.py
// ---------------------------------------------------------------------------

test('single * does not cross directory separators; src/*/*.py matches 5 files', async ({ page }) => {
  const rule = await setupPerFileRuleRow(page)
  const input = rule.getByTestId('rule-key-input')
  const preview = rule.locator('p').filter({ hasText: /Matches \d+ sample/ })

  // src/*.py — should cross no slashes → 0 matches
  await input.fill('src/*.py')
  await expect(preview).toBeVisible()
  await expect(preview).toContainText('Matches 0 sample files')

  // src/*/*.py — two-segment match → 5 matches
  await input.fill('src/*/*.py')
  await expect(preview).toContainText('Matches 5 sample files')
  // First three listed
  await expect(preview).toContainText('src/auth/login.py, src/auth/oauth.py, src/api/endpoints.py')
  // +2 more for the remaining two
  await expect(preview).toContainText('+2 more')
})
