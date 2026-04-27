/**
 * e2e spec: export-menu
 *
 * Covers the user story:
 *   As a security researcher with a completed experiment, I want to open the
 *   Download dropdown on the experiment detail page, choose "Export full bundle
 *   (.secrev.zip)", select between `descriptor` (recommended) and `reference`
 *   dataset modes in the dialog, and click Export — so the browser starts
 *   downloading a .secrev.zip file from the URL
 *   /api/experiments/<id>/export?dataset_mode=<mode>.
 *
 * Mocking strategy:
 *   mockApi() registers a catch-all GET /api/** handler. Routes are LIFO in
 *   Playwright, so per-test handlers registered after mockApi() take priority.
 *   Non-matching paths fall through via route.fallback().
 *
 *   The export action creates a temporary anchor and calls .click() on it
 *   programmatically — Playwright intercepts the resulting navigation via
 *   page.route(). We fulfill with a minimal JSON body to avoid an actual
 *   download event and capture the request URL to assert the dataset_mode
 *   query parameter.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Fixture IDs
// ---------------------------------------------------------------------------

/** Completed experiment — ExportMenu is rendered for terminal experiments only. */
const COMPLETED_ID = 'aaaaaaaa-0001-0001-0001-000000000001'

/** Running experiment — ExportMenu must NOT appear. */
const RUNNING_ID = 'bbbbbbbb-0002-0002-0002-000000000002'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Navigate to the detail page and wait for the experiment heading to render. */
async function gotoDetail(page: import('@playwright/test').Page, id: string) {
  await page.goto(`/experiments/${id}`)
  // Wait for a positive mount signal (the experiment ID is rendered as <h1>) so
  // negative assertions like "Download summary is absent" don't pass vacuously
  // while the page is still loading.
  await expect(page.getByRole('heading', { name: id })).toBeVisible()
}

/** Open the Download dropdown on the completed experiment detail page. */
async function openDownloadMenu(page: import('@playwright/test').Page) {
  const summary = page.locator('summary').filter({ hasText: 'Download' })
  await summary.click()
}

