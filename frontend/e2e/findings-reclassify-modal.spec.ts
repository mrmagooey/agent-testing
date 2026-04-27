/**
 * FindingsExplorer — reclassify modal e2e tests
 *
 * Covers the user story: a researcher drills into a FP finding on the
 * ExperimentDetail page, opens the reclassification modal, types a note,
 * and confirms — so the API receives the new label with the note.
 *
 * The component under test is FindingsExplorer (ExperimentDetail page),
 * NOT the inline reclassify path on RunDetail (covered by run-detail-interactions).
 *
 * Fixture facts used here:
 *  - find-001  SQL Injection in user login handler  match_status=tp  run_id=run-001-aaa
 *  - find-002  Reflected XSS in search results      match_status=fp  run_id=run-001-aaa
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'
const FINDING_ID_FP = 'find-002'
const FP_TITLE = 'Reflected XSS in search results'
const TP_TITLE = 'SQL Injection in user login handler'
const PAGE_URL = `/experiments/${EXPERIMENT_ID}`

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Locate the reclassify modal by structural anchor (no role="dialog" in the markup). */
function getModal(page: Parameters<typeof mockApi>[0]) {
  return page
    .locator('div')
    .filter({ hasText: 'Reclassify Finding' })
    .filter({ has: page.locator('textarea') })
    .first()
}

/** Expand the FP finding row and return the row locator. */
async function expandFpRow(page: Parameters<typeof mockApi>[0]) {
  const row = page.getByRole('row').filter({ hasText: FP_TITLE })
  await row.click()
  return row
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto(PAGE_URL)
  // Wait for the findings table to be rendered before each test
  await expect(page.getByRole('row').filter({ hasText: FP_TITLE })).toBeVisible()
})

// ---------------------------------------------------------------------------
// 1. FP finding row is visible in the findings table
// ---------------------------------------------------------------------------

test('findings table shows the FP finding row "Reflected XSS in search results"', async ({ page }) => {
  await expect(page.getByRole('row').filter({ hasText: FP_TITLE })).toBeVisible()
})

// ---------------------------------------------------------------------------
// 2. Clicking the row expands it; Reclassify button appears
// ---------------------------------------------------------------------------

test('clicking the FP row expands it and shows the Reclassify button', async ({ page }) => {
  await expandFpRow(page)
  await expect(
    page.getByRole('button', { name: /Reclassify as Unlabeled Real/i })
  ).toBeVisible()
})

// ---------------------------------------------------------------------------
// 3. Clicking Reclassify opens the modal with expected content
// ---------------------------------------------------------------------------

test('clicking Reclassify opens the modal with correct heading, description, textarea, and buttons', async ({ page }) => {
  await expandFpRow(page)
  await page.getByRole('button', { name: /Reclassify as Unlabeled Real/i }).click()

  const modal = getModal(page)

  // Heading
  await expect(modal.getByRole('heading', { name: 'Reclassify Finding' })).toBeVisible()

  // Paragraph mentioning unlabeled_real
  await expect(modal.getByText(/unlabeled_real/)).toBeVisible()

  // Textarea starts empty
  const textarea = modal.getByPlaceholder('Reason for reclassification...')
  await expect(textarea).toBeVisible()
  await expect(textarea).toHaveValue('')

  // Confirm is enabled by default
  await expect(modal.getByRole('button', { name: /Confirm/i })).toBeEnabled()

  // Cancel is visible
  await expect(modal.getByRole('button', { name: 'Cancel' })).toBeVisible()
})

// ---------------------------------------------------------------------------
// 4. Cancel closes the modal and no POST is sent
// ---------------------------------------------------------------------------

test('Cancel closes the modal without sending any POST to /reclassify', async ({ page }) => {
  // Register a capture handler AFTER mockApi to intercept any reclassify POST
  let postFired = false
  await page.route(`**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}/reclassify`, (route) => {
    if (route.request().method() === 'POST') {
      postFired = true
    }
    return route.fallback()
  })

  await expandFpRow(page)
  await page.getByRole('button', { name: /Reclassify as Unlabeled Real/i }).click()

  const modal = getModal(page)
  await expect(modal.getByRole('heading', { name: 'Reclassify Finding' })).toBeVisible()

  // Click Cancel
  await modal.getByRole('button', { name: 'Cancel' }).click()

  // Modal must be gone
  await expect(page.getByRole('heading', { name: 'Reclassify Finding' })).not.toBeVisible()

  // No POST should have been sent
  expect(postFired).toBe(false)
})

