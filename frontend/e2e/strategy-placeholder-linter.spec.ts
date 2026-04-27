/**
 * e2e spec: strategy-placeholder-linter
 *
 * Covers the user story:
 *   As a security researcher authoring a strategy's user_prompt_template,
 *   I want a live placeholder linter that flags missing required placeholders
 *   ({repo_summary} and {finding_output_format}) in red and confirms the ones
 *   I've included in green.
 *
 * Production code under test: PlaceholderLinter in StrategyEditor.tsx lines 55-112.
 *
 * Key production behavior captured here:
 *   - Unknown (non-required) placeholders render as GREEN pills, not amber.
 *     The production code computes an `unknown` variable but never uses it in JSX.
 *   - Empty/whitespace-only templates return null (no linter element).
 *   - Regex /\{(\w+)\}/g matches {code} inside {{code}} (starts at index 1).
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Fixtures — copied from strategy-fork-edit.spec.ts
// ---------------------------------------------------------------------------

const STRATEGY_BUILTIN_SINGLE: import('../src/api/strategies').StrategySummary = {
  id: 'strat-builtin-single-001',
  name: 'Zero Shot Single Agent',
  orchestration_shape: 'single_agent',
  is_builtin: true,
  parent_strategy_id: null,
}

const ALL_STRATEGY_SUMMARIES = [STRATEGY_BUILTIN_SINGLE]

// Base parent fixture — user_prompt_template uses double-braces: {{code}}
// The regex /\{(\w+)\}/g matches {code} inside {{code}}, yielding one green pill.
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

// ---------------------------------------------------------------------------
// Helper: wire up strategy mock routes AFTER mockApi()
// Routes are LIFO in Playwright — these take priority over the legacy handler.
// ---------------------------------------------------------------------------

async function mockStrategiesRoutes(page: import('@playwright/test').Page) {
  // GET /api/strategies → StrategySummary[]
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

  // POST /api/strategies → 201 with a minimal fork body
  await page.route('**/api/strategies', (route) => {
    const method = route.request().method()
    if (method !== 'POST') return route.fallback()
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({ ...FULL_BUILTIN_SINGLE, id: 'strat-new-fork-001' }),
    })
  })

  // GET /api/strategies/<id> → UserStrategy
  await page.route('**/api/strategies/*', (route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()
    if (url.pathname.endsWith('/validate')) return route.fallback()
    if (method !== 'GET') return route.fallback()
    const id = decodeURIComponent(url.pathname.split('/').pop() ?? '')
    if (id === 'strat-builtin-single-001') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(FULL_BUILTIN_SINGLE),
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
// Helpers
// ---------------------------------------------------------------------------

// The User Prompt Template textarea in BundleDefaultForm.
// The label text matches via Playwright's role-based name resolution.
function getUserPromptTextarea(page: import('@playwright/test').Page) {
  return page.getByRole('textbox', { name: /User Prompt Template/i })
}

// Linter container — scoped to the div immediately following the textarea.
// We use the presence of bg-red-100 or bg-green-100 spans to locate it.
// Scoping prevents strict-mode collisions with textarea value text.
function getLinterContainer(page: import('@playwright/test').Page) {
  return page.locator('div.mt-1\\.5.text-xs')
}

// Red pill: text is "missing: {p}"
function getRedPill(page: import('@playwright/test').Page, placeholder: string) {
  return page.getByText(`missing: ${placeholder}`, { exact: true })
}

// Green pill: text is just "{p}" — scope to spans with bg-green-100 to avoid
// matching the same text inside the <textarea> element (textarea content is
// in the DOM as a text node but Playwright's getByText does still match it in
// some engines; scoping to <span> elements avoids any ambiguity).
function getGreenPill(page: import('@playwright/test').Page, placeholder: string) {
  // Escape regex metachars in `{p}` so both braces are literals
  const escaped = placeholder.replace(/[{}]/g, '\\$&')
  return page.locator('span').filter({ hasText: new RegExp(`^${escaped}$`) }).filter({
    has: page.locator('xpath=self::*[contains(@class,"bg-green-100") or contains(@class,"bg-green-950")]'),
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
// Tests
// ---------------------------------------------------------------------------

test.describe('PlaceholderLinter on StrategyEditor', () => {
  // -------------------------------------------------------------------------
  // Test 1: Empty template renders no linter element
  // -------------------------------------------------------------------------

  test('empty template renders no linter pills', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const textarea = getUserPromptTextarea(page)
    await expect(textarea).toBeVisible()

    // Clear the textarea — replaces the parent's {{code}} value with ''
    await textarea.fill('')

    // No red pills
    await expect(page.locator('span.bg-red-100, span.bg-red-950')).toHaveCount(0)
    // No green pills
    await expect(page.locator('span.bg-green-100, span.bg-green-950')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // Test 2: Whitespace-only template renders no linter element
  // -------------------------------------------------------------------------

  test('whitespace-only template renders no linter pills', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const textarea = getUserPromptTextarea(page)
    await textarea.fill('   \n  ')

    // Render guard: found.length === 0 && template.trim().length === 0 → null
    await expect(page.locator('span.bg-red-100, span.bg-red-950')).toHaveCount(0)
    await expect(page.locator('span.bg-green-100, span.bg-green-950')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // Test 3: Template missing both required placeholders → 2 red pills, 0 green
  // -------------------------------------------------------------------------

  test('template without any placeholders shows 2 red missing pills and no green pills', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const textarea = getUserPromptTextarea(page)
    await textarea.fill('Just a plain prompt with no placeholders.')

    await expect(getRedPill(page, '{repo_summary}')).toBeVisible()
    await expect(getRedPill(page, '{finding_output_format}')).toBeVisible()

    // No green pills
    await expect(page.locator('span.bg-green-100, span.bg-green-950')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // Test 4: Both required placeholders present → 2 green pills, 0 red
  // -------------------------------------------------------------------------

  test('template with both required placeholders shows 2 green pills and no red pills', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const textarea = getUserPromptTextarea(page)
    await textarea.fill('Repo: {repo_summary}\nFormat: {finding_output_format}')

    // No red (missing) pills
    await expect(page.locator('span.bg-red-100, span.bg-red-950')).toHaveCount(0)

    // Both required placeholders appear as green confirmed pills (no "missing:" prefix)
    await expect(page.getByText('{repo_summary}', { exact: true }).locator('xpath=self::span[contains(@class,"bg-green")]')).toBeVisible()
    await expect(page.getByText('{finding_output_format}', { exact: true }).locator('xpath=self::span[contains(@class,"bg-green")]')).toBeVisible()

    // No "missing:" text anywhere
    await expect(page.getByText(/missing:/)).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // Test 5: One required + one unknown → 1 red, 2 green
  //
  // This test captures the ACTUAL production behavior: unknown placeholders
  // render as green pills, not amber. The `unknown` variable computed in
  // PlaceholderLinter is never referenced in the JSX — the green branch simply
  // renders found.filter(p => !missing.includes(p)), which includes unknowns.
  // -------------------------------------------------------------------------

  test('template with one required and one unknown placeholder shows 1 red and 2 green pills (unknown renders green per actual production behavior)', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const textarea = getUserPromptTextarea(page)
    // {repo_summary} present; {finding_output_format} missing; {custom_field} unknown
    await textarea.fill('{repo_summary} and {custom_field}')

    // 1 red pill for the missing required placeholder
    await expect(getRedPill(page, '{finding_output_format}')).toBeVisible()
    // {repo_summary} is missing's complement → green
    await expect(page.getByText('{repo_summary}', { exact: true }).locator('xpath=self::span[contains(@class,"bg-green")]')).toBeVisible()
    // {custom_field} is unknown but NOT in missing → also green (production behavior)
    await expect(page.getByText('{custom_field}', { exact: true }).locator('xpath=self::span[contains(@class,"bg-green")]')).toBeVisible()

    // {repo_summary} is NOT missing → no red pill for it
    await expect(getRedPill(page, '{repo_summary}')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // Test 6: Live update — typing in the textarea updates the linter reactively
  // -------------------------------------------------------------------------

  test('typing into the textarea reactively updates linter pills', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const textarea = getUserPromptTextarea(page)

    // Start from a missing-both state
    await textarea.fill('Some text with no placeholders')

    // Both required placeholders are missing
    await expect(getRedPill(page, '{repo_summary}')).toBeVisible()
    await expect(getRedPill(page, '{finding_output_format}')).toBeVisible()

    // Type {repo_summary} into the textarea — append it
    await textarea.fill('Some text {repo_summary}')

    // {repo_summary} red pill disappears; its green pill appears
    await expect(getRedPill(page, '{repo_summary}')).toHaveCount(0)
    await expect(page.getByText('{repo_summary}', { exact: true }).locator('xpath=self::span[contains(@class,"bg-green")]')).toBeVisible()
    // {finding_output_format} still missing
    await expect(getRedPill(page, '{finding_output_format}')).toBeVisible()

    // Now add {finding_output_format} too
    await textarea.fill('Some text {repo_summary} {finding_output_format}')

    // Both red pills gone
    await expect(getRedPill(page, '{finding_output_format}')).toHaveCount(0)
    await expect(getRedPill(page, '{repo_summary}')).toHaveCount(0)
    // Both green pills present
    await expect(page.getByText('{repo_summary}', { exact: true }).locator('xpath=self::span[contains(@class,"bg-green")]')).toBeVisible()
    await expect(page.getByText('{finding_output_format}', { exact: true }).locator('xpath=self::span[contains(@class,"bg-green")]')).toBeVisible()
  })

  // -------------------------------------------------------------------------
  // Test 7: Default fork fixture — parent has {{code}} template
  //
  // The regex /\{(\w+)\}/g applied to '...{{code}}...' matches {code} (single-
  // braced substring starting at index 1 inside {{code}}). So found = ['{code}'],
  // neither required placeholder is present → 2 red pills + 1 green {code} pill.
  // -------------------------------------------------------------------------

  test('default parent fixture with {{code}} template shows 2 red pills and 1 green {code} pill', async ({ page }) => {
    // Load the fork page without modifying the textarea
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    // Wait for form pre-seeding from the parent fixture
    await expect(getUserPromptTextarea(page)).toBeVisible()

    // Both required placeholders absent → 2 red pills
    await expect(getRedPill(page, '{repo_summary}')).toBeVisible()
    await expect(getRedPill(page, '{finding_output_format}')).toBeVisible()

    // {code} matched by regex inside {{code}} → renders as green pill (single braces)
    await expect(page.getByText('{code}', { exact: true }).locator('xpath=self::span[contains(@class,"bg-green")]')).toBeVisible()
  })
})
