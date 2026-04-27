/**
 * e2e spec: strategy-vulnclass-overrides
 *
 * Covers the user story:
 *   As a security researcher authoring a per_vuln_class strategy, I want to
 *   switch to the SQLi tab, uncheck "Inherit from default" on the model_id
 *   field, set a different model, then switch to the XSS tab and override
 *   max_turns — so the resulting POST body carries overrides for both classes
 *   with only the changed fields, and other vuln-class tabs stay clean.
 *
 * Mocking strategy:
 *   mockApi() registers a legacy GET /api/strategies handler returning a string
 *   array. We override it with page.route() handlers registered AFTER mockApi()
 *   — Playwright routes are LIFO so our handlers win.
 *
 * Key production behavior for test 6 (re-check Inherit / amber dot):
 *   VulnClassOverrides.setOverride never removes an entry once created; it only
 *   updates the override object. So after all fields are cleared back to null
 *   via InheritToggle, the entry { key: 'xss', override: { max_turns: null } }
 *   persists in the rules array, and rules.some(r => r.key === 'xss') remains
 *   true — the amber dot stays visible even after the inherit toggle is re-checked.
 *
 * Inherit checkbox ordering in OverrideFieldEditor (zero-indexed within panel):
 *   0 = system_prompt
 *   1 = user_prompt_template
 *   2 = model_id
 *   3 = verification
 *   4 = max_turns
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const STRATEGY_BUILTIN_PERVULN: import('../src/api/strategies').StrategySummary = {
  id: 'strat-builtin-pervuln-001',
  name: 'Per-VulnClass Built-in',
  orchestration_shape: 'per_vuln_class',
  is_builtin: true,
  parent_strategy_id: null,
}

const ALL_STRATEGY_SUMMARIES = [STRATEGY_BUILTIN_PERVULN]

const FULL_BUILTIN_PERVULN: import('../src/api/strategies').UserStrategy = {
  id: 'strat-builtin-pervuln-001',
  name: 'Per-VulnClass Built-in',
  parent_strategy_id: null,
  orchestration_shape: 'per_vuln_class',
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
  id: 'strat-new-pervuln-fork-001',
  name: 'Fork of Per-VulnClass Built-in',
  parent_strategy_id: 'strat-builtin-pervuln-001',
  orchestration_shape: 'per_vuln_class',
  is_builtin: false,
  created_at: '2026-04-27T00:00:00Z',
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
  'strat-builtin-pervuln-001': FULL_BUILTIN_PERVULN,
  'strat-new-pervuln-fork-001': CREATED_FORK,
}

// ---------------------------------------------------------------------------
// Helper: wire up strategy mock routes AFTER mockApi()
// Routes are LIFO in Playwright — these take priority over the legacy handler.
// ---------------------------------------------------------------------------

async function mockStrategiesRoutes(page: import('@playwright/test').Page) {
  await page.route('**/api/strategies', (route) => {
    const method = route.request().method()
    if (method !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(ALL_STRATEGY_SUMMARIES),
    })
  })

  await page.route('**/api/strategies/__new__/validate', (route) => {
    const method = route.request().method()
    if (method !== 'POST') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ valid: true, errors: [] }),
    })
  })

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

  await page.route('**/api/strategies/*', (route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()
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
// Helpers
// ---------------------------------------------------------------------------

/**
 * Returns the nth inherit checkbox in the active tabpanel.
 * OverrideFieldEditor renders 5 InheritToggle checkboxes in this order:
 *   0 = system_prompt, 1 = user_prompt_template, 2 = model_id,
 *   3 = verification, 4 = max_turns
 */
function inheritCheckbox(panel: import('@playwright/test').Locator, index: number) {
  return panel.locator('input[type="checkbox"]').nth(index)
}

const INHERIT_INDEX = {
  system_prompt: 0,
  user_prompt_template: 1,
  model_id: 2,
  verification: 3,
  max_turns: 4,
} as const

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('per_vuln_class override editing', () => {
  // -------------------------------------------------------------------------
  // Test 1: Tabs render and default to sqli
  // -------------------------------------------------------------------------

  test('tabs render and sqli is active by default with no amber dots', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-pervuln-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    // Overrides section is visible immediately (shape is per_vuln_class from parent)
    await expect(page.getByRole('heading', { name: /Overrides/ })).toBeVisible()

    // Key tabs are present
    await expect(page.getByRole('tab', { name: 'sqli' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'xss' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'ssrf' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'rce' })).toBeVisible()

    // sqli is selected by default
    await expect(page.getByRole('tab', { name: 'sqli' })).toHaveAttribute('aria-selected', 'true')

    // No amber dots in any tab
    await expect(page.locator('[role="tab"] span.bg-amber-500')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // Test 2: Switching tabs changes the active panel
  // -------------------------------------------------------------------------

  test('clicking xss tab makes it active and shows no amber dot', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-pervuln-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()
    await expect(page.getByRole('heading', { name: /Overrides/ })).toBeVisible()

    await page.getByRole('tab', { name: 'xss' }).click()

    await expect(page.getByRole('tab', { name: 'xss' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('tab', { name: 'sqli' })).toHaveAttribute('aria-selected', 'false')

    // No amber dots after a plain tab switch
    await expect(page.locator('[role="tab"] span.bg-amber-500')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // Test 3: Unchecking Inherit on model_id reveals field and shows amber dot
  // -------------------------------------------------------------------------

  test('unchecking Inherit on model_id for sqli reveals the input and adds amber dot', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-pervuln-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()
    await expect(page.getByRole('heading', { name: /Overrides/ })).toBeVisible()

    // sqli is the active tab by default
    const sqliTab = page.getByRole('tab', { name: 'sqli' })
    await expect(sqliTab).toHaveAttribute('aria-selected', 'true')

    const panel = page.getByRole('tabpanel')
    const modelIdInheritCb = inheritCheckbox(panel, INHERIT_INDEX.model_id)

    // Initially inheriting: checkbox checked, no text input
    await expect(modelIdInheritCb).toBeChecked()
    await expect(panel.locator('input[type="text"]')).not.toBeVisible()

    // Uncheck to stop inheriting
    await modelIdInheritCb.uncheck()

    // Text input now visible (the model_id field)
    await expect(panel.locator('input[type="text"]')).toBeVisible()

    // Amber dot appears on the sqli tab
    await expect(sqliTab.locator('span.bg-amber-500')).toHaveCount(1)
  })

  // -------------------------------------------------------------------------
  // Test 4: Type a model_id, switch tabs, switch back: state preserved
  // -------------------------------------------------------------------------

  test('model_id value is preserved when switching away from sqli and back', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-pervuln-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()
    await expect(page.getByRole('heading', { name: /Overrides/ })).toBeVisible()

    const sqliTab = page.getByRole('tab', { name: 'sqli' })
    const panel = page.getByRole('tabpanel')

    // Uncheck model_id inherit on sqli
    await inheritCheckbox(panel, INHERIT_INDEX.model_id).uncheck()

    // Fill in model id
    await panel.locator('input[type="text"]').fill('claude-3-5-sonnet-20241022')
    await expect(panel.locator('input[type="text"]')).toHaveValue('claude-3-5-sonnet-20241022')

    // Switch to xss tab
    await page.getByRole('tab', { name: 'xss' }).click()
    await expect(page.getByRole('tab', { name: 'xss' })).toHaveAttribute('aria-selected', 'true')

    // Switch back to sqli
    await sqliTab.click()
    await expect(sqliTab).toHaveAttribute('aria-selected', 'true')

    // Value still present
    await expect(panel.locator('input[type="text"]')).toHaveValue('claude-3-5-sonnet-20241022')

    // Amber dot still on sqli
    await expect(sqliTab.locator('span.bg-amber-500')).toHaveCount(1)
  })

  // -------------------------------------------------------------------------
  // Test 5: Override a different field on a different tab
  // -------------------------------------------------------------------------

  test('overriding max_turns on xss shows amber dot on xss; sqli dot unaffected', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-pervuln-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()
    await expect(page.getByRole('heading', { name: /Overrides/ })).toBeVisible()

    const panel = page.getByRole('tabpanel')
    const sqliTab = page.getByRole('tab', { name: 'sqli' })

    // Override model_id on sqli
    await inheritCheckbox(panel, INHERIT_INDEX.model_id).uncheck()
    await panel.locator('input[type="text"]').fill('claude-3-5-sonnet-20241022')

    // Switch to xss
    await page.getByRole('tab', { name: 'xss' }).click()
    const xssTab = page.getByRole('tab', { name: 'xss' })
    await expect(xssTab).toHaveAttribute('aria-selected', 'true')

    // Override max_turns on xss (index 4)
    await inheritCheckbox(panel, INHERIT_INDEX.max_turns).uncheck()

    // xss now has amber dot
    await expect(xssTab.locator('span.bg-amber-500')).toHaveCount(1)

    // Set a valid max_turns value (within 1-200 to avoid HTML5 form-validation block)
    await panel.locator('input[type="number"]').fill('42')
    await expect(panel.locator('input[type="number"]')).toHaveValue('42')

    // Switch back to sqli — sqli amber dot still present
    await sqliTab.click()
    await expect(sqliTab.locator('span.bg-amber-500')).toHaveCount(1)

    // xss amber dot still present
    await expect(xssTab.locator('span.bg-amber-500')).toHaveCount(1)
  })

  // -------------------------------------------------------------------------
  // Test 6: Re-checking Inherit hides the field again.
  //
  // Production behavior: VulnClassOverrides.setOverride never removes an entry
  // once added to the rules array. When InheritToggle re-checks, clear(field)
  // sets the field to null in the override object, but the entry itself persists
  // as { key: 'xss', override: { max_turns: null } }. Since
  // rules.some(r => r.key === 'xss') is still true, the amber dot STAYS.
  // -------------------------------------------------------------------------

  test('re-checking Inherit on max_turns hides the input but amber dot persists on xss', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-pervuln-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()
    await expect(page.getByRole('heading', { name: /Overrides/ })).toBeVisible()

    // Go to xss and uncheck max_turns inherit
    await page.getByRole('tab', { name: 'xss' }).click()
    const xssTab = page.getByRole('tab', { name: 'xss' })
    await expect(xssTab).toHaveAttribute('aria-selected', 'true')

    const panel = page.getByRole('tabpanel')
    const maxTurnsCb = inheritCheckbox(panel, INHERIT_INDEX.max_turns)

    await maxTurnsCb.uncheck()

    // Number input is visible; amber dot present
    await expect(panel.locator('input[type="number"]')).toBeVisible()
    await expect(xssTab.locator('span.bg-amber-500')).toHaveCount(1)

    // Re-check Inherit on max_turns
    await maxTurnsCb.check()

    // Number input disappears
    await expect(panel.locator('input[type="number"]')).not.toBeVisible()

    // Amber dot persists because the rule entry remains in the rules array
    await expect(xssTab.locator('span.bg-amber-500')).toHaveCount(1)
  })

  // -------------------------------------------------------------------------
  // Test 7: POST body shape — only changed fields per vuln class
  // -------------------------------------------------------------------------

  test('save posts correct overrides: sqli with model_id only, xss with max_turns only', async ({ page }) => {
    let capturedBody: Record<string, unknown> | null = null

    // Per-test LIFO override to capture the POST body
    await page.route('**/api/strategies', (route) => {
      const method = route.request().method()
      if (method !== 'POST') return route.fallback()
      capturedBody = route.request().postDataJSON()
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(CREATED_FORK),
      })
    })

    await page.goto('/strategies/strat-builtin-pervuln-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()
    await expect(page.getByRole('heading', { name: /Overrides/ })).toBeVisible()

    // Set name
    await page.getByTestId('name-input').fill('VC Override Test')

    const panel = page.getByRole('tabpanel')

    // sqli: override model_id (sqli is active by default)
    await expect(page.getByRole('tab', { name: 'sqli' })).toHaveAttribute('aria-selected', 'true')
    await inheritCheckbox(panel, INHERIT_INDEX.model_id).uncheck()
    await panel.locator('input[type="text"]').fill('claude-3-5-sonnet-20241022')

    // xss: override max_turns
    await page.getByRole('tab', { name: 'xss' }).click()
    await expect(page.getByRole('tab', { name: 'xss' })).toHaveAttribute('aria-selected', 'true')
    await inheritCheckbox(panel, INHERIT_INDEX.max_turns).uncheck()
    await panel.locator('input[type="number"]').fill('42')

    // Save
    await page.getByTestId('save-btn').click()
    await expect(page).toHaveURL(/\/strategies\/strat-new-pervuln-fork-001/)

    expect(capturedBody).not.toBeNull()

    // Top-level shape
    expect(capturedBody!['orchestration_shape']).toBe('per_vuln_class')

    const overrides = capturedBody!['overrides'] as Array<{ key: string; override: Record<string, unknown> }>
    expect(Array.isArray(overrides)).toBe(true)

    // Both sqli and xss entries must be present
    const sqliEntry = overrides.find((r) => r.key === 'sqli')
    const xssEntry = overrides.find((r) => r.key === 'xss')
    expect(sqliEntry).toBeDefined()
    expect(xssEntry).toBeDefined()

    // sqli: model_id set; all other override fields null/undefined
    expect(sqliEntry!.override['model_id']).toBe('claude-3-5-sonnet-20241022')
    expect(sqliEntry!.override['max_turns'] == null).toBe(true)
    expect(sqliEntry!.override['system_prompt'] == null).toBe(true)
    expect(sqliEntry!.override['user_prompt_template'] == null).toBe(true)
    expect(sqliEntry!.override['verification'] == null).toBe(true)

    // xss: max_turns set; all other override fields null/undefined
    expect(xssEntry!.override['max_turns']).toBe(42)
    expect(xssEntry!.override['model_id'] == null).toBe(true)
    expect(xssEntry!.override['system_prompt'] == null).toBe(true)
    expect(xssEntry!.override['user_prompt_template'] == null).toBe(true)
    expect(xssEntry!.override['verification'] == null).toBe(true)

    // No other vuln classes appear in overrides
    const otherKeys = overrides.map((r) => r.key).filter((k) => k !== 'sqli' && k !== 'xss')
    expect(otherKeys).toHaveLength(0)
  })
})
