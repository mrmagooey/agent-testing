/**
 * FindingsExplorer on ExperimentDetail — e2e tests
 *
 * Covers: filter dropdowns (match status, vuln class, severity), live counter,
 * empty-state message, row expand/collapse, single-row expansion invariant,
 * "View in dataset" link href and stopPropagation behaviour.
 *
 * Component under test: FindingsExplorer.tsx, mounted at ExperimentDetail line 346.
 * NOT RunDetail — RunDetail has its own inline filter UI.
 *
 * Fixture facts:
 *  find-001  SQL Injection in user login handler  vuln_class=sqli  severity=critical  match_status=tp  run_id=run-001-aaa  matched_label_id=label-001
 *  find-002  Reflected XSS in search results      vuln_class=xss   severity=high      match_status=fp  run_id=run-001-aaa  matched_label_id=null
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const PAGE_URL = `/experiments/${EXPERIMENT_ID}`

const SQL_TITLE = 'SQL Injection in user login handler'
const XSS_TITLE = 'Reflected XSS in search results'

test.describe('FindingsExplorer on ExperimentDetail', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto(PAGE_URL)
    await page.getByRole('heading', { name: 'Findings' }).waitFor()
  })

  // ---------------------------------------------------------------------------
  // 1. Default render
  // ---------------------------------------------------------------------------

  test('default render: 2 findings visible with "2 findings" counter and default filter options', async ({ page }) => {
    await expect(page.getByRole('row').filter({ hasText: SQL_TITLE })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: XSS_TITLE })).toBeVisible()

    await expect(page.locator('span').filter({ hasText: /^2 findings$/ })).toBeVisible()

    await expect(page.locator('select').filter({ hasText: 'All statuses' })).toBeVisible()
    await expect(page.locator('select').filter({ hasText: 'All vuln classes' })).toBeVisible()
    await expect(page.locator('select').filter({ hasText: 'All severities' })).toBeVisible()
  })

  // ---------------------------------------------------------------------------
  // 2. Filter by match status: fp hides the tp row
  // ---------------------------------------------------------------------------

  test('filter by match status "fp" hides TP row and shows "1 findings"', async ({ page }) => {
    await page.locator('select').filter({ hasText: 'All statuses' }).selectOption('fp')

    await expect(page.getByRole('row').filter({ hasText: XSS_TITLE })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: SQL_TITLE })).toHaveCount(0)

    await expect(page.locator('span').filter({ hasText: /^1 findings$/ })).toBeVisible()
  })

  // ---------------------------------------------------------------------------
  // 3. Filter by severity: critical hides high-severity row
  // ---------------------------------------------------------------------------

  test('filter by severity "critical" hides high-severity row and shows "1 findings"', async ({ page }) => {
    await page.locator('select').filter({ hasText: 'All severities' }).selectOption('critical')

    await expect(page.getByRole('row').filter({ hasText: SQL_TITLE })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: XSS_TITLE })).toHaveCount(0)

    await expect(page.locator('span').filter({ hasText: /^1 findings$/ })).toBeVisible()
  })

  // ---------------------------------------------------------------------------
  // 4. Filter combination: vuln_class=sqli + match=tp
  // ---------------------------------------------------------------------------

  test('filter combination sqli + tp shows only find-001 with "1 findings"', async ({ page }) => {
    await page.locator('select').filter({ hasText: 'All vuln classes' }).selectOption('sqli')
    await page.locator('select').filter({ hasText: 'All statuses' }).selectOption('tp')

    await expect(page.getByRole('row').filter({ hasText: SQL_TITLE })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: XSS_TITLE })).toHaveCount(0)

    await expect(page.locator('span').filter({ hasText: /^1 findings$/ })).toBeVisible()
  })

  // ---------------------------------------------------------------------------
  // 5. Empty state when no findings match
  // ---------------------------------------------------------------------------

  test('empty-state message and "0 findings" when filters exclude all findings', async ({ page }) => {
    await page.locator('select').filter({ hasText: 'All severities' }).selectOption('low')

    await expect(page.getByText('No findings match the current filters.')).toBeVisible()
    await expect(page.locator('span').filter({ hasText: /^0 findings$/ })).toBeVisible()

    await expect(page.getByRole('row').filter({ hasText: SQL_TITLE })).toHaveCount(0)
    await expect(page.getByRole('row').filter({ hasText: XSS_TITLE })).toHaveCount(0)
  })

  // ---------------------------------------------------------------------------
  // 6. Vuln class dropdown options derived from findings
  // ---------------------------------------------------------------------------

  test('vuln class dropdown lists sqli and xss from fixture but not ssrf', async ({ page }) => {
    const vulnSelect = page.locator('select').filter({ hasText: 'All vuln classes' })

    await expect(vulnSelect.locator('option[value="sqli"]')).toHaveCount(1)
    await expect(vulnSelect.locator('option[value="xss"]')).toHaveCount(1)
    await expect(vulnSelect.locator('option[value="ssrf"]')).toHaveCount(0)
    await expect(vulnSelect.locator('option[value="all"]')).toHaveCount(1)
  })

  // ---------------------------------------------------------------------------
  // 7. Click row to expand: CodeMirror editor + "View in dataset" link
  // ---------------------------------------------------------------------------

  test('clicking find-001 row expands it and shows CodeMirror editor and "View in dataset" link with correct href', async ({ page }) => {
    await page.getByRole('row').filter({ hasText: SQL_TITLE }).click()

    const expandedCell = page.locator('td[colspan="6"]').first()
    await expect(expandedCell.locator('.cm-editor')).toBeVisible()

    const link = page.getByRole('link', { name: 'View in dataset' })
    await expect(link).toBeVisible()

    const href = await link.getAttribute('href')
    expect(href).toContain('/datasets/cve-2024-python/source?')
    expect(href).toContain('path=src%2Fauth%2Flogin.py')
    expect(href).toContain('line=42')
    expect(href).toContain('end=47')
    expect(href).toContain(`from_experiment=${EXPERIMENT_ID}`)
    expect(href).toContain('from_run=run-001-aaa')
  })

  // ---------------------------------------------------------------------------
  // 8. Click row again to collapse
  // ---------------------------------------------------------------------------

  test('clicking an expanded row again collapses it', async ({ page }) => {
    const row = page.getByRole('row').filter({ hasText: SQL_TITLE })

    await row.click()
    await expect(page.locator('.cm-editor')).toBeVisible()

    await row.click()
    await expect(page.locator('.cm-editor')).toHaveCount(0)
    await expect(page.getByRole('link', { name: 'View in dataset' })).toHaveCount(0)
  })

  // ---------------------------------------------------------------------------
  // 9. Single-row expansion: clicking a different row collapses the previous
  // ---------------------------------------------------------------------------

  test('expanding row B after row A collapses row A', async ({ page }) => {
    const rowA = page.getByRole('row').filter({ hasText: SQL_TITLE })
    const rowB = page.getByRole('row').filter({ hasText: XSS_TITLE })

    await rowA.click()
    await expect(page.locator('.cm-editor')).toBeVisible()

    await rowB.click()

    // Row B's expanded content must now be present — the Reclassify button is
    // specific to find-002 (fp) and confirms it is the active expanded row.
    await expect(page.getByRole('button', { name: /Reclassify as Unlabeled Real/i })).toBeVisible()

    // Row A's "View in dataset" link should still exist (find-002 also has a
    // file_path), but only ONE cm-editor must be mounted — one expanded row.
    await expect(page.locator('.cm-editor')).toHaveCount(1)
  })

  // ---------------------------------------------------------------------------
  // 10. "View in dataset" stopPropagation — clicking it does not collapse the row
  // ---------------------------------------------------------------------------

  test('"View in dataset" click does not collapse the expanded row (stopPropagation)', async ({ page }) => {
    await page.getByRole('row').filter({ hasText: SQL_TITLE }).click()
    await expect(page.locator('.cm-editor')).toBeVisible()

    // Install a capturing preventDefault listener so the link click does not
    // open a popup or navigate away, letting us observe the row state.
    await page.evaluate(() => {
      document.addEventListener('click', (e) => e.preventDefault(), { capture: true })
    })

    await page.getByRole('link', { name: 'View in dataset' }).click()

    // Row must remain expanded after the link click.
    await expect(page.locator('.cm-editor')).toBeVisible()
    await expect(page.getByRole('link', { name: 'View in dataset' })).toBeVisible()
  })
})