/** Open the Download dropdown then click "Export full bundle (.secrev.zip)". */
async function openBundleDialog(page: import('@playwright/test').Page) {
  await openDownloadMenu(page)
  await page.getByTestId('export-bundle-btn').click()
  // Wait for the dialog to be present
  await expect(page.getByRole('dialog')).toBeVisible()
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// Test 1: Download summary is visible on the completed experiment detail page
// ---------------------------------------------------------------------------

test('Download dropdown summary is visible on a completed experiment detail page', async ({
  page,
}) => {
  await gotoDetail(page, COMPLETED_ID)

  const summary = page.locator('summary').filter({ hasText: 'Download' })
  await expect(summary).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 2: Clicking the summary opens the dropdown and reveals both menu items
// ---------------------------------------------------------------------------

test('clicking the Download summary opens the dropdown with both menu items', async ({ page }) => {
  await gotoDetail(page, COMPLETED_ID)

  // Before clicking, the dropdown items should not be visible
  await expect(page.getByText('Download reports (.zip)')).not.toBeVisible()
  await expect(page.getByTestId('export-bundle-btn')).not.toBeVisible()

  await openDownloadMenu(page)

  // After clicking, both items should be visible
  await expect(page.getByText('Download reports (.zip)')).toBeVisible()
  await expect(page.getByTestId('export-bundle-btn')).toBeVisible()
  await expect(page.getByTestId('export-bundle-btn')).toContainText('Export full bundle (.secrev.zip)')
})

// ---------------------------------------------------------------------------
// Test 3: Clicking "Export full bundle" closes the dropdown and opens the dialog
// ---------------------------------------------------------------------------

test('clicking "Export full bundle" closes the dropdown and opens the Export Full Bundle dialog', async ({
  page,
}) => {
  await gotoDetail(page, COMPLETED_ID)
  await openDownloadMenu(page)

  // The dropdown is open
  await expect(page.getByTestId('export-bundle-btn')).toBeVisible()

  await page.getByTestId('export-bundle-btn').click()

  // The dialog should now be open
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Export Full Bundle' })).toBeVisible()

  // The dropdown should be closed (bundle btn no longer visible)
  await expect(page.getByTestId('export-bundle-btn')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 4: Dialog has both radios; descriptor is checked by default
// ---------------------------------------------------------------------------

test('dialog shows both dataset-mode radios with descriptor checked by default', async ({
  page,
}) => {
  await gotoDetail(page, COMPLETED_ID)
  await openBundleDialog(page)

  const descriptorRadio = page.getByTestId('dataset-mode-radio-descriptor')
  const referenceRadio = page.getByTestId('dataset-mode-radio-reference')

  await expect(descriptorRadio).toBeVisible()
  await expect(referenceRadio).toBeVisible()

  await expect(descriptorRadio).toBeChecked()
  await expect(referenceRadio).not.toBeChecked()
})

// ---------------------------------------------------------------------------
// Test 5: Clicking the reference radio checks it and unchecks descriptor
// ---------------------------------------------------------------------------

test('clicking the reference radio checks it and unchecks descriptor', async ({ page }) => {
  await gotoDetail(page, COMPLETED_ID)
  await openBundleDialog(page)

  const descriptorRadio = page.getByTestId('dataset-mode-radio-descriptor')
  const referenceRadio = page.getByTestId('dataset-mode-radio-reference')

  // Confirm initial state
  await expect(descriptorRadio).toBeChecked()
  await expect(referenceRadio).not.toBeChecked()

  // Click the reference radio (via its label for a realistic interaction)
  await page.getByTestId('dataset-mode-label-reference').click()

  await expect(referenceRadio).toBeChecked()
  await expect(descriptorRadio).not.toBeChecked()
})

// ---------------------------------------------------------------------------
// Test 6: Cancel closes the dialog; re-opening resets to descriptor
// ---------------------------------------------------------------------------

test('Cancel closes the dialog; re-opening the bundle dialog resets dataset mode to descriptor', async ({
  page,
}) => {
  await gotoDetail(page, COMPLETED_ID)
  await openBundleDialog(page)

  // Switch to reference
  await page.getByTestId('dataset-mode-label-reference').click()
  await expect(page.getByTestId('dataset-mode-radio-reference')).toBeChecked()

  // Click Cancel
  await page.getByTestId('export-dialog-cancel').click()

  // Dialog should be gone
  await expect(page.getByRole('dialog')).not.toBeVisible()

  // Re-open the dialog
  await openBundleDialog(page)

  // Mode should be reset to descriptor
  await expect(page.getByTestId('dataset-mode-radio-descriptor')).toBeChecked()
  await expect(page.getByTestId('dataset-mode-radio-reference')).not.toBeChecked()
})

// ---------------------------------------------------------------------------
// Test 7: Happy path — Export with descriptor mode hits the correct URL
// ---------------------------------------------------------------------------

test('happy path: exporting with descriptor mode requests the correct export URL', async ({
  page,
  browserName,
}) => {
  await gotoDetail(page, COMPLETED_ID)
  await openBundleDialog(page)

  // descriptor is the default — click Export without changing mode
  await expect(page.getByTestId('dataset-mode-radio-descriptor')).toBeChecked()

  let capturedUrl: string | null = null

  if (browserName === 'firefox') {
    // Firefox does not reliably emit the 'download' event for anchor-triggered
    // downloads. Use the context-level request listener instead.
    page.context().on('request', (req) => {
      if (req.url().includes('/api/experiments/') && req.url().includes('/export')) {
        capturedUrl = req.url()
      }
    })
    await page.getByTestId('export-dialog-confirm').click()
    await expect(async () => {
      expect(capturedUrl).not.toBeNull()
    }).toPass({ timeout: 5000 })
  } else {
    // Chromium / WebKit: waitForEvent('download') reliably captures anchor downloads
    const downloadPromise = page.waitForEvent('download')
    await page.getByTestId('export-dialog-confirm').click()
    const download = await downloadPromise
    capturedUrl = download.url()
    expect(download.suggestedFilename()).toContain('.secrev.zip')
  }

  // Assert the URL contains the correct query parameter
  expect(capturedUrl).toContain(`/api/experiments/${COMPLETED_ID}/export`)
  expect(capturedUrl).toContain('dataset_mode=descriptor')

  // Dialog should close after Export
  await expect(page.getByRole('dialog')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 8: Happy path — Export with reference mode hits the correct URL
// ---------------------------------------------------------------------------

test('happy path: exporting with reference mode requests the correct export URL', async ({
  page,
  browserName,
}) => {
  await gotoDetail(page, COMPLETED_ID)
  await openBundleDialog(page)

  // Switch to reference
  await page.getByTestId('dataset-mode-label-reference').click()
  await expect(page.getByTestId('dataset-mode-radio-reference')).toBeChecked()

  let capturedUrl: string | null = null

  if (browserName === 'firefox') {
    // Firefox does not reliably emit the 'download' event for anchor-triggered
    // downloads. Use the context-level request listener instead.
    page.context().on('request', (req) => {
      if (req.url().includes('/api/experiments/') && req.url().includes('/export')) {
        capturedUrl = req.url()
      }
    })
    await page.getByTestId('export-dialog-confirm').click()
    await expect(async () => {
      expect(capturedUrl).not.toBeNull()
    }).toPass({ timeout: 5000 })
  } else {
    // Chromium / WebKit: waitForEvent('download') reliably captures anchor downloads
    const downloadPromise = page.waitForEvent('download')
    await page.getByTestId('export-dialog-confirm').click()
    const download = await downloadPromise
    capturedUrl = download.url()
    expect(download.suggestedFilename()).toContain('.secrev.zip')
  }

  // Assert the URL contains the correct query parameter
  expect(capturedUrl).toContain(`/api/experiments/${COMPLETED_ID}/export`)
  expect(capturedUrl).toContain('dataset_mode=reference')

  // Dialog should close after Export
  await expect(page.getByRole('dialog')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 9: Download dropdown is NOT rendered for a running experiment
// ---------------------------------------------------------------------------

test('Download dropdown is absent on a running (non-terminal) experiment detail page', async ({
  page,
}) => {
  await gotoDetail(page, RUNNING_ID)

  // The Download summary must not be present
  const summary = page.locator('summary').filter({ hasText: 'Download' })
  await expect(summary).not.toBeVisible()
})
