/**
 * e2e spec: strategy-delete
 *
 * Covers the user story:
 *   As a strategy author, when I try to delete a strategy that's still
 *   referenced by an experiment, I want to see a clear error message in the
 *   confirmation dialog instead of having the delete silently fail or appear
 *   to succeed.
 *
 * Mocking strategy:
 *   mockApi() + inline mockStrategiesRoutes registers GET /api/strategies/*
 *   returning a non-builtin user strategy. Per-test overrides are layered on
 *   top for DELETE responses — Playwright routes are LIFO so per-test handlers
 *   win over the beforeEach handler.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const USER_STRATEGY_ID = 'strat-user-single-delete-001'

const FULL_USER_STRATEGY: import('../src/api/strategies').UserStrategy = {
  id: USER_STRATEGY_ID,
  name: 'My Deletable Strategy',
  parent_strategy_id: 'strat-builtin-single-001',
  orchestration_shape: 'single_agent',
  is_builtin: false,
  created_at: '2025-10-01T00:00:00Z',
  default: {
    system_prompt: 'You are a security expert.',
    user_prompt_template: 'Review the following code:\n\n{{code}}',
    profile_modifier: '',
    model_id: 'gpt-4o',
    tools: ['read_file'],
    verification: 'none',
    max_turns: 20,
    tool_extensions: [],
    subagents: [],
    max_subagent_depth: 1,
    max_subagent_invocations: 1,
    max_subagent_batch_size: 1,
    dispatch_fallback: 'none',
    output_type_name: 'finding_list',
  },
  overrides: [],
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function mockStrategiesRoutes(page: import('@playwright/test').Page) {
  // GET /api/strategies/* → FULL_USER_STRATEGY for our test ID
  await page.route('**/api/strategies/*', (route) => {
    const method = route.request().method()
    if (method !== 'GET') return route.fallback()
    const url = new URL(route.request().url())
    const id = decodeURIComponent(url.pathname.split('/').pop() ?? '')
    if (id === USER_STRATEGY_ID) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(FULL_USER_STRATEGY),
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
// Test 1: Successful delete navigates to /strategies
// ---------------------------------------------------------------------------

test('successful delete navigates to /strategies', async ({ page }) => {
  // Layer DELETE 204 on top
  await page.route(`**/api/strategies/${USER_STRATEGY_ID}*`, (route) => {
    if (route.request().method() !== 'DELETE') return route.fallback()
    return route.fulfill({ status: 204, body: '' })
  })

  await page.goto(`/strategies/${USER_STRATEGY_ID}`)
  await expect(page.getByRole('heading', { name: 'My Deletable Strategy' })).toBeVisible()

  await page.getByTestId('delete-btn').click()
  await expect(page.getByRole('dialog')).toBeVisible()

  await page.getByTestId('confirm-delete-btn').click()

  await expect(page).toHaveURL('/strategies')
})

// ---------------------------------------------------------------------------
// Test 2: 409 shows error in dialog, dialog stays open, button re-enables
// ---------------------------------------------------------------------------

test('409 from backend shows error in dialog, dialog stays open, button re-enables', async ({ page }) => {
  const errorMsg = "Strategy 'user.foo.abc123' is referenced by one or more runs and cannot be deleted."

  // Layer DELETE 409 on top
  await page.route(`**/api/strategies/${USER_STRATEGY_ID}*`, (route) => {
    if (route.request().method() !== 'DELETE') return route.fallback()
    return route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ detail: errorMsg }),
    })
  })

  await page.goto(`/strategies/${USER_STRATEGY_ID}`)
  await expect(page.getByRole('heading', { name: 'My Deletable Strategy' })).toBeVisible()

  await page.getByTestId('delete-btn').click()
  await expect(page.getByRole('dialog')).toBeVisible()

  await page.getByTestId('confirm-delete-btn').click()

  // Error appears in the dialog
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.getByRole('alert')).toContainText(errorMsg)

  // Dialog stays open
  await expect(page.getByTestId('confirm-delete-btn')).toBeVisible()

  // Button is re-enabled and shows original label
  const btn = page.getByTestId('confirm-delete-btn')
  await expect(btn).toHaveText('Delete')
  await expect(btn).not.toBeDisabled()
})

// ---------------------------------------------------------------------------
// Test 3: Generic 500 fallback message — dialog shows SOME feedback
// ---------------------------------------------------------------------------

test('500 from backend shows an error alert in the dialog and dialog stays open', async ({ page }) => {
  // Layer DELETE 500 with no body on top
  await page.route(`**/api/strategies/${USER_STRATEGY_ID}*`, (route) => {
    if (route.request().method() !== 'DELETE') return route.fallback()
    return route.fulfill({ status: 500, body: '' })
  })

  await page.goto(`/strategies/${USER_STRATEGY_ID}`)
  await expect(page.getByRole('heading', { name: 'My Deletable Strategy' })).toBeVisible()

  await page.getByTestId('delete-btn').click()
  await expect(page.getByRole('dialog')).toBeVisible()

  await page.getByTestId('confirm-delete-btn').click()

  // Some error feedback is visible (text is irrelevant — just check role=alert exists)
  await expect(page.getByRole('alert')).toBeVisible()

  // Dialog stays open
  await expect(page.getByTestId('confirm-delete-btn')).toBeVisible()
})