// ---------------------------------------------------------------------------
// 5. Typing in the textarea updates its value
// ---------------------------------------------------------------------------

test('typing a note in the textarea updates the textarea value', async ({ page }) => {
  const NOTE = 'User input is escaped via Jinja2 autoescape, this is not exploitable'

  await expandFpRow(page)
  await page.getByRole('button', { name: /Reclassify as Unlabeled Real/i }).click()

  const modal = getModal(page)
  const textarea = modal.getByPlaceholder('Reason for reclassification...')

  await textarea.fill(NOTE)
  await expect(textarea).toHaveValue(NOTE)
})

// ---------------------------------------------------------------------------
// 6. Happy path with note — POST is sent with correct body; modal closes
// ---------------------------------------------------------------------------

test('happy path: type note, click Confirm — POST body is correct and modal closes', async ({ page }) => {
  const NOTE = 'User input is escaped via Jinja2 autoescape, this is not exploitable'

  // Capture the POST body. Register AFTER mockApi so this handler wins (LIFO).
  let capturedBody: Record<string, unknown> | null = null
  await page.route(`**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}/reclassify`, async (route) => {
    if (route.request().method() === 'POST') {
      capturedBody = route.request().postDataJSON() as Record<string, unknown>
      return route.fulfill({ status: 204, body: '' })
    }
    return route.fallback()
  })

  await expandFpRow(page)
  await page.getByRole('button', { name: /Reclassify as Unlabeled Real/i }).click()

  const modal = getModal(page)
  const textarea = modal.getByPlaceholder('Reason for reclassification...')
  await textarea.fill(NOTE)

  await modal.getByRole('button', { name: /Confirm/i }).click()

  // Modal must close
  await expect(page.getByRole('heading', { name: 'Reclassify Finding' })).not.toBeVisible()

  // POST body assertions
  expect(capturedBody).not.toBeNull()
  expect(capturedBody!.finding_id).toBe(FINDING_ID_FP)
  expect(capturedBody!.new_status).toBe('unlabeled_real')
  expect(capturedBody!.note).toBe(NOTE)
})

// ---------------------------------------------------------------------------
// 7. In-flight state — Confirm button shows "Saving..." and is disabled
// ---------------------------------------------------------------------------

test('in-flight: Confirm button reads "Saving..." and is disabled while request is pending', async ({ page }) => {
  // Slow mock: delay the response by 1.5 s so we have a window to assert disabled state
  await page.route(`**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}/reclassify`, async (route) => {
    if (route.request().method() === 'POST') {
      await new Promise<void>((resolve) => setTimeout(resolve, 1500))
      return route.fulfill({ status: 204, body: '' })
    }
    return route.fallback()
  })

  await expandFpRow(page)
  await page.getByRole('button', { name: /Reclassify as Unlabeled Real/i }).click()

  const modal = getModal(page)

  // Click Confirm without waiting for the request to finish
  await modal.getByRole('button', { name: /Confirm/i }).click()

  // During the 1.5 s delay the button should flip to "Saving..." and become disabled
  const savingBtn = modal.getByRole('button', { name: 'Saving...' })
  await expect(savingBtn).toBeVisible()
  await expect(savingBtn).toBeDisabled()

  // Wait for it to resolve and the modal to close
  await expect(page.getByRole('heading', { name: 'Reclassify Finding' })).not.toBeVisible({ timeout: 5000 })
})

// ---------------------------------------------------------------------------
// 8. TP finding row does NOT show the Reclassify button
// ---------------------------------------------------------------------------

test('expanding a TP finding row does NOT show the Reclassify button', async ({ page }) => {
  const tpRow = page.getByRole('row').filter({ hasText: TP_TITLE })
  await tpRow.click()

  await expect(
    page.getByRole('button', { name: /Reclassify as Unlabeled Real/i })
  ).not.toBeVisible()
})
