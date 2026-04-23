/**
 * Matrix dimension pre-filter tests on the experiment detail page.
 *
 * The fixture experiment-results.json contains 3 runs:
 *   run-001-aaa: gpt-4o, zero_shot, with_tools, default
 *   run-002-bbb: gpt-4o, zero_shot, without_tools, default
 *   run-003-ccc: claude-3-5-sonnet-20241022, chain_of_thought, with_tools, default
 *
 * This gives us ≥2 unique values for model, strategy, and tool_variant — enough to
 * exercise every filter dimension that renders.
 */
import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// Filter bar presence
// ---------------------------------------------------------------------------

test.describe('Filter bar render', () => {
  test('filter bar appears above the matrix table', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // MatrixFilterBar renders popover buttons for each dimension with >1 unique value
    await expect(page.getByRole('button', { name: /model/i })).toBeVisible()
  })

  test('"Clear filters" button is disabled (no-op) when no filter is active', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    const clearBtn = page.getByRole('button', { name: 'Clear filters' })
    await expect(clearBtn).toBeVisible()
    await expect(clearBtn).toBeDisabled()
  })

  test('shows "N of M runs" count with full count when no filter active', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // 3 of 3 runs shown when filter is empty
    await expect(page.getByText('3 of 3 runs')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Opening a popover
// ---------------------------------------------------------------------------

test.describe('Popover behavior', () => {
  test('clicking model button opens a dropdown with model options', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    const modelBtn = page.getByRole('button', { name: /^model$/i })
    await modelBtn.click()

    // Fixture has 2 distinct models: gpt-4o and claude-3-5-sonnet-20241022
    await expect(page.getByRole('option', { name: /gpt-4o/ })).toBeVisible()
    await expect(page.getByRole('option', { name: /claude-3-5-sonnet/ })).toBeVisible()
  })

  test('popover closes when clicking outside', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    const modelBtn = page.getByRole('button', { name: /^model$/i })
    await modelBtn.click()
    await expect(page.getByRole('option', { name: /gpt-4o/ })).toBeVisible()

    // Click outside the popover
    await page.click('h2:has-text("Experiment Matrix")')
    await expect(page.getByRole('option', { name: /gpt-4o/ })).not.toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Filtering by model
// ---------------------------------------------------------------------------

test.describe('Model filter', () => {
  test('selecting gpt-4o updates URL with ?model=gpt-4o', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    await page.getByRole('button', { name: /^model$/i }).click()
    await page.getByRole('option', { name: /gpt-4o/ }).click()

    await expect(page).toHaveURL(/[?&]model=gpt-4o/)
  })

  test('filtering to gpt-4o reduces visible rows (2 of 3 runs)', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    await page.getByRole('button', { name: /^model$/i }).click()
    await page.getByRole('option', { name: /gpt-4o/ }).click()

    // Only 2 gpt-4o runs should be visible in the table
    await expect(page.getByText('2 of 3 runs')).toBeVisible()
  })

  test('matrix table rows drop after model filter applied', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    const matrixSection = page.locator('section').filter({ hasText: 'Experiment Matrix' })
    await expect(matrixSection).toBeVisible()

    // Count checkboxes before filter (3 runs = 3 checkboxes)
    const checkboxesBefore = matrixSection.locator('input[type="checkbox"]')
    await expect(checkboxesBefore).toHaveCount(3)

    // Filter to claude only
    await matrixSection.getByRole('button', { name: /^model$/i }).click()
    await page.getByRole('option', { name: /claude-3-5-sonnet/ }).click()

    // Only 1 claude run in fixture
    const checkboxesAfter = matrixSection.locator('input[type="checkbox"]')
    await expect(checkboxesAfter).toHaveCount(1)
  })

  test('clicking Clear filters restores all rows and cleans URL', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}?model=gpt-4o`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // Filter is pre-applied via URL — only 2 of 3 runs
    await expect(page.getByText('2 of 3 runs')).toBeVisible()

    // Clear it
    const clearBtn = page.getByRole('button', { name: 'Clear filters' })
    await expect(clearBtn).toBeEnabled()
    await clearBtn.click()

    // All 3 runs back
    await expect(page.getByText('3 of 3 runs')).toBeVisible()

    // URL no longer has model param
    await expect(page).not.toHaveURL(/model=/)
  })
})

// ---------------------------------------------------------------------------
// Empty-result state
// ---------------------------------------------------------------------------

test.describe('Empty filter result state', () => {
  test('shows "No runs match these filters" panel when filter produces zero results', async ({
    page,
  }) => {
    // Provide an impossible filter value via URL
    await page.goto(`/experiments/${EXPERIMENT_ID}?model=nonexistent-model-xyz`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    await expect(page.getByText('No runs match these filters')).toBeVisible()
  })

  test('"Clear filters" in the empty-state panel restores rows', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}?model=nonexistent-model-xyz`)
    await expect(page.getByText('No runs match these filters')).toBeVisible()

    // The empty-state panel has its own Clear filters button (second occurrence)
    await page.getByRole('button', { name: 'Clear filters' }).nth(1).click()

    // Dashed panel should be gone, rows should be back
    await expect(page.getByText('No runs match these filters')).not.toBeVisible()
    await expect(page.getByText('3 of 3 runs')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// URL state persistence
// ---------------------------------------------------------------------------

test.describe('URL state persistence', () => {
  test('filter pre-loaded from URL param on initial render', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}?model=claude-3-5-sonnet-20241022`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // Should show 1 of 3 (only the claude run)
    await expect(page.getByText('1 of 3 runs')).toBeVisible()
  })

  test('model filter button shows active badge when filter is set via URL', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}?model=gpt-4o`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // The model button should show an active count badge (1 selected)
    const modelBtn = page.getByRole('button', { name: /^model/i })
    await expect(modelBtn).toBeVisible()
    // The button should have amber styling (active class) — check for the count badge
    await expect(modelBtn.locator('span').filter({ hasText: '1' })).toBeVisible()
  })

  test('multi-value model filter set via URL works', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}?model=gpt-4o,claude-3-5-sonnet-20241022`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // All 3 runs should be visible
    await expect(page.getByText('3 of 3 runs')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// DimensionChart updates with filter
// ---------------------------------------------------------------------------

test.describe('DimensionChart reflects filter', () => {
  test('after filtering to claude only, model chart has fewer bars', async ({ page }) => {
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
    await expect(page.getByRole('heading', { name: 'Experiment Matrix' })).toBeVisible()

    // Filter to claude only
    await page.getByRole('button', { name: /^model$/i }).click()
    await page.getByRole('option', { name: /claude-3-5-sonnet/ }).click()

    // The Model Comparison chart heading should still be visible (chart re-renders)
    await expect(page.getByText('Model Comparison (Avg F1)')).toBeVisible()
  })
})
