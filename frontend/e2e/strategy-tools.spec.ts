/**
 * e2e spec: strategy-tools
 *
 * Covers the user story:
 *   As a security researcher authoring a strategy, I want to quickly toggle
 *   which builtin tools are available to the agent — with clear visual feedback
 *   for selected vs. unselected — so the POST body's `tools` array reflects
 *   exactly what I picked, including pre-existing tools inherited from the
 *   fork's parent.
 *
 * Key production facts:
 *   - COMMON_TOOLS = ['read_file', 'list_directory', 'search_files', 'run_command', 'write_file']
 *   - The fixture's parent tools are ['read_file', 'search_code']. Only 'read_file' is in
 *     COMMON_TOOLS so it gets an amber button. 'search_code' is invisible but persists on save.
 *   - Active button class: bg-amber-100  Inactive: bg-white
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

const ALL_STRATEGY_SUMMARIES = [STRATEGY_BUILTIN_SINGLE]

// Parent fixture: tools include 'read_file' (in COMMON_TOOLS) and 'search_code' (not in COMMON_TOOLS).
// subagent_* values are >= 1 to satisfy HTML5 min={1} constraint on number inputs.
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

const CREATED_FORK: import('../src/api/strategies').UserStrategy = {
  id: 'strat-new-fork-001',
  name: 'Fork of Zero Shot Single Agent',
  parent_strategy_id: 'strat-builtin-single-001',
  orchestration_shape: 'single_agent',
  is_builtin: false,
  created_at: '2026-01-01T00:00:00Z',
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

const STRATEGY_FULL_BY_ID: Record<string, import('../src/api/strategies').UserStrategy> = {
  'strat-builtin-single-001': FULL_BUILTIN_SINGLE,
  'strat-new-fork-001': CREATED_FORK,
}

// ---------------------------------------------------------------------------
// Helper: wire up strategy mock routes AFTER mockApi()
// Routes are LIFO in Playwright — these take priority over the legacy handler.
// Copied verbatim from strategy-fork-edit.spec.ts lines 113-178.
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
// Tests
// ---------------------------------------------------------------------------

test.describe('StrategyEditor Tools toggles', () => {
  // -------------------------------------------------------------------------
  // Test 1: All 5 COMMON_TOOLS render as buttons in the Tools section
  // -------------------------------------------------------------------------

  test('all 5 COMMON_TOOLS render as buttons in the Tools section', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    // Each tool name is unique on the page as an exact button label in the Tools section.
    // exact:true avoids partial collisions if tool names appear elsewhere (e.g. system prompt).
    await expect(page.getByRole('button', { name: 'read_file', exact: true })).toHaveCount(1)
    await expect(page.getByRole('button', { name: 'list_directory', exact: true })).toHaveCount(1)
    await expect(page.getByRole('button', { name: 'search_files', exact: true })).toHaveCount(1)
    await expect(page.getByRole('button', { name: 'run_command', exact: true })).toHaveCount(1)
    await expect(page.getByRole('button', { name: 'write_file', exact: true })).toHaveCount(1)
  })

  // -------------------------------------------------------------------------
  // Test 2: Pre-existing parent tools are styled as active (amber), others inactive
  // -------------------------------------------------------------------------

  test('pre-existing parent tool read_file is amber-active; other 4 COMMON_TOOLS are inactive', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    // 'read_file' is in the parent's tools array → should be amber
    await expect(page.getByRole('button', { name: 'read_file', exact: true })).toHaveClass(/bg-amber-100/)

    // The other 4 COMMON_TOOLS are not in the parent fixture → should be inactive (bg-white)
    await expect(page.getByRole('button', { name: 'list_directory', exact: true })).toHaveClass(/bg-white/)
    await expect(page.getByRole('button', { name: 'search_files', exact: true })).toHaveClass(/bg-white/)
    await expect(page.getByRole('button', { name: 'run_command', exact: true })).toHaveClass(/bg-white/)
    await expect(page.getByRole('button', { name: 'write_file', exact: true })).toHaveClass(/bg-white/)
  })

  // -------------------------------------------------------------------------
  // Test 3: Clicking an inactive button toggles it to active
  // -------------------------------------------------------------------------

  test('clicking an inactive tool button toggles it to amber-active', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const writeFileBtn = page.getByRole('button', { name: 'write_file', exact: true })

    // Initially inactive
    await expect(writeFileBtn).toHaveClass(/bg-white/)

    await writeFileBtn.click()

    // Now active
    await expect(writeFileBtn).toHaveClass(/bg-amber-100/)
  })

  // -------------------------------------------------------------------------
  // Test 4: Clicking an active button toggles it back to inactive
  // -------------------------------------------------------------------------

  test('clicking an active tool button toggles it back to inactive', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const readFileBtn = page.getByRole('button', { name: 'read_file', exact: true })

    // Initially active (from parent fixture)
    await expect(readFileBtn).toHaveClass(/bg-amber-100/)

    await readFileBtn.click()

    // Now inactive — no amber class
    await expect(readFileBtn).not.toHaveClass(/bg-amber-100/)
    await expect(readFileBtn).toHaveClass(/bg-white/)
  })

  // -------------------------------------------------------------------------
  // Test 5: Multiple selections are retained independently
  // -------------------------------------------------------------------------

  test('clicking list_directory then run_command makes both active; others stay inactive', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    await page.getByRole('button', { name: 'list_directory', exact: true }).click()
    await page.getByRole('button', { name: 'run_command', exact: true }).click()

    await expect(page.getByRole('button', { name: 'list_directory', exact: true })).toHaveClass(/bg-amber-100/)
    await expect(page.getByRole('button', { name: 'run_command', exact: true })).toHaveClass(/bg-amber-100/)

    // These were not clicked — list_directory and run_command are newly active,
    // search_files and write_file remain inactive.
    await expect(page.getByRole('button', { name: 'search_files', exact: true })).toHaveClass(/bg-white/)
    await expect(page.getByRole('button', { name: 'write_file', exact: true })).toHaveClass(/bg-white/)
  })

  // -------------------------------------------------------------------------
  // Test 6: Save POST body reflects toggles AND preserves invisible search_code
  // -------------------------------------------------------------------------

  test("save POST body's default.tools reflects toggles and preserves invisible non-COMMON tool search_code", async ({ page }) => {
    let capturedBody: Record<string, unknown> | null = null

    // LIFO: this per-test override wins over the base mockStrategiesRoutes handler
    await page.route('**/api/strategies', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      capturedBody = route.request().postDataJSON()
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(CREATED_FORK),
      })
    })

    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()
    // Wait for name pre-seed so the form is fully hydrated before interacting
    await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

    // Deactivate read_file (was active from parent)
    await page.getByRole('button', { name: 'read_file', exact: true }).click()
    await expect(page.getByRole('button', { name: 'read_file', exact: true })).toHaveClass(/bg-white/)

    // Activate write_file (was inactive)
    await page.getByRole('button', { name: 'write_file', exact: true }).click()
    await expect(page.getByRole('button', { name: 'write_file', exact: true })).toHaveClass(/bg-amber-100/)

    // Fill name (required for save button to be enabled)
    await page.getByTestId('name-input').fill('Tools Toggle Test')

    await page.getByTestId('save-btn').click()
    await expect(page).toHaveURL(/\/strategies\/strat-new-fork-001/)

    expect(capturedBody).not.toBeNull()

    const defaultBundle = capturedBody!['default'] as Record<string, unknown>
    const tools = defaultBundle['tools'] as string[]

    // read_file was deactivated — must not be present
    expect(tools).not.toContain('read_file')

    // write_file was activated — must be present
    expect(tools).toContain('write_file')

    // search_code is invisible (not in COMMON_TOOLS) but must persist on save
    expect(tools).toContain('search_code')

    // Consolidate: exactly write_file and search_code, no leaked extras
    expect(tools).toEqual(expect.arrayContaining(['write_file', 'search_code']))
    expect(tools).toHaveLength(2)
  })

  // -------------------------------------------------------------------------
  // Test 7: Hidden non-COMMON tool (search_code) persists on save with no toggle
  // -------------------------------------------------------------------------

  test('hidden non-COMMON tool search_code persists in POST body when no tool is toggled', async ({ page }) => {
    let capturedBody: Record<string, unknown> | null = null

    await page.route('**/api/strategies', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      capturedBody = route.request().postDataJSON()
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(CREATED_FORK),
      })
    })

    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()
    await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

    // No tool toggles — save as-is
    await page.getByTestId('save-btn').click()
    await expect(page).toHaveURL(/\/strategies\/strat-new-fork-001/)

    expect(capturedBody).not.toBeNull()

    const defaultBundle = capturedBody!['default'] as Record<string, unknown>
    const tools = defaultBundle['tools'] as string[]

    // Both the COMMON parent tool and the hidden non-COMMON tool must persist
    expect(tools).toContain('read_file')
    expect(tools).toContain('search_code')
  })
})
