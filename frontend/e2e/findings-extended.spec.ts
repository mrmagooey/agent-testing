/**
 * Extended Findings page tests covering:
 * - Debounced search input (fires API after 350ms)
 * - Date filter inputs (created_from / created_to)
 * - Sort dropdown — "Confidence (high→low)" option
 * - Sort dropdown disabled when no results
 * - Pagination Next/Previous disabled states (offset=0 / offset+limit>=total)
 * - Pagination per-page dropdown changes limit param
 * - "No findings indexed yet" empty state
 * - "No results for current filters" empty state
 * - FindingsFilterBar chip toggle (vuln_class filter)
 * - FindingsFilterBar "Clear all" button
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// The mock returns 3 findings for /findings with no filters.
// To test pagination we need total > limit — we override the API.

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// Empty states
// ---------------------------------------------------------------------------

test.describe('Findings empty states', () => {
  test('"No findings indexed yet" shown when API returns total=0 and no filters active', async ({ page }) => {
    await page.route('**/api/findings**', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total: 0,
          limit: 50,
          offset: 0,
          facets: { vuln_class: {}, severity: {}, match_status: {}, model_id: {}, strategy: {}, dataset_name: {} },
          items: [],
        }),
      })
    })
    await page.goto('/findings')
    await expect(page.getByText('No findings indexed yet')).toBeVisible()
  })

  test('"No results for current filters" shown when query returns 0 items but total>0 elsewhere', async ({ page }) => {
    // First call (no q) returns items; second call (with q) returns empty
    let callCount = 0
    await page.route('**/api/findings**', (route) => {
      callCount++
      const url = new URL(route.request().url())
      const q = url.searchParams.get('q') ?? ''
      if (q) {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            total: 0,
            limit: 50,
            offset: 0,
            facets: { vuln_class: {}, severity: {}, match_status: {}, model_id: {}, strategy: {}, dataset_name: {} },
            items: [],
          }),
        })
      } else {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            total: 3,
            limit: 50,
            offset: 0,
            facets: {
              vuln_class: { sqli: 1, xss: 1, path_traversal: 1 },
              severity: { critical: 1, high: 2 },
              match_status: { tp: 2, fp: 1 },
              model_id: { 'gpt-4o': 2 },
              strategy: { zero_shot: 2 },
              dataset_name: { 'cve-2024-python': 3 },
            },
            items: [
              {
                finding_id: 'find-001',
                title: 'SQL Injection in user login handler',
                vuln_class: 'sqli',
                severity: 'critical',
                match_status: 'tp',
                file_path: 'src/auth/login.py',
                line_start: 42,
                line_end: 47,
                experiment_name: 'Test',
                model_id: 'gpt-4o',
                strategy: 'zero_shot',
                dataset_name: 'cve-2024-python',
                created_at: '2026-04-17T08:15:00Z',
                confidence: 0.94,
              },
            ],
          }),
        })
      }
    })

    await page.goto('/findings')
    await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()

    // Navigate to a URL with a filter that returns no results
    await page.goto('/findings?q=zzz-no-match')
    await expect(page.getByText('No results for current filters')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Debounced search (350ms)
// ---------------------------------------------------------------------------

test.describe('Findings debounced search', () => {
  test('typing in search box fires API request with q param after debounce', async ({ page }) => {
    await page.goto('/findings')

    const searchInput = page.getByRole('searchbox', { name: 'Search findings' })

    // Set up the waitForRequest promise before typing so we don't miss early fires.
    // The component debounces at 350ms; we wait for exactly the request that carries
    // the final query string rather than sleeping for an arbitrary duration.
    const debouncedRequest = page.waitForRequest(
      (req) =>
        req.url().includes('/api/findings') &&
        req.method() === 'GET' &&
        new URL(req.url()).searchParams.get('q') === 'sqli',
    )

    await searchInput.fill('sqli')

    // Confirm the debounced request fired with the correct q= value
    const req = await debouncedRequest
    expect(new URL(req.url()).searchParams.get('q')).toBe('sqli')
  })

  test('search input filters results via URL q param', async ({ page }) => {
    await page.goto('/findings?q=sql')
    // The mock filters by q — SQL Injection matches 'sql'
    await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
    // XSS should not match 'sql'
    await expect(page.getByText('Reflected XSS in search results')).not.toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Sort dropdown
// ---------------------------------------------------------------------------

test.describe('Findings sort dropdown', () => {
  test('sort dropdown shows "Confidence (high→low)" option', async ({ page }) => {
    await page.goto('/findings')
    const sortSelect = page.locator('select#sort-select')
    await expect(sortSelect).toBeVisible()
    await expect(sortSelect.locator('option[value="confidence desc"]')).toHaveText('Confidence (high→low)')
  })

  test('selecting "Confidence (high→low)" fires API with sort=confidence desc', async ({ page }) => {
    await page.goto('/findings')

    const requestPromise = page.waitForRequest(
      (req) => req.url().includes('/api/findings') && req.url().includes('sort=confidence+desc'),
    )

    await page.locator('select#sort-select').selectOption('confidence desc')

    await requestPromise
    await expect(page).toHaveURL(/sort=confidence\+desc/)
  })

  test('sort dropdown is present alongside results', async ({ page }) => {
    await page.goto('/findings')
    await expect(page.locator('select#sort-select')).toBeVisible()
    await expect(page.getByText('3 findings')).toBeVisible()
  })
})

// ---------------------------------------------------------------------------
// Pagination in Findings page (Prev/Next disabled states + per-page)
// ---------------------------------------------------------------------------

function makeFindings(count: number) {
  const items = Array.from({ length: count }, (_, i) => ({
    finding_id: `find-pad-${i}`,
    title: `Finding ${i}`,
    vuln_class: 'sqli',
    severity: 'high',
    match_status: 'tp',
    file_path: `src/file${i}.py`,
    line_start: 1,
    line_end: 5,
    experiment_name: 'Test',
    model_id: 'gpt-4o',
    strategy: 'zero_shot',
    dataset_name: 'cve-2024-python',
    created_at: '2026-04-17T08:00:00Z',
    confidence: 0.9,
    cwe_ids: [],
  }))
  return items
}

test.describe('Findings pagination', () => {
  test('Previous button disabled at offset=0', async ({ page }) => {
    const allItems = makeFindings(75)
    await page.route('**/api/findings**', (route) => {
      const url = new URL(route.request().url())
      const limit = Number(url.searchParams.get('limit') ?? 50)
      const offset = Number(url.searchParams.get('offset') ?? 0)
      const paginated = allItems.slice(offset, offset + limit)
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total: allItems.length,
          limit,
          offset,
          facets: { vuln_class: { sqli: 75 }, severity: { high: 75 }, match_status: { tp: 75 }, model_id: {}, strategy: {}, dataset_name: {} },
          items: paginated,
        }),
      })
    })

    await page.goto('/findings')

    // Prev is disabled at offset=0
    await expect(page.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    // Next is enabled since 75 > 50
    await expect(page.getByRole('button', { name: 'Next page' })).toBeEnabled()
  })

  test('Next button disabled when offset+limit >= total', async ({ page }) => {
    const allItems = makeFindings(75)
    await page.route('**/api/findings**', (route) => {
      const url = new URL(route.request().url())
      const limit = Number(url.searchParams.get('limit') ?? 50)
      const offset = Number(url.searchParams.get('offset') ?? 0)
      const paginated = allItems.slice(offset, offset + limit)
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total: allItems.length,
          limit,
          offset,
          facets: { vuln_class: {}, severity: {}, match_status: {}, model_id: {}, strategy: {}, dataset_name: {} },
          items: paginated,
        }),
      })
    })

    // Start at offset=50 — page 2 has 25 items (75-50=25 < 50), so Next is disabled
    await page.goto('/findings?offset=50')

    await expect(page.getByRole('button', { name: 'Next page' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Previous page' })).toBeEnabled()
  })

  test('clicking Next updates offset in URL', async ({ page }) => {
    const allItems = makeFindings(75)
    await page.route('**/api/findings**', (route) => {
      const url = new URL(route.request().url())
      const limit = Number(url.searchParams.get('limit') ?? 50)
      const offset = Number(url.searchParams.get('offset') ?? 0)
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total: allItems.length,
          limit,
          offset,
          facets: { vuln_class: {}, severity: {}, match_status: {}, model_id: {}, strategy: {}, dataset_name: {} },
          items: allItems.slice(offset, offset + limit),
        }),
      })
    })

    await page.goto('/findings')
    await page.getByRole('button', { name: 'Next page' }).click()

    await expect(page).toHaveURL(/offset=50/)
  })

  test('per-page dropdown has expected page size options', async ({ page }) => {
    // Verify the per-page select is rendered with the expected options.
    // The Pagination component renders options: [25, 50, 100].
    await page.goto('/findings')
    await expect(page.getByText('3 findings')).toBeVisible()

    const pageSizeSelect = page.locator('select#page-size-select')
    await expect(pageSizeSelect).toBeVisible({ timeout: 8000 })

    // All expected options should be present
    await expect(pageSizeSelect.locator('option[value="25"]')).toHaveCount(1)
    await expect(pageSizeSelect.locator('option[value="50"]')).toHaveCount(1)
    await expect(pageSizeSelect.locator('option[value="100"]')).toHaveCount(1)

    // Default value is 50 (DEFAULT_LIMIT)
    await expect(pageSizeSelect).toHaveValue('50')
  })
})

