/**
 * RunDetail interaction tests — interactions not covered by run-detail.spec.ts
 * or run-detail-filters.spec.ts.
 *
 * Covers:
 *  1. Severity filter select change (critical) — visible row shifts
 *  2. Match-status filter select change (fp) — FP rows visible
 *  3. Findings search input — POST/GET /api/experiments/{id}/findings/search fires
 *  4. Pagination Previous/Next button states
 *  5. Prompt Snapshot collapsible toggle (expand / collapse)
 *  6. Conversation Transcript collapsible toggle (expand / collapse)
 *  7. Finding row click expands detail; FP finding shows Reclassify button → POST reclassify
 *  8. Tool call row click expands JSON viewer
 *  9. DownloadButton present with label "Download Run"
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

// ---------------------------------------------------------------------------
// Constants derived from fixture files
// ---------------------------------------------------------------------------

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'
const BASE_URL = `/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`

// The fixture has exactly 2 findings (< PAGE_SIZE=25), so pagination is hidden
// unless we override. We will use a custom large-findings fixture for pagination tests.

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Return a run-full fixture body whose findings array is padded to `count` entries. */
function padFindings(count: number): object {
  const __filename = fileURLToPath(import.meta.url)
  const __dirname = dirname(__filename)
  const base = JSON.parse(
    readFileSync(join(__dirname, 'fixtures/run-full.json'), 'utf-8')
  ) as { findings: object[]; [key: string]: unknown }

  const seed = base.findings[0] as Record<string, unknown>
  const findings = Array.from({ length: count }, (_, i) => ({
    ...seed,
    finding_id: `find-pad-${i}`,
    title: `Padded Finding ${i}`,
    severity: 'high',
    match_status: 'tp',
  }))

  return { ...base, findings }
}

// ---------------------------------------------------------------------------
// beforeEach
// ---------------------------------------------------------------------------

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto(BASE_URL)
})

// ---------------------------------------------------------------------------
// 1. Severity filter select — change to "critical"
// ---------------------------------------------------------------------------

test('severity filter "critical" — only critical-severity finding remains visible', async ({ page }) => {
  const severitySelect = page.locator('select').filter({ hasText: 'All severities' })
  await severitySelect.selectOption('critical')

  // SQL Injection is severity=critical
  await expect(page.getByRole('cell', { name: 'SQL Injection in user login handler' })).toBeVisible()
  // Reflected XSS is severity=high — must disappear
  await expect(page.getByRole('cell', { name: 'Reflected XSS in search results' })).not.toBeVisible()
})

test('severity filter "critical" — count badge reflects filtered set', async ({ page }) => {
  const severitySelect = page.locator('select').filter({ hasText: 'All severities' })
  await severitySelect.selectOption('critical')

  // The span shows "N of M" where N=1
  await expect(page.getByText(/^1 of 2$/)).toBeVisible()
})

// ---------------------------------------------------------------------------
// 2. Match-status filter — change to "fp"
// ---------------------------------------------------------------------------

test('match filter "fp" — only FP finding is visible', async ({ page }) => {
  const matchSelect = page.locator('select').filter({ hasText: 'All statuses' })
  await matchSelect.selectOption('fp')

  // Reflected XSS is fp
  await expect(page.getByRole('cell', { name: 'Reflected XSS in search results' })).toBeVisible()
  // SQL Injection is tp — must disappear
  await expect(page.getByRole('cell', { name: 'SQL Injection in user login handler' })).not.toBeVisible()
})

test('match filter "fp" — URL param updated', async ({ page }) => {
  const matchSelect = page.locator('select').filter({ hasText: 'All statuses' })
  await matchSelect.selectOption('fp')
  await expect(page).toHaveURL(/match=fp/)
})

// ---------------------------------------------------------------------------
// 3. Findings search input — assert API request fires with query=sql
// ---------------------------------------------------------------------------

// NOTE: RunDetail's inline search input (type="search") filters locally — it does NOT
// call the /findings/search endpoint. That endpoint is called by FindingsSearch inside
// FindingsExplorer on the ExperimentDetail page. There is no FindingsSearch component on
// RunDetail, so we assert the inline filter behaviour instead and skip the API-request
// assertion for this page.
// TODO: If a FindingsSearch component is added to RunDetail in future, replace this test
//       with a waitForRequest assertion against /api/experiments/{id}/findings/search?q=sql.

