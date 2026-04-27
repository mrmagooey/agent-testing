/**
 * LabelsFilterBar — e2e tests
 *
 * Covers the user story: a security researcher browsing a dataset's labels
 * can filter the labels table by CWE, severity, and source (individually and
 * combined) — each filter change re-fetches
 * GET /api/datasets/<name>/labels?cwe=…&severity=…&source=… with the chosen
 * values, and a "Clear filters" link appears once any filter is active and
 * resets all three when clicked.
 *
 * Component under test: LabelsFilterBar in frontend/src/pages/DatasetDetail.tsx
 * (lines 269–344).
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DATASET = 'cve-2024-python'
const PAGE_URL = `/datasets/${DATASET}`

// ---------------------------------------------------------------------------
// Fixture
//
// Inline a minimal labels fixture whose shape matches the Label interface in
// frontend/src/api/client.ts. Three entries — label-001 uses file_path
// "src/auth/login.py" which the assertions below rely on.
// ---------------------------------------------------------------------------

const LABELS_FIXTURE = [
  {
    label_id: 'label-001',
    dataset: DATASET,
    file_path: 'src/auth/login.py',
    line_start: 42,
    line_end: 47,
    vuln_class: 'sqli',
    cwe: 'CWE-89',
    severity: 'critical',
    description: 'SQL injection via unsanitized user input in login query',
    source: 'cve',
  },
  {
    label_id: 'label-002',
    dataset: DATASET,
    file_path: 'src/api/users.py',
    line_start: 103,
    line_end: 108,
    vuln_class: 'ssrf',
    cwe: 'CWE-918',
    severity: 'high',
    description: 'SSRF via user-controlled URL in image fetch endpoint',
    source: 'manual',
  },
  {
    label_id: 'label-003',
    dataset: DATASET,
    file_path: 'src/files/download.py',
    line_start: 15,
    line_end: 20,
    vuln_class: 'path_traversal',
    cwe: 'CWE-22',
    severity: 'high',
    description: 'Path traversal in file download handler',
    source: 'cve',
  },
]

// ---------------------------------------------------------------------------
// Helper: register a per-test labels route handler AFTER mockApi so LIFO
// ordering makes our handler win over the one in mockApi.
//
// The mockApi labels regex is /^\/datasets\/[^/]+\/labels$/ which does NOT
// match URLs with query strings. The glob pattern here uses a trailing `*`
// to catch both bare and query-string URLs, ensuring all filter requests are
// served by our handler and URL capture works correctly.
// ---------------------------------------------------------------------------

async function setupLabelsCapture(
  page: Parameters<typeof mockApi>[0],
): Promise<{ captured: string[] }> {
  const captured: string[] = []
  await page.route(`**/api/datasets/${DATASET}/labels*`, (route) => {
    // The component only issues GET; chain to next handler for any other method
    // (consistent with sibling specs).
    if (route.request().method() !== 'GET') return route.fallback()
    captured.push(route.request().url())
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(LABELS_FIXTURE),
    })
  })
  return { captured }
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// 1. Filter bar is visible; all controls start empty; Clear is hidden
// ---------------------------------------------------------------------------

test('filter bar is visible with all three controls empty and no Clear button', async ({ page }) => {
  const { captured } = await setupLabelsCapture(page)
  await page.goto(PAGE_URL)

  // Wait for the filter bar to be rendered
  const cweInput = page.getByTestId('filter-cwe')
  const severitySelect = page.getByTestId('filter-severity')
  const sourceInput = page.getByTestId('filter-source')

  await expect(cweInput).toBeVisible()
  await expect(severitySelect).toBeVisible()
  await expect(sourceInput).toBeVisible()

  // All controls start in their empty/default state
  await expect(cweInput).toHaveValue('')
  await expect(severitySelect).toHaveValue('')
  await expect(sourceInput).toHaveValue('')

  // Clear button must not be present when no filter is active
  await expect(page.getByTestId('filter-clear')).not.toBeVisible()

  // Sanity: at least one labels request was made
  expect(captured.length).toBeGreaterThan(0)
})

// ---------------------------------------------------------------------------
// 2. Initial page load fires GET /labels with NO query params
// ---------------------------------------------------------------------------

test('initial page load fires GET /labels with no query params', async ({ page }) => {
  const { captured } = await setupLabelsCapture(page)
  await page.goto(PAGE_URL)

  await expect(page.getByTestId('filter-cwe')).toBeVisible()

  expect(captured.length).toBeGreaterThan(0)
  const firstUrl = new URL(captured[0])
  expect(firstUrl.searchParams.get('cwe')).toBeNull()
  expect(firstUrl.searchParams.get('severity')).toBeNull()
  expect(firstUrl.searchParams.get('source')).toBeNull()
})

// ---------------------------------------------------------------------------
// 3. Typing into CWE input refetches with ?cwe=CWE-89
// ---------------------------------------------------------------------------

test('typing CWE-89 into filter-cwe triggers refetch with ?cwe=CWE-89', async ({ page }) => {
  const { captured } = await setupLabelsCapture(page)
  await page.goto(PAGE_URL)

  // Wait for initial render to settle before interacting
  await expect(page.getByTestId('filter-cwe')).toBeVisible()

  // fill() is atomic — fires a single change event rather than per-keystroke events
  await page.getByTestId('filter-cwe').fill('CWE-89')

  // Wait until the captured array records a request with the full cwe value
  await expect
    .poll(() => {
      const last = captured.at(-1)
      if (!last) return null
      return new URL(last).searchParams.get('cwe')
    })
    .toBe('CWE-89')

  const lastUrl = new URL(captured.at(-1)!)
  expect(lastUrl.searchParams.get('cwe')).toBe('CWE-89')
})

// ---------------------------------------------------------------------------
// 4. Selecting severity=critical refetches with ?severity=critical
// ---------------------------------------------------------------------------

test('selecting "critical" from filter-severity triggers refetch with ?severity=critical', async ({ page }) => {
  const { captured } = await setupLabelsCapture(page)
  await page.goto(PAGE_URL)

  await expect(page.getByTestId('filter-severity')).toBeVisible()

  await page.getByTestId('filter-severity').selectOption('critical')

  await expect
    .poll(() => {
      const last = captured.at(-1)
      if (!last) return null
      return new URL(last).searchParams.get('severity')
    })
    .toBe('critical')

  const lastUrl = new URL(captured.at(-1)!)
  expect(lastUrl.searchParams.get('severity')).toBe('critical')
})

// ---------------------------------------------------------------------------
// 5. Typing into source input refetches with ?source=manual
// ---------------------------------------------------------------------------

test('typing "manual" into filter-source triggers refetch with ?source=manual', async ({ page }) => {
  const { captured } = await setupLabelsCapture(page)
  await page.goto(PAGE_URL)

  await expect(page.getByTestId('filter-source')).toBeVisible()

  await page.getByTestId('filter-source').fill('manual')

  await expect
    .poll(() => {
      const last = captured.at(-1)
      if (!last) return null
      return new URL(last).searchParams.get('source')
    })
    .toBe('manual')

  const lastUrl = new URL(captured.at(-1)!)
  expect(lastUrl.searchParams.get('source')).toBe('manual')
})

// ---------------------------------------------------------------------------
// 6. Combining filters: CWE + severity + source all appear in the final URL
// ---------------------------------------------------------------------------

test('combining cwe=CWE-89, severity=high, source=manual produces all three params in final request', async ({ page }) => {
  const { captured } = await setupLabelsCapture(page)
  await page.goto(PAGE_URL)

  await expect(page.getByTestId('filter-cwe')).toBeVisible()

  // Apply all three filters in sequence
  await page.getByTestId('filter-cwe').fill('CWE-89')
  await page.getByTestId('filter-severity').selectOption('high')
  await page.getByTestId('filter-source').fill('manual')

  // After all three are set, wait until the last captured URL carries all params
  await expect
    .poll(() => {
      const last = captured.at(-1)
      if (!last) return null
      const u = new URL(last)
      // Return a stable representation only once all three params are present
      if (
        u.searchParams.get('cwe') === 'CWE-89' &&
        u.searchParams.get('severity') === 'high' &&
        u.searchParams.get('source') === 'manual'
      ) {
        return 'all-set'
      }
      return null
    })
    .toBe('all-set')

  const lastUrl = new URL(captured.at(-1)!)
  expect(lastUrl.searchParams.get('cwe')).toBe('CWE-89')
  expect(lastUrl.searchParams.get('severity')).toBe('high')
  expect(lastUrl.searchParams.get('source')).toBe('manual')
})

// ---------------------------------------------------------------------------
// 7. Clear button appears when any filter is active
// ---------------------------------------------------------------------------

test('"Clear filters" button appears when at least one filter is active', async ({ page }) => {
  await setupLabelsCapture(page)
  await page.goto(PAGE_URL)

  await expect(page.getByTestId('filter-cwe')).toBeVisible()

  // Initially hidden
  await expect(page.getByTestId('filter-clear')).not.toBeVisible()

  // Activate one filter
  await page.getByTestId('filter-cwe').fill('CWE-89')

  // Button should now appear
  await expect(page.getByTestId('filter-clear')).toBeVisible()
})

// ---------------------------------------------------------------------------
// 8. Clicking "Clear filters" resets all inputs and fires a bare /labels request
// ---------------------------------------------------------------------------

test('clicking "Clear filters" resets all filter inputs and refetches with no query params', async ({ page }) => {
  const { captured } = await setupLabelsCapture(page)
  await page.goto(PAGE_URL)

  await expect(page.getByTestId('filter-cwe')).toBeVisible()

  // Set all three filters
  await page.getByTestId('filter-cwe').fill('CWE-89')
  await page.getByTestId('filter-severity').selectOption('critical')
  await page.getByTestId('filter-source').fill('manual')

  // Wait for the Clear button to be present before clicking
  await expect(page.getByTestId('filter-clear')).toBeVisible()

  // Record how many requests have been captured so far
  const countBefore = captured.length

  // Click Clear
  await page.getByTestId('filter-clear').click()

  // All inputs should be reset to empty/default
  await expect(page.getByTestId('filter-cwe')).toHaveValue('')
  await expect(page.getByTestId('filter-severity')).toHaveValue('')
  await expect(page.getByTestId('filter-source')).toHaveValue('')

  // Clear button should disappear once all filters are empty
  await expect(page.getByTestId('filter-clear')).not.toBeVisible()

  // A new fetch must have been triggered after clearing
  await expect.poll(() => captured.length).toBeGreaterThan(countBefore)

  // The last request after clearing should carry no filter params
  const lastUrl = new URL(captured.at(-1)!)
  expect(lastUrl.searchParams.get('cwe')).toBeNull()
  expect(lastUrl.searchParams.get('severity')).toBeNull()
  expect(lastUrl.searchParams.get('source')).toBeNull()
})

// ---------------------------------------------------------------------------
// 9. Mocked labels response is rendered in the table
// ---------------------------------------------------------------------------

test('mocked labels response is rendered — at least one row shows src/auth/login.py', async ({ page }) => {
  await setupLabelsCapture(page)
  await page.goto(PAGE_URL)

  // Wait for the labels table to render at least one row from the fixture
  await expect(page.getByText('src/auth/login.py').first()).toBeVisible()
})
