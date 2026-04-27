/**
 * Shared mock routes helper for strategy-related e2e tests.
 *
 * Exports the `mockStrategiesRoutes` function and the fixture constants used
 * by strategy editor tests. Call this AFTER mockApi() — Playwright routes are
 * LIFO so these handlers win over the legacy string-array handler.
 *
 * Other specs (strategy-fork-edit, iter-15, iter-30, iter-31, iter-32) keep
 * their own inline copies; this file is the canonical source for new specs.
 */

import type { Page } from '@playwright/test'

// ---------------------------------------------------------------------------
// Fixture constants
// ---------------------------------------------------------------------------

export const STRATEGY_BUILTIN_SINGLE: import('../../src/api/strategies').StrategySummary = {
  id: 'strat-builtin-single-001',
  name: 'Zero Shot Single Agent',
  orchestration_shape: 'single_agent',
  is_builtin: true,
  parent_strategy_id: null,
}

const ALL_STRATEGY_SUMMARIES = [STRATEGY_BUILTIN_SINGLE]

// Full UserStrategy for the builtin single_agent strategy.
// max_subagent_depth/invocations/batch_size must be >= 1 to satisfy the
// min={1} attribute on the number inputs in BundleDefaultForm.
export const FULL_BUILTIN_SINGLE: import('../../src/api/strategies').UserStrategy = {
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
export const CREATED_FORK: import('../../src/api/strategies').UserStrategy = {
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
const STRATEGY_FULL_BY_ID: Record<string, import('../../src/api/strategies').UserStrategy> = {
  'strat-builtin-single-001': FULL_BUILTIN_SINGLE,
  'strat-new-fork-001': CREATED_FORK,
}

// ---------------------------------------------------------------------------
// Route registration
// ---------------------------------------------------------------------------

/**
 * Register strategy API mock routes on the given page.
 *
 * Covers:
 *   GET  /api/strategies              → StrategySummary[]
 *   POST /api/strategies/__new__/validate → { valid: true, errors: [] }
 *   POST /api/strategies              → CREATED_FORK (echo name/shape from body)
 *   GET  /api/strategies/<id>         → UserStrategy by id
 *
 * Must be called after mockApi() so these LIFO handlers take priority.
 */
export async function mockStrategiesRoutes(page: Page) {
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

  // POST /api/strategies → echo body's name/shape into a full UserStrategy
  await page.route('**/api/strategies', (route) => {
    const method = route.request().method()
    if (method !== 'POST') return route.fallback()
    const body = route.request().postDataJSON() as Partial<import('../../src/api/strategies').UserStrategy>
    const created: import('../../src/api/strategies').UserStrategy = {
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

  // GET /api/strategies/<id> → UserStrategy (single resource)
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
