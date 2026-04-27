import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// StrategySummary objects matching the current StrategySummary shape.
// The default mockApi /api/strategies handler returns plain strings, which
// breaks StrategyCard rendering. Tests that need to interact with strategy
// cards register this override (pattern from cost-estimate.spec.ts).
const STRATEGIES: import('../src/api/strategies').StrategySummary[] = [
  { id: 'zero_shot', name: 'Zero Shot', orchestration_shape: 'single_agent', is_builtin: true, parent_strategy_id: null },
  { id: 'chain_of_thought', name: 'Chain of Thought', orchestration_shape: 'single_agent', is_builtin: true, parent_strategy_id: null },
]

async function mockStrategiesRoutes(page: import('@playwright/test').Page) {
  await page.route('**/api/strategies', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(STRATEGIES),
    })
  })
}

/** Override POST /experiments to accept the current body shape (strategy_ids). */
async function mockExperimentsPost(page: import('@playwright/test').Page) {
  await page.route('**/api/experiments', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        experiment_id: 'newexperiment-1111-1111-1111-111111111111',
        status: 'pending',
      }),
    })
  })
}

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto('/experiments/new')
})

test('shows page heading', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
})

test('shows dataset selector', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Dataset' })).toBeVisible()
  const select = page.locator('select').first()
  await expect(select).toBeVisible()
  await expect(select.locator('option', { hasText: 'cve-2024-python' })).toBeAttached()
})

test('shows repetitions input', async ({ page }) => {
  const repetitionsInput = page.locator('input[type="number"][min="1"][max="10"]')
  await expect(repetitionsInput).toBeVisible()
  await expect(repetitionsInput).toHaveValue('1')
})

test('shows submit experiment button', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Submit Experiment' })).toBeVisible()
})

test('validates dataset required', async ({ page }) => {
  await page.getByRole('button', { name: 'Submit Experiment' }).click()
  await expect(page.getByText('Please select a dataset')).toBeVisible()
})

test('validates at least one strategy required', async ({ page }) => {
  await page.locator('select').first().selectOption('cve-2024-python')
  await page.getByRole('button', { name: 'Submit Experiment' }).click()
  await expect(page.getByText('Select at least one strategy')).toBeVisible()
})

test('spend cap input accepts decimal values', async ({ page }) => {
  const spendCapInput = page.locator('input[type="number"][step="0.01"]')
  await spendCapInput.fill('25.50')
  await expect(spendCapInput).toHaveValue('25.50')
})

test('shows "Allow unavailable models" checkbox', async ({ page }) => {
  await expect(page.getByTestId('allow-unavailable-checkbox')).toBeVisible()
  await expect(page.getByTestId('allow-unavailable-checkbox')).not.toBeChecked()
})

test('successful form submission navigates to experiment detail', async ({ page }) => {
  await mockStrategiesRoutes(page)
  await mockExperimentsPost(page)
  await page.reload()
  await page.locator('select').first().selectOption('cve-2024-python')
  await page.locator('[data-testid="strategy-card"]').first().click()
  await page.getByRole('button', { name: 'Submit Experiment' }).click()
  await expect(page).toHaveURL(/\/experiments\/newexperiment-1111/)
})

test('checking "Allow unavailable models" sends allow_unavailable_models:true in payload', async ({ page }) => {
  await mockStrategiesRoutes(page)
  await mockExperimentsPost(page)
  await page.reload()

  const postPromise = page.waitForRequest(
    (req) => req.url().includes('/api/experiments') && req.method() === 'POST'
  )

  await page.locator('select').first().selectOption('cve-2024-python')
  await page.locator('[data-testid="strategy-card"]').first().click()
  await page.getByTestId('allow-unavailable-checkbox').check()
  await page.getByRole('button', { name: 'Submit Experiment' }).click()

  const req = await postPromise
  const body = req.postDataJSON()
  expect(body.allow_unavailable_models).toBe(true)
})
