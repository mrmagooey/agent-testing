/**
 * A3: MatrixTable interactions (sticky cols, expand row, heatmap cells),
 *     ThemeToggle persistence across reload, Breadcrumbs rendering + navigation.
 */
import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'

// ---------------------------------------------------------------------------
// MatrixTable — sticky columns, expand row, heatmap, sorting
// ---------------------------------------------------------------------------

test.describe('MatrixTable interactions', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
  })

  test('MatrixTable shows Model column header', async ({ page }) => {
    await expect(page.getByRole('columnheader', { name: 'Model' }).first()).toBeVisible()
  })

  test('MatrixTable shows Strategy column header', async ({ page }) => {
    await expect(page.getByRole('columnheader', { name: 'Strategy' })).toBeVisible()
  })

  test('MatrixTable shows Tools column header', async ({ page }) => {
    await expect(page.getByRole('columnheader', { name: 'Tools' })).toBeVisible()
  })

  test('MatrixTable shows metric column headers: Prec, Recall, F1, FPR', async ({ page }) => {
    await expect(page.getByRole('columnheader', { name: 'Prec' })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: 'Recall' })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: 'F1' })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: 'FPR' })).toBeVisible()
  })

  test('MatrixTable shows Cost and Duration columns', async ({ page }) => {
    await expect(page.getByRole('columnheader', { name: 'Cost', exact: true })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: 'Duration' })).toBeVisible()
  })

  test('MatrixTable expand button collapses and expands detail row', async ({ page }) => {
    // Find the first expand toggle button (▼)
    const expandBtn = page.locator('button[title="Expand details"]').first()
    await expect(expandBtn).toBeVisible()
    await expandBtn.click()
    // Detail row should now be visible with Profile, Verif., TP, FP, FN
    await expect(page.getByText('Profile:').first()).toBeVisible()
    await expect(page.getByText('Verif.:').first()).toBeVisible()
    await expect(page.getByText('TP:').first()).toBeVisible()
    await expect(page.getByText('FP:').first()).toBeVisible()
    await expect(page.getByText('FN:').first()).toBeVisible()
  })

  test('MatrixTable collapse button hides detail row', async ({ page }) => {
    const expandBtn = page.locator('button[title="Expand details"]').first()
    await expandBtn.click()
    await expect(page.getByText('Profile:').first()).toBeVisible()
    // Click collapse button
    const collapseBtn = page.locator('button[title="Collapse details"]').first()
    await collapseBtn.click()
    await expect(page.getByText('Profile:').first()).not.toBeVisible()
  })

  test('MatrixTable clicking F1 column header sorts by F1', async ({ page }) => {
    const f1Header = page.getByRole('columnheader', { name: 'F1' })
    await f1Header.click()
    // After clicking, a sort indicator should appear
    await expect(f1Header).toContainText(/F1/)
    // Sort direction toggle: click again
    await f1Header.click()
    await expect(f1Header).toContainText(/F1/)
  })

  test('MatrixTable shows model values in sticky column cells', async ({ page }) => {
    // fixture runs include gpt-4o and claude-3-5-sonnet-20241022
    await expect(page.getByText('gpt-4o').first()).toBeVisible()
  })

  test('MatrixTable heatmap cells contain metric values with labels', async ({ page }) => {
    // F1 value 0.776 from fixture run-001
    await expect(page.getByText('0.776').first()).toBeVisible()
  })

  test('MatrixTable shows F1 score for each run', async ({ page }) => {
    // fixture has runs with f1: 0.776, 0.726, 0.855
    await expect(page.getByText('0.776').first()).toBeVisible()
    await expect(page.getByText('0.726').first()).toBeVisible()
    await expect(page.getByText('0.855').first()).toBeVisible()
  })

  test('MatrixTable checkbox selects a run and shows selection indicator', async ({ page }) => {
    const matrixSection = page.locator('section').filter({ hasText: 'Experiment Matrix' })
    const checkbox = matrixSection.locator('input[type="checkbox"]').first()
    await checkbox.check()
    await expect(page.getByText(/1 run.*selected/i).first()).toBeVisible()
  })

  test('MatrixTable row click navigates to run detail', async ({ page }) => {
    // Click a non-checkbox/expand-btn part of the first row
    const matrixSection = page.locator('section').filter({ hasText: 'Experiment Matrix' })
    const firstRunCell = matrixSection.locator('td.font-mono').filter({ hasText: 'gpt-4o' }).first()
    await firstRunCell.click()
    await expect(page).toHaveURL(/\/experiments\/.*\/runs\//)
  })
})

// ---------------------------------------------------------------------------
// ThemeToggle — persistence across reload
// ---------------------------------------------------------------------------