test('search input "sql" — matching finding remains visible (inline client-side filter)', async ({ page }) => {
  const searchInput = page.locator('input[type="search"]')
  await searchInput.fill('sql')

  await expect(page.getByRole('cell', { name: 'SQL Injection in user login handler' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Reflected XSS in search results' })).not.toBeVisible()
})

test.skip('findings search fires GET /api/experiments/{id}/findings/search?q=sql — NOT on RunDetail', async () => {
  // TODO: RunDetail does not mount FindingsSearch; the /findings/search endpoint is only
  //       called from FindingsExplorer (ExperimentDetail). Add this test there once the
  //       experiment-detail-interactions spec is written.
})

// ---------------------------------------------------------------------------
// 4. Pagination Previous / Next
// ---------------------------------------------------------------------------

test('pagination — Previous button disabled on page 1 when fixture has < 25 findings', async ({ page }) => {
  // Fixture has 2 findings (< PAGE_SIZE=25), so the pagination row is not rendered at all.
  // Verify there is no "Previous" button visible (i.e. pagination is absent).
  await expect(page.getByRole('button', { name: 'Previous' })).not.toBeVisible()
  await expect(page.getByRole('button', { name: 'Next' })).not.toBeVisible()
})

test('pagination — Previous disabled on page 1 and Next enabled with >25 findings', async ({ page }) => {
  // Override the run endpoint to return 30 padded findings so pagination renders.
  await page.route(`**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`, (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(padFindings(30)),
    })
  })

  await page.goto(BASE_URL)

  const prevBtn = page.getByRole('button', { name: 'Previous' })
  const nextBtn = page.getByRole('button', { name: 'Next' })

  await expect(prevBtn).toBeVisible()
  await expect(nextBtn).toBeVisible()

  // Page 1 — Previous should be disabled
  await expect(prevBtn).toBeDisabled()
  // Next should be enabled
  await expect(nextBtn).toBeEnabled()
})

test('pagination — clicking Next enables Previous', async ({ page }) => {
  await page.route(`**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`, (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(padFindings(30)),
    })
  })

  await page.goto(BASE_URL)

  const prevBtn = page.getByRole('button', { name: 'Previous' })
  const nextBtn = page.getByRole('button', { name: 'Next' })

  // Ensure Next is clickable before clicking
  await expect(nextBtn).toBeEnabled()
  await nextBtn.click()

  // Now on page 2 — Previous must become enabled
  await expect(prevBtn).toBeEnabled()
  // And Next should be disabled (only 30 findings → 2 pages; page 2 is last)
  await expect(nextBtn).toBeDisabled()
})

// ---------------------------------------------------------------------------
// 5. Prompt Snapshot collapsible — expand then collapse
// ---------------------------------------------------------------------------

test('Prompt Snapshot collapsible — expands to show diff panes on click', async ({ page }) => {
  const toggle = page.getByRole('button', { name: 'Prompt Snapshot' })

  // Initially collapsed — diff panes must be absent
  await expect(page.getByTestId('clean-prompt-pane')).not.toBeVisible()
  await expect(page.getByTestId('injected-prompt-pane')).not.toBeVisible()

  await toggle.click()

  // Fixture has both clean_prompt and injected_prompt → DiffPane rendered
  await expect(page.getByTestId('clean-prompt-pane')).toBeVisible()
  await expect(page.getByTestId('injected-prompt-pane')).toBeVisible()
})

test('Prompt Snapshot collapsible — second click collapses content', async ({ page }) => {
  const toggle = page.getByRole('button', { name: 'Prompt Snapshot' })

  await toggle.click()
  await expect(page.getByTestId('clean-prompt-pane')).toBeVisible()

  await toggle.click()
  await expect(page.getByTestId('clean-prompt-pane')).not.toBeVisible()
})

test('Prompt Snapshot collapsible — chevron toggles direction', async ({ page }) => {
  const toggle = page.getByRole('button', { name: 'Prompt Snapshot' })

  // Collapsed state shows down-arrow
  await expect(toggle).toContainText('▼')

  await toggle.click()
  // Expanded state shows up-arrow
  await expect(toggle).toContainText('▲')

  await toggle.click()
  await expect(toggle).toContainText('▼')
})

// ---------------------------------------------------------------------------
// 6. Conversation Transcript collapsible — expand then collapse
// ---------------------------------------------------------------------------

test('Conversation Transcript collapsible — expands to show messages on click', async ({ page }) => {
  const toggle = page.getByRole('button', { name: /Conversation Transcript/ })

  // Initially collapsed
  await expect(page.getByText('Analyze the codebase for security vulnerabilities.')).not.toBeVisible()

  await toggle.click()

  // Fixture message from run-full.json
  await expect(page.getByText('Analyze the codebase for security vulnerabilities.')).toBeVisible()
})

test('Conversation Transcript collapsible — second click collapses messages', async ({ page }) => {
  const toggle = page.getByRole('button', { name: /Conversation Transcript/ })

  await toggle.click()
  await expect(page.getByText('Analyze the codebase for security vulnerabilities.')).toBeVisible()

  await toggle.click()
  await expect(page.getByText('Analyze the codebase for security vulnerabilities.')).not.toBeVisible()
})

test('Conversation Transcript collapsible — shows assistant message after expand', async ({ page }) => {
  const toggle = page.getByRole('button', { name: /Conversation Transcript/ })
  await toggle.click()

  await expect(
    page.getByText("I'll systematically review the codebase for security issues.")
  ).toBeVisible()
})

