/**
 * e2e spec: findings-back-forward
 *
 * Verifies that browser Back and Forward navigation restores URL-driven filter
 * state on the global Findings page. All filter state lives in URL search
 * params; each filter change pushes a new history entry (no `replace: true`),
 * so Back/Forward must re-trigger the data fetch with the restored params.
 *
 * Selected-state mechanism: filter chips use a class-based indicator.
 * An active chip receives the `bg-amber-600` Tailwind class; an inactive chip
 * does NOT have that class.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

/**
 * Returns true when the chip button for `facetKey`/`value` is currently in
 * the "selected" (active) state — i.e. has the amber background class that
 * FindingsFilterBar applies to active chips.
 */
async function isChipSelected(
  page: import('@playwright/test').Page,
  facetKey: string,
  value: string,
): Promise<boolean> {
  const chip = page.getByTestId(`filter-chip-${facetKey}-${value}`)
  await chip.waitFor({ state: 'visible' })
  const cls = (await chip.getAttribute('class')) ?? ''
  return cls.includes('bg-amber-600')
}

// ---------------------------------------------------------------------------
// Test 1: Browser Back restores prior filter state
// ---------------------------------------------------------------------------

test('browser Back restores the unfiltered state after a filter was applied', async ({ page }) => {
  await mockApi(page)

  const requestUrls: string[] = []
  page.on('request', (req) => {
    if (req.method() === 'GET' && req.url().includes('/api/findings')) {
      requestUrls.push(req.url())
    }
  })

  // 1. Load /findings with no filters
  await page.goto('/findings')
  // Wait for initial load
  await expect(page.getByRole('heading', { name: 'Findings' })).toBeVisible()
  const countAfterLoad = requestUrls.length
  expect(countAfterLoad).toBeGreaterThan(0)
  // Initial GET must NOT contain severity param
  expect(requestUrls.at(-1)).not.toContain('severity=')

  // 2. Click the severity=critical chip — pushes new history entry
  await page.getByTestId('filter-chip-severity-critical').click()
  // Wait for the filtered request
  await expect.poll(() => requestUrls.length).toBeGreaterThan(countAfterLoad)
  expect(requestUrls.at(-1)).toContain('severity=critical')
  expect(await isChipSelected(page, 'severity', 'critical')).toBe(true)
  expect(page.url()).toContain('severity=critical')

  // 3. Go back
  const countBeforeBack = requestUrls.length
  await page.goBack()

  // Wait for the un-filtered request to fire
  await expect.poll(() => requestUrls.length).toBeGreaterThan(countBeforeBack)
  expect(requestUrls.at(-1)).not.toContain('severity=')

  // URL must no longer have severity param
  expect(page.url()).not.toContain('severity=')

  // Chip must be in UNselected state
  expect(await isChipSelected(page, 'severity', 'critical')).toBe(false)
})

// ---------------------------------------------------------------------------
// Test 2: Browser Forward re-applies the next filter state
// ---------------------------------------------------------------------------

test('browser Forward re-applies the filtered state that was navigated away from', async ({ page }) => {
  await mockApi(page)

  const requestUrls: string[] = []
  page.on('request', (req) => {
    if (req.method() === 'GET' && req.url().includes('/api/findings')) {
      requestUrls.push(req.url())
    }
  })

  // Set up history: no-filter → with-filter → back to no-filter
  await page.goto('/findings')
  await expect(page.getByRole('heading', { name: 'Findings' })).toBeVisible()

  await page.getByTestId('filter-chip-severity-critical').click()
  await expect.poll(() => requestUrls.some((u) => u.includes('severity=critical'))).toBe(true)

  const countBeforeBack = requestUrls.length
  await page.goBack()
  await expect.poll(() => requestUrls.length).toBeGreaterThan(countBeforeBack)

  // Now go forward — should restore severity=critical
  const countBeforeForward = requestUrls.length
  await page.goForward()
  await expect.poll(() => requestUrls.length).toBeGreaterThan(countBeforeForward)

  expect(requestUrls.at(-1)).toContain('severity=critical')
  expect(page.url()).toContain('severity=critical')
  expect(await isChipSelected(page, 'severity', 'critical')).toBe(true)
})

// ---------------------------------------------------------------------------
// Test 3: Two-step back across two sequential filter changes
// ---------------------------------------------------------------------------