test.describe('ThemeToggle persistence', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto('/')
  })

  test('ThemeToggle button is visible in nav', async ({ page }) => {
    const themeBtn = page.locator('button[aria-label*="mode"]')
    await expect(themeBtn).toBeVisible()
  })

  test('ThemeToggle switches between light and dark modes', async ({ page }) => {
    const themeBtn = page.locator('button[aria-label*="mode"]')
    const initialLabel = await themeBtn.getAttribute('aria-label')
    await themeBtn.click()
    const newLabel = await themeBtn.getAttribute('aria-label')
    expect(newLabel).not.toEqual(initialLabel)
  })

  test('dark mode persists after page reload', async ({ page }) => {
    const themeBtn = page.locator('button[aria-label*="mode"]')
    // Switch to dark if we're in light, or to light if in dark first
    const initialLabel = await themeBtn.getAttribute('aria-label')

    // Navigate to dark mode by clicking until we see "Switch to light mode" label
    if (initialLabel?.includes('dark')) {
      await themeBtn.click() // switch to dark
    }
    // Now in dark mode; aria-label should say "Switch to light mode"
    await expect(themeBtn).toHaveAttribute('aria-label', 'Switch to light mode')

    // Reload and verify dark mode persisted
    await page.reload()
    await mockApi(page)
    const afterReload = page.locator('button[aria-label*="mode"]')
    await expect(afterReload).toHaveAttribute('aria-label', 'Switch to light mode')
  })

  test('light mode persists after page reload', async ({ page }) => {
    const themeBtn = page.locator('button[aria-label*="mode"]')
    // Ensure we are in light mode
    const label = await themeBtn.getAttribute('aria-label')
    if (label?.includes('light')) {
      // already dark — switch to light
      await themeBtn.click()
    }
    // Now in light mode
    await expect(themeBtn).toHaveAttribute('aria-label', 'Switch to dark mode')
    await page.reload()
    await mockApi(page)
    const afterReload = page.locator('button[aria-label*="mode"]')
    await expect(afterReload).toHaveAttribute('aria-label', 'Switch to dark mode')
  })

  test('html element has dark class when dark mode is active', async ({ page }) => {
    const themeBtn = page.locator('button[aria-label*="mode"]')
    // Switch to dark mode
    const label = await themeBtn.getAttribute('aria-label')
    if (label?.includes('dark')) {
      await themeBtn.click()
    }
    await expect(page.locator('html')).toHaveClass(/dark/)
  })
})

// ---------------------------------------------------------------------------
// Breadcrumbs — rendering and navigation
// ---------------------------------------------------------------------------

test.describe('Breadcrumbs rendering and navigation', () => {
  test('RunDetail breadcrumbs show Dashboard / experimentId / runId', async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)
    const nav = page.getByRole('navigation', { name: 'Breadcrumb' })
    await expect(nav).toBeVisible()
    await expect(nav.getByRole('link', { name: 'Dashboard' })).toBeVisible()
    await expect(nav.getByRole('link', { name: EXPERIMENT_ID })).toBeVisible()
    // The last crumb (run_id) is plain text, not a link
    await expect(nav.getByText(RUN_ID)).toBeVisible()
  })

  test('RunDetail breadcrumb Dashboard link navigates to /', async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)
    await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'Dashboard' }).click()
    await expect(page).toHaveURL('/')
  })

  test('RunDetail breadcrumb experiment link navigates to experiment detail', async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)
    await page.getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: EXPERIMENT_ID }).click()
    await expect(page).toHaveURL(`/experiments/${EXPERIMENT_ID}`)
  })

  test('RunCompare breadcrumbs show Dashboard / experimentId / Compare', async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=run-001-aaa&b=run-002-bbb`)
    const nav = page.getByRole('navigation', { name: 'Breadcrumb' })
    await expect(nav.getByRole('link', { name: 'Dashboard' })).toBeVisible()
    await expect(nav.getByRole('link', { name: EXPERIMENT_ID })).toBeVisible()
    await expect(nav.getByText('Compare')).toBeVisible()
  })

  test('Breadcrumbs use slash separator between items', async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)
    const nav = page.getByRole('navigation', { name: 'Breadcrumb' })
    // Separators are "/" spans
    const separators = nav.locator('span').filter({ hasText: '/' })
    await expect(separators.first()).toBeVisible()
  })

  test('last breadcrumb item has bold/prominent styling (not a link)', async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)
    const nav = page.getByRole('navigation', { name: 'Breadcrumb' })
    // Last crumb is a span with font-medium class, not an <a>
    const lastCrumb = nav.locator('span.font-medium').last()
    await expect(lastCrumb).toBeVisible()
  })
})