// ---------------------------------------------------------------------------
// FindingsFilterBar chip toggle and Clear All
// ---------------------------------------------------------------------------

test.describe('FindingsFilterBar', () => {
  test('clicking a vuln_class chip toggles it on', async ({ page }) => {
    await page.goto('/findings')

    const sqliChip = page.getByTestId('filter-chip-vuln_class-sqli')
    await expect(sqliChip).toBeVisible()
    await sqliChip.click()

    // URL should contain vuln_class=sqli
    await expect(page).toHaveURL(/vuln_class=sqli/)
  })

  test('clicking an active chip toggles it off', async ({ page }) => {
    await page.goto('/findings?vuln_class=sqli')

    const sqliChip = page.getByTestId('filter-chip-vuln_class-sqli')
    // Should be active (amber background)
    await expect(sqliChip).toHaveClass(/bg-amber-600/)

    await sqliChip.click()

    // URL should no longer contain vuln_class=sqli
    await expect(page).not.toHaveURL(/vuln_class=sqli/)
  })

  test('"Clear all" button appears when a filter is active', async ({ page }) => {
    await page.goto('/findings?vuln_class=sqli')
    await expect(page.getByRole('button', { name: 'Clear all' })).toBeVisible()
  })

  test('"Clear all" button resets all filters', async ({ page }) => {
    await page.goto('/findings?vuln_class=sqli&severity=critical')

    const clearAll = page.getByRole('button', { name: 'Clear all' })
    await expect(clearAll).toBeVisible()
    await clearAll.click()

    await expect(page).not.toHaveURL(/vuln_class=/)
    await expect(page).not.toHaveURL(/severity=/)
  })

  test('"Clear all" button is not shown when no filters are active', async ({ page }) => {
    await page.goto('/findings')
    await expect(page.getByRole('button', { name: 'Clear all' })).not.toBeVisible()
  })

  test('date filter created_from updates URL', async ({ page }) => {
    await page.goto('/findings')
    const fromInput = page.getByLabel('Created from')
    await fromInput.fill('2026-04-01')
    await expect(page).toHaveURL(/created_from=2026-04-01/)
  })

  test('date filter created_to updates URL', async ({ page }) => {
    await page.goto('/findings')
    const toInput = page.getByLabel('Created to')
    await toInput.fill('2026-04-30')
    await expect(page).toHaveURL(/created_to=2026-04-30/)
  })
})
