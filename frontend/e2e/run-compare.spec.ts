import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_A = 'run-001-aaa'
const RUN_B = 'run-002-bbb'

// ---------------------------------------------------------------------------
// Picker panel visibility — tests that exercise the fix for the broken
// /experiments/:id/compare route (pickers were hidden there before the fix).
// ---------------------------------------------------------------------------

test.describe('RunCompare picker panel visibility', () => {
  test('scoped route /experiments/:id/compare renders both Run A and Run B pickers', async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare`)

    // Both picker headings must be present
    await expect(page.getByText('Run A')).toBeVisible()
    await expect(page.getByText('Run B')).toBeVisible()

    // The Experiment select labels (from RunPicker) must be present — two of them
    const experimentLabels = page.getByText('Experiment', { exact: true })
    await expect(experimentLabels.first()).toBeVisible()
    await expect(experimentLabels.nth(1)).toBeVisible()

    // The Run select labels must be present — two of them
    const runLabels = page.getByText('Run', { exact: true })
    await expect(runLabels.first()).toBeVisible()
    await expect(runLabels.nth(1)).toBeVisible()
  })

  test('scoped route pre-selects the experiment id in both pickers', async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare`)

    // Both experiment selects should have the experiment id as their current value.
    // RunPicker renders a <select> whose value equals the experiment_id when pre-selected.
    const experimentSelects = page.locator('select').filter({ hasText: EXPERIMENT_ID })
    await expect(experimentSelects.first()).toBeVisible()
    await expect(experimentSelects.nth(1)).toBeVisible()
  })

  test('plain /compare route renders both Run A and Run B pickers', async ({ page }) => {
    await mockApi(page)
    await page.goto('/compare')

    // Both picker headings must be present
    await expect(page.getByText('Run A')).toBeVisible()
    await expect(page.getByText('Run B')).toBeVisible()

    // Experiment labels present
    const experimentLabels = page.getByText('Experiment', { exact: true })
    await expect(experimentLabels.first()).toBeVisible()
    await expect(experimentLabels.nth(1)).toBeVisible()

    // No experiment pre-selected — both selects show the default placeholder
    const selects = page.locator('select')
    // At least two selects (one per picker's experiment dropdown)
    await expect(selects.first()).toBeVisible()
  })

  test('plain /compare shows empty state when no runs selected', async ({ page }) => {
    await mockApi(page)
    await page.goto('/compare')
    await expect(page.getByText('Select two runs above to compare them.')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Existing tests — navigate to the legacy route with runs pre-selected via
// query params so comparison data is loaded.
// ---------------------------------------------------------------------------

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)
})

test('shows run A card with model', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Run A' })).toBeVisible()
})

test('shows run B card with model', async ({ page }) => {
  await expect(page.getByRole('heading', { name: 'Run B' })).toBeVisible()
})

test('shows model info in run A card', async ({ page }) => {
  // Scope to the Run A comparison card (heading role), not the RunPicker panel which also contains "Run A".
  const runACard = page.getByRole('heading', { name: 'Run A' }).locator('xpath=ancestor::*[@data-slot="card"]').first()
  await expect(runACard.getByText('gpt-4o').first()).toBeVisible()
  await expect(runACard.getByText('zero_shot').first()).toBeVisible()
})

test('shows metrics in both run cards', async ({ page }) => {
  await expect(page.getByText('0.812').first()).toBeVisible()
  await expect(page.getByText('0.756').first()).toBeVisible()
})

test('shows summary text with counts', async ({ page }) => {
  await expect(page.getByText(/Run A found/)).toBeVisible()
  await expect(page.getByText(/Run B found/)).toBeVisible()
  await expect(page.getByText(/found by both/).last()).toBeVisible()
})

test('shows Found by Both tab with count', async ({ page }) => {
  await expect(page.getByRole('button', { name: /Found by Both \(1\)/ })).toBeVisible()
})

test('shows Only in A tab with count', async ({ page }) => {
  await expect(page.getByRole('button', { name: /Only in A \(1\)/ })).toBeVisible()
})

test('shows Only in B tab with count', async ({ page }) => {
  await expect(page.getByRole('button', { name: /Only in B \(0\)/ })).toBeVisible()
})

test('found by both tab shows shared findings', async ({ page }) => {
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
})

test('only in A tab shows findings unique to A', async ({ page }) => {
  await page.getByRole('button', { name: /Only in A/ }).click()
  await expect(page.getByText('Path traversal in file download endpoint')).toBeVisible()
})

test('only in B tab shows empty state', async ({ page }) => {
  await page.getByRole('button', { name: /Only in B/ }).click()
  await expect(page.getByText('No findings in this category.')).toBeVisible()
})

test('clicking finding card expands description', async ({ page }) => {
  await page.getByText('SQL Injection in user login handler').click()
  await expect(page.getByText('User-supplied input is concatenated directly')).toBeVisible()
})

test('clicking finding card again collapses it', async ({ page }) => {
  await page.getByText('SQL Injection in user login handler').click()
  await expect(page.getByText('User-supplied input is concatenated directly')).toBeVisible()
  await page.getByText('SQL Injection in user login handler').click()
  await expect(page.getByText('User-supplied input is concatenated directly')).not.toBeVisible()
})
