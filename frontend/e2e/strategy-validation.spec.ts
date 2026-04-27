/**
 * e2e spec: strategy-validation
 *
 * Covers the user story:
 *   As a security researcher saving a new or forked strategy, I want
 *   server-side validation to fire first and surface errors in a red error
 *   block before the strategy commits, AND if the validation endpoint fails
 *   or is unavailable, the save should fall through to the create POST so a
 *   transient outage doesn't block me.
 *
 * Production code under test:
 *   frontend/src/pages/StrategyEditor.tsx lines 845-868
 *   The validateStrategy() call with .catch(() => null), the short-circuit on
 *   invalid results, and the createStrategy() call that follows.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'
import { mockStrategiesRoutes, FULL_BUILTIN_SINGLE, CREATED_FORK } from './helpers/mockStrategiesRoutes'

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await mockStrategiesRoutes(page)
})

// Selector for the red error block — scoped so it won't collide with other
// elements if "Errors:" text ever appears elsewhere.
const errorBlock = (page: Parameters<typeof page.locator>[0] extends never ? never : import('@playwright/test').Page) =>
  page.locator('div.bg-red-50').filter({ has: page.getByText('Errors:') })

test.describe('StrategyEditor save validation flow', () => {
  // ---------------------------------------------------------------------------
  // Test 1: Validation returning invalid short-circuits the create POST
  // ---------------------------------------------------------------------------

  test('validation returning {valid:false} shows error block and does not call create POST', async ({ page }) => {
    // Layer a per-test override for the validate endpoint (LIFO: wins over base)
    await page.route('**/api/strategies/__new__/validate', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          valid: false,
          errors: ['model_id is required', 'system_prompt missing {repo_summary}'],
        }),
      })
    })

    // Count calls to the create endpoint (bare POST /api/strategies, not /validate)
    let createCount = 0
    await page.route('**/api/strategies', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      // The validate sub-resource URL ends with /__new__/validate, not bare /strategies.
      // This handler only fires for the bare create endpoint.
      createCount++
      return route.fallback()
    })

    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByRole('heading', { name: 'Fork Strategy' })).toBeVisible()
    await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

    await page.getByTestId('save-btn').click()

    // Red error block must appear
    const errBlock = errorBlock(page)
    await expect(errBlock).toBeVisible()
    await expect(errBlock.getByText('Errors:')).toBeVisible()
    await expect(errBlock.getByRole('listitem').filter({ hasText: 'model_id is required' })).toBeVisible()
    await expect(errBlock.getByRole('listitem').filter({ hasText: 'system_prompt missing {repo_summary}' })).toBeVisible()

    // Create POST must NOT have fired
    expect(createCount).toBe(0)

    // URL must still be on the fork page — no navigation
    await expect(page).toHaveURL(/\/strategies\/strat-builtin-single-001\/fork/)
  })

  // ---------------------------------------------------------------------------
  // Test 2: Multiple errors render as separate <li> items
  // ---------------------------------------------------------------------------

  test('four validation errors render as exactly four <li> items', async ({ page }) => {
    await page.route('**/api/strategies/__new__/validate', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          valid: false,
          errors: [
            'model_id is required',
            'system_prompt missing {repo_summary}',
            'max_turns must be > 0',
            'unknown tool: delete_file',
          ],
        }),
      })
    })

    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

    await page.getByTestId('save-btn').click()

    const errBlock = errorBlock(page)
    await expect(errBlock).toBeVisible()
    // Exactly 4 <li> elements inside the error block
    await expect(errBlock.getByRole('listitem')).toHaveCount(4)
  })

  // ---------------------------------------------------------------------------
  // Test 3: Validation returning valid:true proceeds to create POST and navigates
  // ---------------------------------------------------------------------------

  test('validation returning {valid:true} proceeds to create POST and navigates to new strategy', async ({ page }) => {
    // Default mockStrategiesRoutes already returns {valid:true}. Track create call.
    let createCalled = false
    await page.route('**/api/strategies', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      createCalled = true
      return route.fallback()
    })

    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

    await page.getByTestId('save-btn').click()

    await expect(page).toHaveURL(/\/strategies\/strat-new-fork-001/)
    expect(createCalled).toBe(true)
  })

  // ---------------------------------------------------------------------------
  // Test 4: Validation network abort is caught silently; create POST proceeds
  // ---------------------------------------------------------------------------

  test('validation network abort is swallowed and create POST still fires', async ({ page }) => {
    // Abort the validate request — production .catch(() => null) swallows this
    await page.route('**/api/strategies/__new__/validate', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.abort('failed')
    })

    let createCalled = false
    await page.route('**/api/strategies', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      createCalled = true
      return route.fallback()
    })

    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

    await page.getByTestId('save-btn').click()

    await expect(page).toHaveURL(/\/strategies\/strat-new-fork-001/)
    expect(createCalled).toBe(true)
  })

  // ---------------------------------------------------------------------------
  // Test 5: Validation 500 is caught silently; create POST proceeds
  // ---------------------------------------------------------------------------

  test('validation HTTP 500 is swallowed and create POST still fires', async ({ page }) => {
    // A 500 causes apiFetch to throw; production .catch(() => null) swallows it
    await page.route('**/api/strategies/__new__/validate', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal Server Error' }),
      })
    })

    let createCalled = false
    await page.route('**/api/strategies', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      createCalled = true
      return route.fallback()
    })

    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

    await page.getByTestId('save-btn').click()

    await expect(page).toHaveURL(/\/strategies\/strat-new-fork-001/)
    expect(createCalled).toBe(true)
  })

  // ---------------------------------------------------------------------------
  // Test 6: createStrategy throwing populates saveErrors
  // ---------------------------------------------------------------------------

  test('createStrategy 400 error surfaces the detail in the red error block', async ({ page }) => {
    // Validation passes (default); create POST returns 400
    await page.route('**/api/strategies', (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Strategy name conflict' }),
      })
    })

    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

    await page.getByTestId('save-btn').click()

    const errBlock = errorBlock(page)
    await expect(errBlock).toBeVisible()
    await expect(errBlock.getByText('Strategy name conflict')).toBeVisible()
  })

  // ---------------------------------------------------------------------------
  // Test 7: Save button shows "Saving…" while in-flight
  // ---------------------------------------------------------------------------

  test('save button shows "Saving…" and is disabled while request is in-flight', async ({ page }) => {
    // Delay both validate and create responses so we can observe the saving state
    await page.route('**/api/strategies/__new__/validate', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      await new Promise((r) => setTimeout(r, 1500))
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ valid: true, errors: [] }),
      })
    })

    await page.route('**/api/strategies', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      await new Promise((r) => setTimeout(r, 1500))
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(CREATED_FORK),
      })
    })

    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

    const saveBtn = page.getByTestId('save-btn')
    await saveBtn.click()

    // While in-flight the button should read "Saving…" and be disabled
    await expect(saveBtn).toHaveText('Saving…')
    await expect(saveBtn).toBeDisabled()

    // After responses resolve, navigation completes
    await expect(page).toHaveURL(/\/strategies\/strat-new-fork-001/, { timeout: 10000 })
  })

  // ---------------------------------------------------------------------------
  // Test 8: Fixing the cause and re-clicking clears the validation errors
  // ---------------------------------------------------------------------------

  test('re-clicking save after fixing the cause clears the validation error block', async ({ page }) => {
    // Named handler so we can unroute by reference, leaving the base
    // mockStrategiesRoutes validate handler intact for the second click.
    // Bare page.unroute(pattern) would remove BOTH handlers, falling through
    // to apiFetch's .catch(() => null) and silently bypassing validation.
    const failingValidate = async (route: import('@playwright/test').Route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ valid: false, errors: ['model_id is required'] }),
      })
    }
    await page.route('**/api/strategies/__new__/validate', failingValidate)

    await page.goto('/strategies/strat-builtin-single-001/fork')
    await expect(page.getByTestId('name-input')).toHaveValue('Fork of Zero Shot Single Agent')

    await page.getByTestId('save-btn').click()

    const errBlock = errorBlock(page)
    await expect(errBlock).toBeVisible()

    // Remove ONLY the failing override; the base {valid:true} handler stays.
    await page.unroute('**/api/strategies/__new__/validate', failingValidate)

    await page.getByTestId('save-btn').click()

    await expect(page).toHaveURL(/\/strategies\/strat-new-fork-001/)
    await expect(errBlock).not.toBeVisible()
  })
})
