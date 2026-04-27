/**
 * e2e spec: strategy-tool-extensions
 *
 * Covers the user story:
 *   As a security researcher authoring a strategy, I want checkboxes for the
 *   available Tool Extensions (Tree-sitter, LSP, DevDocs) — with disabled,
 *   reduced-opacity styling on unavailable extensions — so I can opt into
 *   compile-time helpers without trying to toggle disabled options, and so my
 *   POST body's `default.tool_extensions` reflects exactly what I selected.
 *
 * These checkboxes live inside StrategyEditor / BundleDefaultForm, rendered at
 * /strategies/<id>/fork. They are NOT the ExperimentNew checkboxes (which were
 * removed in a prior refactor). tool-extensions.spec.ts covers the old removed
 * location and is broken; this spec covers the current production mount-point.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const STRATEGY_BUILTIN_SINGLE = {
  id: 'strat-builtin-single-001',
  name: 'Zero Shot Single Agent',
  orchestration_shape: 'single_agent',
  is_builtin: true,
  parent_strategy_id: null,
}

const ALL_STRATEGY_SUMMARIES = [STRATEGY_BUILTIN_SINGLE]

// max_subagent_* must be >= 1 to satisfy HTML5 min={1} validation on number inputs.
const FULL_BUILTIN_SINGLE = {
  ...STRATEGY_BUILTIN_SINGLE,
  created_at: '2025-01-01T00:00:00Z',
  default: {
    system_prompt: 'You are a security expert.',
    user_prompt_template: 'Review the following code for vulnerabilities:\n\n{repo_summary}\n\n{finding_output_format}',
    profile_modifier: '',
    model_id: 'gpt-4o',
    tools: ['read_file'],
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

const CREATED_FORK = {
  id: 'strat-new-fork-001',
  name: 'Fork of Zero Shot Single Agent',
  parent_strategy_id: 'strat-builtin-single-001',
  orchestration_shape: 'single_agent',
  is_builtin: false,
  created_at: '2026-01-01T00:00:00Z',
  default: { ...FULL_BUILTIN_SINGLE.default },
  overrides: [],
}

const STRATEGY_FULL_BY_ID: Record<string, typeof FULL_BUILTIN_SINGLE> = {
  'strat-builtin-single-001': FULL_BUILTIN_SINGLE,
  'strat-new-fork-001': CREATED_FORK,
}

// ---------------------------------------------------------------------------
// Helper: wire strategy mock routes (copied from strategy-fork-edit.spec.ts).
// Routes are LIFO in Playwright — these take priority over the mockApi handler.
// ---------------------------------------------------------------------------

async function mockStrategiesRoutes(page: import('@playwright/test').Page) {
  await page.route('**/api/strategies', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(ALL_STRATEGY_SUMMARIES),
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
    const body = route.request().postDataJSON() as Partial<typeof FULL_BUILTIN_SINGLE>
    return route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        ...CREATED_FORK,
        name: body.name ?? CREATED_FORK.name,
        orchestration_shape: body.orchestration_shape ?? CREATED_FORK.orchestration_shape,
      }),
    })
  })

  await page.route('**/api/strategies/*', (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/validate')) return route.fallback()
    if (route.request().method() !== 'GET') return route.fallback()
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

test.describe('StrategyEditor Tool Extensions', () => {
  test('all three extension labels are visible in the Tool Extensions section', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    await expect(page.locator('label').filter({ hasText: 'Tree-sitter' })).toBeVisible()
    await expect(page.locator('label').filter({ hasText: 'LSP' })).toBeVisible()
    await expect(page.locator('label').filter({ hasText: 'DevDocs' })).toBeVisible()
  })

  test('available extensions (Tree-sitter, LSP) have enabled checkboxes', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const treeSitterCheckbox = page.locator('label').filter({ hasText: 'Tree-sitter' }).locator('input[type="checkbox"]')
    const lspCheckbox = page.locator('label').filter({ hasText: 'LSP' }).locator('input[type="checkbox"]')

    await expect(treeSitterCheckbox).not.toBeDisabled()
    await expect(lspCheckbox).not.toBeDisabled()
  })

  test('unavailable extension (DevDocs) has a disabled checkbox', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const devdocsCheckbox = page.locator('label').filter({ hasText: 'DevDocs' }).locator('input[type="checkbox"]')
    await expect(devdocsCheckbox).toBeDisabled()
  })

  test('DevDocs label has opacity-50 class; available extension labels do not', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const devdocsLabel = page.locator('label').filter({ hasText: 'DevDocs' })
    await expect(devdocsLabel).toHaveClass(/opacity-50/)

    const treeSitterLabel = page.locator('label').filter({ hasText: 'Tree-sitter' })
    await expect(treeSitterLabel).not.toHaveClass(/opacity-50/)

    const lspLabel = page.locator('label').filter({ hasText: 'LSP' })
    await expect(lspLabel).not.toHaveClass(/opacity-50/)
  })

  test('initial state: all three checkboxes are unchecked when parent has tool_extensions: []', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const treeSitterCheckbox = page.locator('label').filter({ hasText: 'Tree-sitter' }).locator('input[type="checkbox"]')
    const lspCheckbox = page.locator('label').filter({ hasText: 'LSP' }).locator('input[type="checkbox"]')
    const devdocsCheckbox = page.locator('label').filter({ hasText: 'DevDocs' }).locator('input[type="checkbox"]')

    await expect(treeSitterCheckbox).not.toBeChecked()
    await expect(lspCheckbox).not.toBeChecked()
    await expect(devdocsCheckbox).not.toBeChecked()
  })

  test('clicking Tree-sitter checkbox checks it while others remain unchecked', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const treeSitterCheckbox = page.locator('label').filter({ hasText: 'Tree-sitter' }).locator('input[type="checkbox"]')
    const lspCheckbox = page.locator('label').filter({ hasText: 'LSP' }).locator('input[type="checkbox"]')
    const devdocsCheckbox = page.locator('label').filter({ hasText: 'DevDocs' }).locator('input[type="checkbox"]')

    await treeSitterCheckbox.click()

    await expect(treeSitterCheckbox).toBeChecked()
    await expect(lspCheckbox).not.toBeChecked()
    await expect(devdocsCheckbox).not.toBeChecked()
  })

  test('force-clicking disabled DevDocs checkbox does not check it', async ({ page }) => {
    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const devdocsCheckbox = page.locator('label').filter({ hasText: 'DevDocs' }).locator('input[type="checkbox"]')

    // force: true bypasses Playwright's actionability check, but the HTML5
    // disabled attribute still prevents a state change in the browser.
    await devdocsCheckbox.click({ force: true })

    await expect(devdocsCheckbox).not.toBeChecked()
  })

  test('POST body default.tool_extensions reflects exactly the selected extensions', async ({ page }) => {
    let capturedBody: Record<string, unknown> | null = null

    // Per-test override (LIFO = wins over the shared handler) to capture the POST.
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
    await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

    // Toggle Tree-sitter and LSP on; leave DevDocs alone (it's disabled).
    await page.locator('label').filter({ hasText: 'Tree-sitter' }).locator('input[type="checkbox"]').click()
    await page.locator('label').filter({ hasText: 'LSP' }).locator('input[type="checkbox"]').click()

    // Wait for the create POST to complete before asserting on capturedBody.
    // We exclude the validate sub-resource (POST /api/strategies/__new__/validate)
    // which also returns POST and fires before the actual create call.
    const [response] = await Promise.all([
      page.waitForResponse(
        (resp) =>
          resp.url().includes('/api/strategies') &&
          !resp.url().includes('/validate') &&
          resp.request().method() === 'POST',
      ),
      page.getByTestId('save-btn').click(),
    ])
    expect(response.status()).toBe(201)

    expect(capturedBody).not.toBeNull()

    const defaultBundle = capturedBody!['default'] as Record<string, unknown>
    expect(defaultBundle).toBeTruthy()
    const toolExtensions = defaultBundle['tool_extensions'] as string[]
    expect(Array.isArray(toolExtensions)).toBe(true)
    expect(toolExtensions).toHaveLength(2)
    expect(toolExtensions).toContain('tree_sitter')
    expect(toolExtensions).toContain('lsp')
    expect(toolExtensions).not.toContain('devdocs')
  })

  test("parent's pre-existing tool_extensions are pre-checked on fork load", async ({ page }) => {
    // Override GET for the parent strategy to have tree_sitter pre-selected.
    const parentWithTreeSitter = {
      ...FULL_BUILTIN_SINGLE,
      default: {
        ...FULL_BUILTIN_SINGLE.default,
        tool_extensions: ['tree_sitter'],
      },
    }

    // Register the override AFTER mockStrategiesRoutes (LIFO — wins).
    await page.route('**/api/strategies/strat-builtin-single-001', (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(parentWithTreeSitter),
      })
    })

    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()

    const treeSitterCheckbox = page.locator('label').filter({ hasText: 'Tree-sitter' }).locator('input[type="checkbox"]')
    const lspCheckbox = page.locator('label').filter({ hasText: 'LSP' }).locator('input[type="checkbox"]')

    await expect(treeSitterCheckbox).toBeChecked()
    await expect(lspCheckbox).not.toBeChecked()
  })
})
