/**
 * e2e spec: findings-drilldown
 *
 * Covers the drill-down user story for the global Findings page:
 *   - Clicking a FindingRow (scope='global') expands its description + CWE list.
 *   - Clicking the same row again collapses it.
 *   - Only one row is expanded at a time (single expandedId state).
 *   - Expanded row shows CWE chips, matched_label_id, and action links.
 *   - "Open run" link navigates to the originating run URL with #finding-<fid> fragment.
 *   - "View source" link navigates to the dataset source view at the right file + line.
 *   - The experiment-name link inside a row has stopPropagation and does NOT expand the row.
 *
 * Mock strategy: mockApi() already serves rich /findings data with find-001 and
 * find-002. No per-test overrides are needed except for navigation tests where we
 * want the destination page to mount cleanly — the shared helper already registers
 * GET handlers for /experiments/<id>/runs/<run> and /datasets/<name>.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const SQL_TITLE = 'SQL Injection in user login handler'
const SQL_DESC = 'User-supplied input is concatenated directly into a SQL query'
const XSS_TITLE = 'Reflected XSS in search results'

const EXP_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'
const FINDING_ID = 'find-001'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// 1. Row visible, initially NOT expanded
// ---------------------------------------------------------------------------

test('SQL Injection row is visible and initially not expanded', async ({ page }) => {
  await page.goto('/findings')

  const row = page.getByRole('row').filter({ hasText: SQL_TITLE })
  await expect(row).toBeVisible()

  // Description text must NOT be present anywhere on the page yet.
  await expect(page.getByText(SQL_DESC)).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// 2. Clicking the row expands it (description becomes visible)
// ---------------------------------------------------------------------------

test('clicking a finding row expands its description', async ({ page }) => {
  await page.goto('/findings')

  const row = page.getByRole('row').filter({ hasText: SQL_TITLE })
  await row.click()

  // Description text rendered inside the CodeViewer should be visible now.
  await expect(page.getByText(SQL_DESC)).toBeVisible()
})

// ---------------------------------------------------------------------------
// 3. Clicking the same row a second time collapses it
// ---------------------------------------------------------------------------

test('clicking an expanded row a second time collapses it', async ({ page }) => {
  await page.goto('/findings')

  const row = page.getByRole('row').filter({ hasText: SQL_TITLE })
  await row.click()
  await expect(page.getByText(SQL_DESC)).toBeVisible()

  // Second click — should collapse.
  await row.click()
  await expect(page.getByText(SQL_DESC)).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// 4. Only one row expanded at a time
// ---------------------------------------------------------------------------

test('expanding row B after row A leaves only row B expanded', async ({ page }) => {
  await page.goto('/findings')

  const rowA = page.getByRole('row').filter({ hasText: SQL_TITLE })
  const rowB = page.getByRole('row').filter({ hasText: XSS_TITLE })

  const xssDesc = 'The search query parameter is reflected in the response without HTML encoding'

  // Expand row A first.
  await rowA.click()
  await expect(page.getByText(SQL_DESC)).toBeVisible()

  // Expand row B — row A should collapse automatically.
  await rowB.click()
  await expect(page.getByText(xssDesc)).toBeVisible()
  await expect(page.getByText(SQL_DESC)).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// 5. CWE chips appear when row is expanded
// ---------------------------------------------------------------------------

test('CWE-89 chip is visible when find-001 is expanded', async ({ page }) => {
  await page.goto('/findings')

  const row = page.getByRole('row').filter({ hasText: SQL_TITLE })
  await row.click()

  // CWE code chip rendered inside <code> element.
  await expect(page.getByText('CWE-89').first()).toBeVisible()
})

// ---------------------------------------------------------------------------
// 6. Matched label line shown when present
// ---------------------------------------------------------------------------

test('matched_label_id is shown when find-001 is expanded', async ({ page }) => {
  await page.goto('/findings')

  const row = page.getByRole('row').filter({ hasText: SQL_TITLE })
  await row.click()

  // "Matched label: label-001" — the label id rendered in a <code> element.
  await expect(page.getByText('label-001')).toBeVisible()
})

// ---------------------------------------------------------------------------
// 7. "Open run" link — correct href and navigates to the run URL
// ---------------------------------------------------------------------------

test('"Open run" link has correct href and navigates to the run page', async ({ page }) => {
  await page.goto('/findings')

  const row = page.getByRole('row').filter({ hasText: SQL_TITLE })
  await row.click()

  const openRunLink = page.getByRole('link', { name: 'Open run' })
  await expect(openRunLink).toBeVisible()

  // Verify the href encodes experiment, run, and finding anchor correctly.
  const expectedHrefSuffix = `/experiments/${EXP_ID}/runs/${RUN_ID}#finding-${FINDING_ID}`
  const href = await openRunLink.getAttribute('href')
  expect(href).toMatch(new RegExp(expectedHrefSuffix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$'))

  // Click and assert we land on the run pathname (fragment may not be tracked by Playwright).
  await openRunLink.click()
  await expect(page).toHaveURL(new RegExp(`/experiments/${EXP_ID}/runs/${RUN_ID}`))
})

// ---------------------------------------------------------------------------
// 8. "View source" link — correct href and navigates to the dataset source view
// ---------------------------------------------------------------------------

test('"View source" link has correct href and navigates to the dataset source view', async ({ page }) => {
  await page.goto('/findings')

  const row = page.getByRole('row').filter({ hasText: SQL_TITLE })
  await row.click()

  const viewSourceLink = page.getByRole('link', { name: 'View source' })
  await expect(viewSourceLink).toBeVisible()

  // Href should include the dataset name, URL-encoded file path, and line number.
  const href = await viewSourceLink.getAttribute('href')
  expect(href).toContain('/datasets/cve-2024-python')
  expect(href).toContain('file=src%2Fauth%2Flogin.py')
  expect(href).toContain('line=42')

  // Click and confirm we land on the dataset page with file/line params.
  await viewSourceLink.click()
  await expect(page).toHaveURL(/\/datasets\/cve-2024-python/)
  await expect(page).toHaveURL(/file=src%2Fauth%2Flogin\.py/)
  await expect(page).toHaveURL(/line=42/)
})

// ---------------------------------------------------------------------------
// 9. Experiment-name link has stopPropagation — does NOT toggle expansion
// ---------------------------------------------------------------------------

test('clicking the experiment-name link does not toggle the row expansion', async ({ page }) => {
  await page.goto('/findings')

  // Pre-condition: row is collapsed — no <tr> in the body contains the description.
  const descRow = page.locator('tr').filter({ hasText: SQL_DESC })
  await expect(descRow).toHaveCount(0)

  // Intercept all <a> clicks via a capturing listener that calls preventDefault.
  // This keeps us on /findings so we can observe whether the <tr>'s onClick
  // (the row's expansion toggle) fired despite the link's stopPropagation.
  await page.evaluate(() => {
    document.addEventListener(
      'click',
      (e) => {
        const a = (e.target as HTMLElement).closest('a')
        if (a) e.preventDefault()
      },
      true,
    )
  })

  // Click the in-row experiment-name link. With preventDefault, navigation
  // is suppressed; with stopPropagation, the row's onClick should not fire
  // and the description row should NOT appear.
  const expLink = page.getByRole('link', { name: 'GPT-4o Zero-Shot April 2026' }).first()
  await expLink.click()

  // Still on /findings (navigation was suppressed).
  expect(page.url()).toContain('/findings')

  // The expansion <tr> with the description must still NOT exist — proves
  // the link's stopPropagation prevented the row's onClick from firing.
  await expect(descRow).toHaveCount(0)
})