// ---------------------------------------------------------------------------
// 7. Finding row click — expand detail; FP finding shows Reclassify button → POST
// ---------------------------------------------------------------------------

test('clicking a finding row expands its description panel', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'SQL Injection in user login handler' }).first()
  await row.click()

  // Description text from run-full.json
  await expect(
    page.getByText('User-supplied input is concatenated directly into a SQL query without parameterization.')
  ).toBeVisible()
})

test('clicking an expanded finding row a second time collapses the detail', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'SQL Injection in user login handler' }).first()
  await row.click()
  await expect(
    page.getByText('User-supplied input is concatenated directly into a SQL query without parameterization.')
  ).toBeVisible()

  await row.click()
  await expect(
    page.getByText('User-supplied input is concatenated directly into a SQL query without parameterization.')
  ).not.toBeVisible()
})

test('FP finding expanded detail shows "Reclassify as Unlabeled Real" button', async ({ page }) => {
  // Reflected XSS has match_status=fp
  const row = page.locator('tbody tr').filter({ hasText: 'Reflected XSS in search results' }).first()
  await row.click()

  await expect(page.getByRole('button', { name: 'Reclassify as Unlabeled Real' })).toBeVisible()
})

test('TP finding expanded detail does NOT show Reclassify button', async ({ page }) => {
  // SQL Injection has match_status=tp
  const row = page.locator('tbody tr').filter({ hasText: 'SQL Injection in user login handler' }).first()
  await row.click()

  await expect(page.getByRole('button', { name: 'Reclassify as Unlabeled Real' })).not.toBeVisible()
})

test('clicking Reclassify as Unlabeled Real posts to /reclassify endpoint', async ({ page }) => {
  const reclassifyRequestPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/reclassify') &&
      req.method() === 'POST'
  )

  const row = page.locator('tbody tr').filter({ hasText: 'Reflected XSS in search results' }).first()
  await row.click()

  const reclassifyBtn = page.getByRole('button', { name: 'Reclassify as Unlabeled Real' })
  await expect(reclassifyBtn).toBeVisible()
  await reclassifyBtn.click()

  // Wait for the POST request — mock returns 204 so no UI crash expected
  const req = await reclassifyRequestPromise
  expect(req.url()).toMatch(new RegExp(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}/reclassify`))
  expect(req.method()).toBe('POST')
})

test('Reclassify POST body includes correct finding_id and new classification', async ({ page }) => {
  const reclassifyRequestPromise = page.waitForRequest(
    (req) => req.url().includes('/reclassify') && req.method() === 'POST'
  )

  const row = page.locator('tbody tr').filter({ hasText: 'Reflected XSS in search results' }).first()
  await row.click()
  await page.getByRole('button', { name: 'Reclassify as Unlabeled Real' }).click()

  const req = await reclassifyRequestPromise
  const body = req.postDataJSON() as Record<string, unknown>
  expect(body.finding_id).toBe('find-002')
  expect(body.new_status).toBe('unlabeled_real')
})

// ---------------------------------------------------------------------------
// 8. Tool call row click — expands JSON viewer
// ---------------------------------------------------------------------------

test('tool call row click — expands the JSON input viewer', async ({ page }) => {
  // The first tool call is read_file — its input truncated in the summary row
  const toolRow = page.locator('table').last().locator('tbody tr').first()
  await toolRow.click()

  // The expanded CodeViewer renders the full JSON; look for the key that is
  // present in the input object: {"path":"src/auth/login.py"}
  await expect(page.getByText(/src\/auth\/login\.py/).last()).toBeVisible()
})

test('tool call row click — second click collapses the expanded viewer', async ({ page }) => {
  const toolTable = page.locator('table').last()

  await expect(toolTable.locator('tbody tr').filter({ hasText: 'search_code' })).toBeVisible()
  const initialRowCount = await toolTable.locator('tbody tr').count()

  const toolRow = toolTable.locator('tbody tr').first()
  await toolRow.click()
  await expect(toolTable.locator('tbody tr')).toHaveCount(initialRowCount + 1)

  await toolRow.click()
  await expect(toolTable.locator('tbody tr')).toHaveCount(initialRowCount)
})

test('tool call row click — second tool call (search_code) expands correctly', async ({ page }) => {
  const toolTable = page.locator('table').last()
  // Second tool call row is search_code
  const searchCodeRow = toolTable.locator('tbody tr').filter({ hasText: 'search_code' }).first()
  await searchCodeRow.click()

  // Input has {"query":"SELECT","path":"src/"}
  await expect(page.getByText(/SELECT/).last()).toBeVisible()
})

// ---------------------------------------------------------------------------
// 9. DownloadButton — visible with label "Download Run"
// ---------------------------------------------------------------------------

test('Download Run button is visible in the run header', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Download Run' })).toBeVisible()
})

test('Download Run button is only one instance on the page', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Download Run' })).toHaveCount(1)
})
