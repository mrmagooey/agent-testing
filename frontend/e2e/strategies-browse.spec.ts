/**
 * e2e spec: strategies-browse
 *
 * Covers the user story:
 *   As a security researcher, I want to browse the catalog of review strategies,
 *   filter to user-authored vs builtin (and by orchestration shape), and drill
 *   into a specific strategy to inspect its default bundle and per-key overrides.
 *
 * Mocking strategy:
 *   mockApi() registers a legacy GET /api/strategies handler that returns a
 *   string array. We override it with page.route() handlers registered AFTER
 *   mockApi() — Playwright routes are LIFO so our handlers win.
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

const STRATEGY_USER_PER_FILE: import('../src/api/strategies').StrategySummary = {
  id: 'strat-user-per-file-003',
  name: 'Custom Per-File Strategy',
  orchestration_shape: 'per_file',
  is_builtin: false,
  parent_strategy_id: null,
}

const ALL_STRATEGY_SUMMARIES = [
  STRATEGY_BUILTIN_SINGLE,
  STRATEGY_USER_PER_VULN,
  STRATEGY_USER_PER_FILE,
]

// Full UserStrategy for the builtin single_agent strategy
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
    max_subagent_depth: 0,
    max_subagent_invocations: 0,
    max_subagent_batch_size: 0,
    dispatch_fallback: 'none',
    output_type_name: 'finding_list',
  },
  overrides: [],
}

// Full UserStrategy for the user-authored per_vuln_class strategy with 2 overrides
const FULL_USER_PER_VULN: import('../src/api/strategies').UserStrategy = {
  id: 'strat-user-per-vuln-002',
  name: 'Custom Per-VulnClass Strategy',
  parent_strategy_id: 'strat-builtin-single-001',
  orchestration_shape: 'per_vuln_class',
  is_builtin: false,
  created_at: '2025-06-15T10:00:00Z',
  default: {
    system_prompt: 'You are a security expert specializing in vulnerability detection.',
    user_prompt_template: 'Analyze this code for {{vuln_class}} vulnerabilities:\n\n{{code}}',
    profile_modifier: 'strict',
    model_id: 'gpt-4o',
    tools: ['read_file'],
    verification: 'llm',
    max_turns: 30,
    tool_extensions: ['tree_sitter'],
    subagents: [],
    max_subagent_depth: 0,
    max_subagent_invocations: 0,
    max_subagent_batch_size: 0,
    dispatch_fallback: 'reprompt',
    output_type_name: 'finding_list',
  },
  overrides: [
    {
      key: 'sqli',
      override: {
        system_prompt: 'You are an expert in SQL injection vulnerabilities.',
        model_id: 'claude-3-5-sonnet-20241022',
        max_turns: 50,
      },
    },
    {
      key: 'xss',
      override: {
        user_prompt_template: 'Focus exclusively on XSS vectors in:\n\n{{code}}',
        tools: ['read_file', 'search_code'],
      },
    },
  ],
}

// Full UserStrategy for the user-authored per_file strategy
const FULL_USER_PER_FILE: import('../src/api/strategies').UserStrategy = {
  id: 'strat-user-per-file-003',
  name: 'Custom Per-File Strategy',
  parent_strategy_id: null,
  orchestration_shape: 'per_file',
  is_builtin: false,
  created_at: '2025-09-20T08:00:00Z',
  default: {
    system_prompt: 'Review each file for vulnerabilities.',
    user_prompt_template: 'Analyze {{file_path}}:\n\n{{code}}',
    profile_modifier: '',
    model_id: 'gpt-4o',
    tools: ['read_file'],
    verification: 'none',
    max_turns: 15,
    tool_extensions: [],
    subagents: [],
    max_subagent_depth: 0,
    max_subagent_invocations: 0,
    max_subagent_batch_size: 0,
    dispatch_fallback: 'programmatic',
    output_type_name: 'finding_list',
  },
  overrides: [
    {
      key: '*.py',
      override: {
        model_id: 'claude-3-5-sonnet-20241022',
      },
    },
  ],
}

// Map from strategy ID to its full UserStrategy object
const STRATEGY_FULL_BY_ID: Record<string, import('../src/api/strategies').UserStrategy> = {
  'strat-builtin-single-001': FULL_BUILTIN_SINGLE,
  'strat-user-per-vuln-002': FULL_USER_PER_VULN,
  'strat-user-per-file-003': FULL_USER_PER_FILE,
}

// ---------------------------------------------------------------------------
// Helper: wire up strategy-specific mock routes AFTER mockApi()
// Routes are LIFO in Playwright — these take priority over the legacy handler.
// ---------------------------------------------------------------------------

async function mockStrategiesRoutes(page: import('@playwright/test').Page) {
  // GET /api/strategies → StrategySummary[] (overrides the legacy string-array handler)
  await page.route('**/api/strategies', (route) => {
    const method = route.request().method()
    if (method !== 'GET') return route.continue()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(ALL_STRATEGY_SUMMARIES),
    })
  })

  // GET /api/strategies/<id> → UserStrategy
  await page.route('**/api/strategies/*', (route) => {
    const method = route.request().method()
    if (method === 'DELETE') {
      return route.fulfill({ status: 204, body: '' })
    }
    if (method !== 'GET') return route.continue()
    const url = new URL(route.request().url())
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
// Test 1: List page heading and "New Strategy" button
// ---------------------------------------------------------------------------

test('list page renders the "Strategies" heading and "New Strategy" button', async ({ page }) => {
  await page.goto('/strategies')
  await expect(page.getByRole('heading', { name: 'Strategies' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'New Strategy' })).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 2: Table rows are rendered for all fixture strategies
// ---------------------------------------------------------------------------

test('table renders rows for all mocked strategies by name', async ({ page }) => {
  await page.goto('/strategies')

  // Wait for the table to contain data
  await expect(page.getByTestId('strategy-row').first()).toBeVisible()

  await expect(page.getByText('Zero Shot Single Agent')).toBeVisible()
  await expect(page.getByText('Custom Per-VulnClass Strategy')).toBeVisible()
  await expect(page.getByText('Custom Per-File Strategy')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 3: builtin / user / all filter chips narrow the table
// ---------------------------------------------------------------------------

test('builtin filter chip narrows table to only builtin rows', async ({ page }) => {
  await page.goto('/strategies')
  await expect(page.getByTestId('strategy-row').first()).toBeVisible()

  await page.getByTestId('filter-builtin').click()

  const rows = page.getByTestId('strategy-row')
  await expect(rows).toHaveCount(1)
  await expect(page.getByText('Zero Shot Single Agent')).toBeVisible()
  await expect(page.getByText('Custom Per-VulnClass Strategy')).not.toBeVisible()
  await expect(page.getByText('Custom Per-File Strategy')).not.toBeVisible()
})

test('user filter chip narrows table to only user-authored rows', async ({ page }) => {
  await page.goto('/strategies')
  await expect(page.getByTestId('strategy-row').first()).toBeVisible()

  await page.getByTestId('filter-user').click()

  const rows = page.getByTestId('strategy-row')
  await expect(rows).toHaveCount(2)
  await expect(page.getByText('Custom Per-VulnClass Strategy')).toBeVisible()
  await expect(page.getByText('Custom Per-File Strategy')).toBeVisible()
  await expect(page.getByText('Zero Shot Single Agent')).not.toBeVisible()
})

test('all filter chip restores all rows after filtering', async ({ page }) => {
  await page.goto('/strategies')
  await expect(page.getByTestId('strategy-row').first()).toBeVisible()

  // Narrow first
  await page.getByTestId('filter-builtin').click()
  await expect(page.getByTestId('strategy-row')).toHaveCount(1)

  // Restore
  await page.getByTestId('filter-all').click()
  await expect(page.getByTestId('strategy-row')).toHaveCount(3)
})

// ---------------------------------------------------------------------------
// Test 4: Shape filter narrows the table
// ---------------------------------------------------------------------------

test('shape filter select narrows table to matching orchestration shape', async ({ page }) => {
  await page.goto('/strategies')
  await expect(page.getByTestId('strategy-row').first()).toBeVisible()

  await page.getByTestId('shape-filter').selectOption('per_vuln_class')

  const rows = page.getByTestId('strategy-row')
  await expect(rows).toHaveCount(1)
  await expect(page.getByText('Custom Per-VulnClass Strategy')).toBeVisible()
  await expect(page.getByText('Zero Shot Single Agent')).not.toBeVisible()
  await expect(page.getByText('Custom Per-File Strategy')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 5: Clicking "View" navigates to /strategies/<id> and viewer renders
// ---------------------------------------------------------------------------

test('clicking View on a row navigates to the strategy viewer with correct details', async ({ page }) => {
  await page.goto('/strategies')
  await expect(page.getByTestId('strategy-row').first()).toBeVisible()

  // Click the View button on the per_vuln_class strategy row
  const perVulnRow = page.getByTestId('strategy-row').filter({ hasText: 'Custom Per-VulnClass Strategy' })
  await perVulnRow.getByTestId('view-btn').click()

  // Should have navigated to the strategy detail URL
  await expect(page).toHaveURL(/\/strategies\/strat-user-per-vuln-002/)

  // Strategy name visible in the heading
  await expect(page.getByRole('heading', { name: 'Custom Per-VulnClass Strategy' })).toBeVisible()

  // Orchestration shape chip visible
  await expect(page.getByText('per_vuln_class').first()).toBeVisible()

  // Default Bundle section visible
  await expect(page.getByRole('heading', { name: 'Default Bundle' })).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 6: per_vuln_class viewer shows Overrides tabs for each key
// ---------------------------------------------------------------------------

test('per_vuln_class strategy viewer renders override tabs and switching tabs shows override fields', async ({ page }) => {
  await page.goto('/strategies/strat-user-per-vuln-002')

  // Wait for the strategy to load
  await expect(page.getByRole('heading', { name: 'Custom Per-VulnClass Strategy' })).toBeVisible()

  // Both override keys should be present as tabs
  const sqliTab = page.getByRole('tab', { name: 'sqli' })
  const xssTab = page.getByRole('tab', { name: 'xss' })

  await expect(sqliTab).toBeVisible()
  await expect(xssTab).toBeVisible()

  // The sqli tab is the default — its overridden fields should be visible
  await expect(page.getByText('claude-3-5-sonnet-20241022').first()).toBeVisible()

  // Switch to the xss tab
  await xssTab.click()

  // The xss override has a user_prompt_template field
  await expect(page.getByText('Focus exclusively on XSS vectors')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 7: Delete button visibility — visible for user strategy, absent for builtin
// ---------------------------------------------------------------------------

test('Delete button is visible on user-authored strategy viewer', async ({ page }) => {
  await page.goto('/strategies/strat-user-per-vuln-002')
  await expect(page.getByRole('heading', { name: 'Custom Per-VulnClass Strategy' })).toBeVisible()

  await expect(page.getByTestId('delete-btn')).toBeVisible()
})

test('Delete button opens confirmation dialog containing the strategy name', async ({ page }) => {
  await page.goto('/strategies/strat-user-per-vuln-002')
  await expect(page.getByRole('heading', { name: 'Custom Per-VulnClass Strategy' })).toBeVisible()

  await page.getByTestId('delete-btn').click()

  // Confirmation dialog must include the strategy name
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('Custom Per-VulnClass Strategy')
})

test('Delete button is NOT visible on a builtin strategy viewer', async ({ page }) => {
  await page.goto('/strategies/strat-builtin-single-001')
  await expect(page.getByRole('heading', { name: 'Zero Shot Single Agent' })).toBeVisible()

  await expect(page.getByTestId('delete-btn')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 8: Breadcrumb "Strategies" link navigates back to /strategies
// ---------------------------------------------------------------------------

test('breadcrumb Strategies link in the viewer navigates back to /strategies', async ({ page }) => {
  await page.goto('/strategies/strat-builtin-single-001')
  await expect(page.getByRole('heading', { name: 'Zero Shot Single Agent' })).toBeVisible()

  // The breadcrumb is rendered as a button with text "Strategies"
  await page.getByRole('button', { name: 'Strategies' }).click()

  await expect(page).toHaveURL('/strategies')
  await expect(page.getByRole('heading', { name: 'Strategies' })).toBeVisible()
})
