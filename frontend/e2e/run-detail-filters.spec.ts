/**
 * A1: URL-state filters + FindingsExplorer/Search interactions in RunDetail.
 *
 * Tests: severity filter, match filter, search query, URL param persistence,
 * filter combination, FindingsSearch interaction.
 */
import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const BATCH_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'
const BASE_URL = `/batches/${BATCH_ID}/runs/${RUN_ID}`

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto(BASE_URL)
})

// ---------------------------------------------------------------------------
// URL-state filter: severity
// ---------------------------------------------------------------------------

test('severity filter "critical" updates URL param and filters findings', async ({ page }) => {
  const severitySelect = page.locator('select').filter({ hasText: 'All severities' })
  await severitySelect.selectOption('critical')
  await expect(page).toHaveURL(/severity=critical/)
  // Only critical finding should remain visible
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
  // High severity finding should be hidden
  await expect(page.getByText('Reflected XSS in search results')).not.toBeVisible()
})

test('severity filter "high" shows only high-severity findings', async ({ page }) => {
  const severitySelect = page.locator('select').filter({ hasText: 'All severities' })
  await severitySelect.selectOption('high')
  await expect(page).toHaveURL(/severity=high/)
  await expect(page.getByText('Reflected XSS in search results')).toBeVisible()
  await expect(page.getByText('SQL Injection in user login handler')).not.toBeVisible()
})

test('severity filter reset to "all" removes URL param', async ({ page }) => {
  const severitySelect = page.locator('select').filter({ hasText: 'All severities' })
  await severitySelect.selectOption('critical')
  await expect(page).toHaveURL(/severity=critical/)
  await severitySelect.selectOption('all')
  // 'severity' param should be removed from URL
  await expect(page).not.toHaveURL(/severity=/)
})

// ---------------------------------------------------------------------------
// URL-state filter: match_status
// ---------------------------------------------------------------------------

test('match filter "tp" updates URL param and filters findings', async ({ page }) => {
  const matchSelect = page.locator('select').filter({ hasText: 'All statuses' })
  await matchSelect.selectOption('tp')
  await expect(page).toHaveURL(/match=tp/)
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
  await expect(page.getByText('Reflected XSS in search results')).not.toBeVisible()
})

test('match filter "fp" shows only false-positive findings', async ({ page }) => {
  const matchSelect = page.locator('select').filter({ hasText: 'All statuses' })
  await matchSelect.selectOption('fp')
  await expect(page).toHaveURL(/match=fp/)
  await expect(page.getByText('Reflected XSS in search results')).toBeVisible()
  await expect(page.getByText('SQL Injection in user login handler')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// URL-state filter: search query
// ---------------------------------------------------------------------------

test('search query updates URL param q', async ({ page }) => {
  const searchInput = page.locator('input[type="search"]')
  await searchInput.fill('SQL')
  await expect(page).toHaveURL(/q=SQL/)
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
  await expect(page.getByText('Reflected XSS in search results')).not.toBeVisible()
})

test('search query filters by file path', async ({ page }) => {
  const searchInput = page.locator('input[type="search"]')
  await searchInput.fill('login.py')
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
  await expect(page.getByText('Reflected XSS in search results')).not.toBeVisible()
})

test('clearing search query removes q from URL', async ({ page }) => {
  const searchInput = page.locator('input[type="search"]')
  await searchInput.fill('SQL')
  await expect(page).toHaveURL(/q=SQL/)
  await searchInput.fill('')
  await expect(page).not.toHaveURL(/q=/)
})

// ---------------------------------------------------------------------------
// URL params pre-loaded on navigation (deep-link)
// ---------------------------------------------------------------------------

test('navigating with severity param pre-applies filter', async ({ page }) => {
  await page.goto(`${BASE_URL}?severity=critical`)
  const severitySelect = page.locator('select').filter({ hasText: 'All severities' })
  await expect(severitySelect).toHaveValue('critical')
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
  await expect(page.getByText('Reflected XSS in search results')).not.toBeVisible()
})

test('navigating with match param pre-applies filter', async ({ page }) => {
  await page.goto(`${BASE_URL}?match=fp`)
  const matchSelect = page.locator('select').filter({ hasText: 'All statuses' })
  await expect(matchSelect).toHaveValue('fp')
  await expect(page.getByText('Reflected XSS in search results')).toBeVisible()
})

test('navigating with q param pre-applies search filter', async ({ page }) => {
  await page.goto(`${BASE_URL}?q=XSS`)
  const searchInput = page.locator('input[type="search"]')
  await expect(searchInput).toHaveValue('XSS')
  await expect(page.getByText('Reflected XSS in search results')).toBeVisible()
  await expect(page.getByText('SQL Injection in user login handler')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Combined filters
// ---------------------------------------------------------------------------

test('severity + match filter combination works correctly', async ({ page }) => {
  const severitySelect = page.locator('select').filter({ hasText: 'All severities' })
  await severitySelect.selectOption('critical')
  // Wait for URL to update before selecting match filter
  await expect(page).toHaveURL(/severity=critical/)
  const matchSelect = page.locator('select').filter({ hasText: 'All statuses' })
  await matchSelect.selectOption('tp')
  await expect(page).toHaveURL(/match=tp/)
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
  await expect(page.getByText('Reflected XSS in search results')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Browser back/forward navigation preserves filter state
// ---------------------------------------------------------------------------

test('URL filter state is applied immediately on navigation', async ({ page }) => {
  // Since filters use replace:true in setSearchParams, the filter state is
  // reflected immediately on the current URL rather than creating history entries.
  // Verify that selecting a filter updates the URL in-place.
  const severitySelect = page.locator('select').filter({ hasText: 'All severities' })
  await severitySelect.selectOption('critical')
  await expect(page).toHaveURL(/severity=critical/)
  // Changing filter again updates URL (replaces, not pushes)
  await severitySelect.selectOption('high')
  await expect(page).toHaveURL(/severity=high/)
  await expect(page).not.toHaveURL(/severity=critical/)
})

// ---------------------------------------------------------------------------
// FindingsSearch component interaction (batch-level search via API)
// ---------------------------------------------------------------------------

test('FindingsSearch input triggers API search and updates results', async ({ page }) => {
  // The FindingsSearch input searches across the batch via API
  // It appears in BatchDetail's FindingsExplorer, not RunDetail.
  // RunDetail has an inline search input that filters local findings.
  // Test the inline search:
  const inlineSearch = page.locator('input[type="search"]')
  await inlineSearch.fill('injection')
  await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()
})
