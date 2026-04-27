import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// StrategySummary objects with full shape.
// The default mockApi /api/strategies handler returns plain strings, which breaks
// StrategyCard rendering and click-to-select flow. We override it here.
const STRATEGIES: import('../src/api/strategies').StrategySummary[] = [
  { id: 'strat-a', name: 'Zero Shot', orchestration_shape: 'single_agent', is_builtin: true, parent_strategy_id: null },
  { id: 'strat-b', name: 'Chain of Thought', orchestration_shape: 'single_agent', is_builtin: true, parent_strategy_id: null },
  { id: 'strat-c', name: 'Few Shot', orchestration_shape: 'per_file', is_builtin: true, parent_strategy_id: null },
]

async function mockStrategiesRoutes(page: import('@playwright/test').Page) {
  // Override GET /api/strategies → StrategySummary[]. Must be registered AFTER
  // mockApi() so LIFO ordering gives this handler higher priority.
  await page.route('**/api/strategies', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(STRATEGIES),
    })
  })
}

test.describe('CostEstimate on ExperimentNew', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await mockStrategiesRoutes(page)
  })

  // 1. Initial idle state -------------------------------------------------------
  test('shows idle state before any configuration', async ({ page }) => {
    await page.goto('/experiments/new')
    await expect(page.getByRole('heading', { name: 'New Experiment' })).toBeVisible()

    // h3 heading is always visible
    await expect(page.getByRole('heading', { name: 'Cost Estimate', level: 3 })).toBeVisible()

    // Idle copy is shown
    await expect(page.getByText('Configure experiment to see estimate.')).toBeVisible()

    // Spinner / Calculating text must NOT be present
    await expect(page.getByText('Calculating...')).not.toBeVisible()
  })

  // 2. Dataset only — no estimate fired -----------------------------------------
  test('selecting only a dataset keeps idle state with no API call', async ({ page }) => {
    await page.goto('/experiments/new')
    await expect(page.getByRole('heading', { name: 'New Experiment' })).toBeVisible()

    let estimateCallCount = 0
    // Register AFTER mockApi/mockStrategiesRoutes for LIFO priority
    await page.route('**/api/experiments/estimate*', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      estimateCallCount++
      return route.fallback()
    })

    await page.locator('select').first().selectOption('cve-2024-python')

    // Wait a bit longer than the 400ms debounce to give it time to fire if it would
    await page.waitForTimeout(600)

    // Idle copy still visible
    await expect(page.getByText('Configure experiment to see estimate.')).toBeVisible()
    // No "Total runs" shown
    await expect(page.getByText('Total runs')).not.toBeVisible()

    expect(estimateCallCount).toBe(0)
  })

  // 3. Dataset + strategy → estimate fires and renders --------------------------
  test('selecting dataset and strategy fires estimate and renders result', async ({ page }) => {
    await page.goto('/experiments/new')
    await expect(page.getByRole('heading', { name: 'New Experiment' })).toBeVisible()

    await page.locator('select').first().selectOption('cve-2024-python')
    // Click the first strategy card to select it
    await page.locator('[data-testid="strategy-card"]').first().click()

    // "Calculating..." should appear while in-flight (or resolve quickly from mock)
    // Wait for result to appear (the mock resolves instantly)
    await expect(page.getByText('Total runs')).toBeVisible({ timeout: 2000 })
    await expect(page.getByText('8')).toBeVisible()

    await expect(page.getByText('Estimated cost')).toBeVisible()
    await expect(page.getByText('$4.00')).toBeVisible()

    // Per-model table rows
    await expect(page.getByText('gpt-4o')).toBeVisible()
    await expect(page.getByText('claude-3-5-sonnet-20241022')).toBeVisible()
    // Each model cost
    await expect(page.getByText('$2.00').first()).toBeVisible()

    // "Configure experiment to see estimate." should be gone
    await expect(page.getByText('Configure experiment to see estimate.')).not.toBeVisible()
  })

  // 4. Spend Cap placeholder updates to 1.2× estimate ---------------------------
  test('spend cap placeholder updates to suggested value after estimate resolves', async ({ page }) => {
    await page.goto('/experiments/new')
    await expect(page.getByRole('heading', { name: 'New Experiment' })).toBeVisible()

    await page.locator('select').first().selectOption('cve-2024-python')
    await page.locator('[data-testid="strategy-card"]').first().click()

    // Wait for estimate to resolve
    await expect(page.getByText('Total runs')).toBeVisible({ timeout: 2000 })

    const spendCapInput = page.locator('input[type="number"][step="0.01"]')
    await expect(spendCapInput).toHaveAttribute('placeholder', /Suggested: \$4\.80/)
  })

  // 5. Deselecting strategy clears estimate back to idle -------------------------
  test('deselecting the strategy clears the estimate back to idle', async ({ page }) => {
    await page.goto('/experiments/new')
    await expect(page.getByRole('heading', { name: 'New Experiment' })).toBeVisible()

    await page.locator('select').first().selectOption('cve-2024-python')
    const firstCard = page.locator('[data-testid="strategy-card"]').first()
    await firstCard.click()

    // Wait for estimate
    await expect(page.getByText('Total runs')).toBeVisible({ timeout: 2000 })

    // Deselect the same card
    await firstCard.click()

    // Estimate should disappear and idle text return
    await expect(page.getByText('Configure experiment to see estimate.')).toBeVisible({ timeout: 2000 })
    await expect(page.getByText('Total runs')).not.toBeVisible()

    // Spend Cap placeholder reverts
    const spendCapInput = page.locator('input[type="number"][step="0.01"]')
    await expect(spendCapInput).toHaveAttribute('placeholder', 'e.g. 10.00')
  })

  // 6. Debounce: rapid toggles coalesce to exactly one API request ---------------
  test('rapid strategy toggles within debounce window coalesce to one API request', async ({ page }) => {
    await page.goto('/experiments/new')
    await expect(page.getByRole('heading', { name: 'New Experiment' })).toBeVisible()

    let count = 0
    await page.route('**/api/experiments/estimate*', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      count++
      return route.fallback()
    })

    await page.locator('select').first().selectOption('cve-2024-python')

    const cards = page.locator('[data-testid="strategy-card"]')

    // Select card A, then toggle card B on/off, all within the 400ms debounce window.
    // Card A ends selected; card B ends deselected. The last dep-change restarts the
    // debounce timer each time, so only one request fires after the final 400ms.
    await cards.nth(0).click()
    await cards.nth(1).click()
    await cards.nth(1).click()

    // Wait for debounce to fire and response to arrive
    await page.waitForTimeout(800)
    await expect(page.getByText('Total runs')).toBeVisible({ timeout: 2000 })

    // Only one estimate request should have been sent
    expect(count).toBe(1)
  })

  // 7. Spinner visible during in-flight estimate ---------------------------------
  test('shows Calculating... spinner while estimate request is in flight', async ({ page }) => {
    // Delay the estimate response by 1500ms
    await page.route('**/api/experiments/estimate*', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      await new Promise((r) => setTimeout(r, 1500))
      return route.fallback()
    })

    await page.goto('/experiments/new')
    await expect(page.getByRole('heading', { name: 'New Experiment' })).toBeVisible()

    await page.locator('select').first().selectOption('cve-2024-python')
    await page.locator('[data-testid="strategy-card"]').first().click()

    // After the debounce fires (400ms), loading state kicks in
    await expect(page.getByText('Calculating...')).toBeVisible({ timeout: 2000 })

    // After the delayed response resolves, loading text should disappear
    await expect(page.getByText('Calculating...')).not.toBeVisible({ timeout: 3000 })
    await expect(page.getByText('Total runs')).toBeVisible()
  })

  // 8. API error → idle state ---------------------------------------------------
  test('500 error response clears estimate back to idle state', async ({ page }) => {
    // Override the estimate endpoint to return 500 after the debounce fires
    await page.route('**/api/experiments/estimate*', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'internal server error' }),
      })
    })

    await page.goto('/experiments/new')
    await expect(page.getByRole('heading', { name: 'New Experiment' })).toBeVisible()

    await page.locator('select').first().selectOption('cve-2024-python')
    await page.locator('[data-testid="strategy-card"]').first().click()

    // Wait for debounce + response
    await page.waitForTimeout(700)

    // After error, estimate is null → idle copy returns
    await expect(page.getByText('Configure experiment to see estimate.')).toBeVisible({ timeout: 2000 })
    // No "Calculating..." (loading cleared in finally block)
    await expect(page.getByText('Calculating...')).not.toBeVisible()
    // No estimate data
    await expect(page.getByText('Total runs')).not.toBeVisible()
  })
})
