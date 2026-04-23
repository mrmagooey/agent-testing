/**
 * E2E coverage for the global /compare route (cross-experiment comparison).
 *
 * This spec covers gap #9: the top-level /compare route is distinct from the
 * scoped /experiments/:id/compare route even though both render RunCompare.
 * Key differences exercised here:
 *   - The global route accepts a_experiment/b_experiment/a_run/b_run query params
 *   - The global route breadcrumb is "Dashboard > Compare" (no experiment segment)
 *   - The global route keeps picker state in the URL and survives reloads
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// IDs from fixtures/experiments.json and fixtures/runs.json
const EXP_A = 'aaaaaaaa-0001-0001-0001-000000000001'
const EXP_B = 'bbbbbbbb-0002-0002-0002-000000000002'
const RUN_A = 'run-001-aaa'
const RUN_B = 'run-002-bbb'

// ---------------------------------------------------------------------------
// Test 1: Deep link — navigating directly to /compare with all four params
// loads the comparison view (run cards, findings tabs, venn summary).
// The /compare-runs mock returns comparison.json which drives all assertions.
// ---------------------------------------------------------------------------

test('deep link to /compare renders run comparison view', async ({ page }) => {
  await mockApi(page)
  await page.goto(
    `/compare?a_experiment=${EXP_A}&a_run=${RUN_A}&b_experiment=${EXP_B}&b_run=${RUN_B}`,
  )

  // Both run cards must appear
  await expect(page.getByRole('heading', { name: 'Run A' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Run B' })).toBeVisible()

  // Metric values from comparison.json run_a / run_b
  await expect(page.getByText('0.812').first()).toBeVisible()
  await expect(page.getByText('0.756').first()).toBeVisible()

  // Findings tab buttons from comparison.json counts (1 in both, 1 in A only, 0 in B only)
  await expect(page.getByRole('button', { name: /Found by Both \(1\)/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /Only in A \(1\)/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /Only in B \(0\)/ })).toBeVisible()

  // Summary Venn text from comparison.json
  await expect(page.getByText(/Run A found/)).toBeVisible()
  await expect(page.getByText(/Run B found/)).toBeVisible()
  await expect(page.getByText(/found by both/).last()).toBeVisible()

  // Shared finding title from comparison.json
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 2: Distinct from scoped compare — the two routes render different
// breadcrumb structures even though they share the RunCompare component.
// Global /compare: breadcrumb has 2 items — "Dashboard" then "Compare".
// Scoped /experiments/:id/compare: breadcrumb has 3 items — adds experiment id.
// ---------------------------------------------------------------------------

test('global /compare breadcrumb differs from scoped /experiments/:id/compare', async ({ page }) => {
  await mockApi(page)

  // -- Global route --
  await page.goto('/compare')

  // The breadcrumb nav contains exactly "Dashboard" and "Compare".
  // The scoped route would also include the experiment ID between them.
  const breadcrumbNav = page.getByRole('navigation', { name: 'Breadcrumb' })
  await expect(breadcrumbNav).toBeVisible()

  // Global breadcrumb: two items — "Dashboard" and "Compare" with no experiment segment
  await expect(breadcrumbNav.getByText('Dashboard')).toBeVisible()
  await expect(breadcrumbNav.getByText('Compare')).toBeVisible()
  // The experiment ID must NOT appear in the global breadcrumb
  await expect(breadcrumbNav.getByText(EXP_A)).not.toBeVisible()

  // -- Scoped route --
  await page.goto(`/experiments/${EXP_A}/compare`)

  // Scoped breadcrumb: three items — "Dashboard", experiment id, "Compare"
  const scopedBreadcrumb = page.getByRole('navigation', { name: 'Breadcrumb' })
  await expect(scopedBreadcrumb.getByText('Dashboard')).toBeVisible()
  await expect(scopedBreadcrumb.getByText(EXP_A)).toBeVisible()
  await expect(scopedBreadcrumb.getByText('Compare')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 3: URL state persistence — picker selections are reflected in the URL
// and survive a page reload.
//
// Flow:
//   1. Navigate to /compare (no params)
//   2. Select experiment A in the Run A picker → URL gains a_experiment=...
//   3. Select a run in the Run A picker → URL gains a_run=...
//   4. Reload → both selects still reflect the chosen values
// ---------------------------------------------------------------------------

test('picker selections update the URL and survive reload', async ({ page }) => {
  await mockApi(page)

  // Start with a pre-loaded URL so both pickers are initialised from query params.
  // The global route reads a_experiment, a_run, b_experiment, b_run from the URL.
  await page.goto(
    `/compare?a_experiment=${EXP_A}&a_run=${RUN_A}&b_experiment=${EXP_B}&b_run=${RUN_B}`,
  )

  // RunPicker renders two <select> elements per panel (experiment + run).
  // On the page there are 4 selects in order: A-exp, A-run, B-exp, B-run.
  const allSelects = page.locator('select')

  const aExpSelect = allSelects.nth(0)
  const aRunSelect = allSelects.nth(1)
  const bExpSelect = allSelects.nth(2)
  const bRunSelect = allSelects.nth(3)

  // Verify the selects reflect the URL params on initial load
  await expect(aExpSelect).toHaveValue(EXP_A)
  await expect(bExpSelect).toHaveValue(EXP_B)

  // Wait for Run A select to be enabled (runs loaded after experiment set) then check value
  await expect(aRunSelect).not.toBeDisabled()
  await expect(aRunSelect).toHaveValue(RUN_A)

  // Wait for Run B select to be enabled then check value
  await expect(bRunSelect).not.toBeDisabled()
  await expect(bRunSelect).toHaveValue(RUN_B)

  // Change Run A's run selection (not the experiment) — this is a single-field update
  // so handlePickerChange correctly accumulates the existing URL params.
  await aRunSelect.selectOption({ value: RUN_B })

  // URL must now contain the updated a_run value
  await expect(page).toHaveURL(new RegExp(`a_run=${RUN_B}`))

  // Reload — mock intercepts persist across reload on the same page object
  await page.reload()

  // After reload the component reads a_run from the URL and pre-selects the run
  const aRunAfterReload = page.locator('select').nth(1)
  await expect(aRunAfterReload).not.toBeDisabled()
  await expect(aRunAfterReload).toHaveValue(RUN_B)

  // The experiment select must also still reflect its original value from the URL
  const aExpAfterReload = page.locator('select').nth(0)
  await expect(aExpAfterReload).toHaveValue(EXP_A)
})