test('two consecutive Back presses each restore the correct prior filter state', async ({ page }) => {
  await mockApi(page)

  const requestUrls: string[] = []
  page.on('request', (req) => {
    if (req.method() === 'GET' && req.url().includes('/api/findings')) {
      requestUrls.push(req.url())
    }
  })

  // Step A: Load with no filters
  await page.goto('/findings')
  await expect(page.getByRole('heading', { name: 'Findings' })).toBeVisible()
  const countA = requestUrls.length
  expect(countA).toBeGreaterThan(0)

  // Step B: Apply severity=critical → URL has ?severity=critical
  await page.getByTestId('filter-chip-severity-critical').click()
  await expect.poll(() => requestUrls.length).toBeGreaterThan(countA)
  expect(requestUrls.at(-1)).toContain('severity=critical')
  expect(page.url()).toContain('severity=critical')

  // Step C: Apply vuln_class=sqli → URL has both severity=critical&vuln_class=sqli
  const countB = requestUrls.length
  await page.getByTestId('filter-chip-vuln_class-sqli').click()
  await expect.poll(() => requestUrls.length).toBeGreaterThan(countB)
  const bothUrl = requestUrls.at(-1)!
  expect(bothUrl).toContain('severity=critical')
  expect(bothUrl).toContain('vuln_class=sqli')
  expect(page.url()).toContain('severity=critical')
  expect(page.url()).toContain('vuln_class=sqli')

  // Step D: Back once → only severity=critical remains (no vuln_class)
  const countC = requestUrls.length
  await page.goBack()
  await expect.poll(() => requestUrls.length).toBeGreaterThan(countC)
  const afterFirstBack = requestUrls.at(-1)!
  expect(afterFirstBack).toContain('severity=critical')
  expect(afterFirstBack).not.toContain('vuln_class=')
  expect(page.url()).toContain('severity=critical')
  expect(page.url()).not.toContain('vuln_class=')

  // Chips: severity still selected, sqli now unselected
  expect(await isChipSelected(page, 'severity', 'critical')).toBe(true)
  expect(await isChipSelected(page, 'vuln_class', 'sqli')).toBe(false)

  // Step E: Back again → no filters
  const countD = requestUrls.length
  await page.goBack()
  await expect.poll(() => requestUrls.length).toBeGreaterThan(countD)
  const afterSecondBack = requestUrls.at(-1)!
  expect(afterSecondBack).not.toContain('severity=')
  expect(afterSecondBack).not.toContain('vuln_class=')
  expect(page.url()).not.toContain('severity=')
  expect(page.url()).not.toContain('vuln_class=')

  // Both chips now unselected
  expect(await isChipSelected(page, 'severity', 'critical')).toBe(false)
  expect(await isChipSelected(page, 'vuln_class', 'sqli')).toBe(false)
})

// ---------------------------------------------------------------------------
// Test 4: Deep-linked URL renders the filtered state immediately on load
// ---------------------------------------------------------------------------

test('deep-linked URL with multiple filters renders chips as selected on mount', async ({ page }) => {
  await mockApi(page)

  const requestUrls: string[] = []
  page.on('request', (req) => {
    if (req.method() === 'GET' && req.url().includes('/api/findings')) {
      requestUrls.push(req.url())
    }
  })

  // Navigate directly to /findings with two filters already set
  await page.goto('/findings?severity=critical&vuln_class=sqli')
  await expect(page.getByRole('heading', { name: 'Findings' })).toBeVisible()

  // Wait for the initial GET to fire
  await expect.poll(() => requestUrls.length).toBeGreaterThan(0)

  // Initial request must contain BOTH filter params
  const initUrl = requestUrls.at(-1)!
  expect(initUrl).toContain('severity=critical')
  expect(initUrl).toContain('vuln_class=sqli')

  // Both chips must render as SELECTED immediately (URL-driven state on mount)
  expect(await isChipSelected(page, 'severity', 'critical')).toBe(true)
  expect(await isChipSelected(page, 'vuln_class', 'sqli')).toBe(true)
})

// ---------------------------------------------------------------------------
// Test 5: Back to the deep-link works after navigating away and back
// ---------------------------------------------------------------------------

test('browser Back returns to a deep-linked filtered view after navigating away', async ({ page }) => {
  await mockApi(page)

  const requestUrls: string[] = []
  page.on('request', (req) => {
    if (req.method() === 'GET' && req.url().includes('/api/findings')) {
      requestUrls.push(req.url())
    }
  })

  // Start at a deep-linked filtered URL
  await page.goto('/findings?severity=critical')
  await expect(page.getByRole('heading', { name: 'Findings' })).toBeVisible()

  // Wait for the initial filtered request
  await expect.poll(() => requestUrls.some((u) => u.includes('severity=critical'))).toBe(true)
  expect(await isChipSelected(page, 'severity', 'critical')).toBe(true)

  // Navigate away to the home/dashboard page
  await page.goto('/')
  // Verify we left the findings page
  await expect(page).not.toHaveURL(/\/findings/)

  // Go back — must return to /findings?severity=critical
  const countBeforeBack = requestUrls.length
  await page.goBack()
  await expect(page).toHaveURL(/\/findings/)
  await expect(page).toHaveURL(/severity=critical/)

  // Wait for the re-fetch with the filter param
  await expect.poll(() => requestUrls.length).toBeGreaterThan(countBeforeBack)
  expect(requestUrls.at(-1)).toContain('severity=critical')

  // Chip must be selected again
  expect(await isChipSelected(page, 'severity', 'critical')).toBe(true)
})
