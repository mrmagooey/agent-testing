/**
 * e2e spec: RunDetail "Download Run" button
 *
 * Covers the user story:
 *   As a security researcher reviewing a single run, I want a "Download Run"
 *   button that initiates a browser download of the experiment's full results
 *   bundle — sharing the experiment's results-download endpoint but reachable
 *   from inside any of its runs — so I can grab the artifact without navigating
 *   back up to the experiment detail page first.
 *
 * Mocking strategy:
 *   mockApi() registers a catch-all GET /api/** handler (LIFO in Playwright).
 *   Per-test route handlers registered after mockApi() take priority.
 *   Non-matching methods fall through via route.fallback().
 *
 *   The DownloadButton creates a temporary <a download> and calls .click() on it.
 *   Playwright intercepts the resulting download event (Chromium) or the
 *   outgoing request (Firefox / browser-agnostic path) via context.on('request').
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'
const RUN_URL = `/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`
const DOWNLOAD_URL_PATTERN = `**/api/experiments/${EXPERIMENT_ID}/results/download`
const EXPECTED_FILENAME = `experiment-${EXPERIMENT_ID}-reports.zip`

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Register the download route mock BEFORE navigating so it is in place when
 * the button click fires. Fulfills with a minimal zip body and a
 * content-disposition header so Playwright can derive the suggested filename.
 */
async function mockDownload(page: import('@playwright/test').Page) {
  await page.route(DOWNLOAD_URL_PATTERN, (route) =>
    route.fulfill({
      status: 200,
      headers: {
        'content-disposition': `attachment; filename=${EXPECTED_FILENAME}`,
      },
      contentType: 'application/zip',
      body: 'zip-bytes',
    })
  )
}

/** Navigate to the run detail page and wait for the run header to render. */
async function gotoRun(page: import('@playwright/test').Page) {
  await page.goto(RUN_URL)
  // Wait for the run header as a positive mount signal so subsequent negative
  // assertions don't pass vacuously while the page is still loading.
  await expect(page.getByRole('button', { name: 'Download Run' })).toBeVisible()
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

test.describe('RunDetail Download Run button', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  // Button visibility is already covered by run-detail-interactions.spec.ts:391-393.
  // This spec focuses on the click flow: URL, filename, no-navigation, cleanup.

  // -------------------------------------------------------------------------
  // 1. Click triggers download with the experiment-scoped filename (Chromium)
  // -------------------------------------------------------------------------

  test('click triggers download with the experiment-scoped filename', async ({
    page,
    browserName,
  }) => {
    // Firefox does not reliably fire the Playwright `download` event for
    // programmatic <a download> clicks; use the request-capture path instead.
    test.skip(browserName === 'firefox', 'Firefox uses request-capture path (test 4)')

    await mockDownload(page)
    await gotoRun(page)

    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download Run' }).click()
    const download = await downloadPromise

    expect(download.suggestedFilename()).toBe(EXPECTED_FILENAME)
  })

  // -------------------------------------------------------------------------
  // 3. Download URL targets the experiment-results endpoint, not the run
  // -------------------------------------------------------------------------

  test('download URL contains the experiment-results endpoint and not the run ID', async ({
    page,
    browserName,
  }) => {
    test.skip(browserName === 'firefox', 'Firefox uses request-capture path (test 4)')

    await mockDownload(page)
    await gotoRun(page)

    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download Run' }).click()
    const download = await downloadPromise

    const url = download.url()
    // Must contain the experiment-scoped download path
    expect(url).toContain(`/api/experiments/${EXPERIMENT_ID}/results/download`)
    // Must NOT contain the run ID — RunDetail passes experimentId, not runId
    expect(url).not.toContain(RUN_ID)
  })

  // -------------------------------------------------------------------------
  // 4. Firefox-safe: capture the outgoing request via context.on('request')
  //
  // In Chromium, programmatic <a download>.click() is captured as a Playwright
  // `download` event, not a standard network request, so context.on('request')
  // does NOT fire in Chromium for download-type navigations. This test uses
  // the Firefox path from the export-menu iter-5 pattern: register a request
  // listener and assert with toPass(). On Chromium we fall back to the
  // waitForEvent('download') approach to keep the test browser-agnostic.
  // -------------------------------------------------------------------------

  test('click fires a request to the experiment-results download endpoint (browser-agnostic)', async ({
    page,
    browserName,
  }) => {
    await mockDownload(page)
    await gotoRun(page)

    let capturedUrl: string | null = null

    if (browserName === 'firefox') {
      // Firefox does not reliably emit the 'download' event for anchor-triggered
      // downloads. Use context-level request listener as the safe fallback.
      page.context().on('request', (req) => {
        if (
          req.url().includes('/api/experiments/') &&
          req.url().includes('/results/download')
        ) {
          capturedUrl = req.url()
        }
      })
      await page.getByRole('button', { name: 'Download Run' }).click()
      await expect(async () => {
        expect(capturedUrl).not.toBeNull()
      }).toPass({ timeout: 5000 })
    } else {
      // Chromium: download-type requests are exposed via the download event.
      const downloadPromise = page.waitForEvent('download')
      await page.getByRole('button', { name: 'Download Run' }).click()
      const download = await downloadPromise
      capturedUrl = download.url()
    }

    // Confirm the captured URL is experiment-scoped, not run-scoped.
    expect(capturedUrl).toContain(`/api/experiments/${EXPERIMENT_ID}/results/download`)
    expect(capturedUrl).not.toContain(RUN_ID)
  })

  // -------------------------------------------------------------------------
  // 5. Button click does not navigate the page
  // -------------------------------------------------------------------------

  test('button click does not navigate away from the run detail page', async ({
    page,
    browserName,
  }) => {
    test.skip(browserName === 'firefox', 'Firefox uses request-capture path; navigation assertion is Chromium-only')

    await mockDownload(page)
    await gotoRun(page)

    const urlBefore = page.url()

    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download Run' }).click()
    await downloadPromise

    // The temporary <a download> click must not cause a page navigation.
    expect(page.url()).toBe(urlBefore)
  })

  // -------------------------------------------------------------------------
  // 6. Temporary <a> element is cleaned up after click
  // -------------------------------------------------------------------------

  test('temporary <a download> element is removed from the DOM after click', async ({
    page,
    browserName,
  }) => {
    test.skip(browserName === 'firefox', 'Firefox uses request-capture path; DOM assertion is Chromium-only')

    await mockDownload(page)
    await gotoRun(page)

    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Download Run' }).click()
    await downloadPromise

    // Production calls document.body.removeChild(a) after the click.
    // After the download event fires the element must be gone.
    await expect(page.locator(`a[download="${EXPECTED_FILENAME}"]`)).toHaveCount(0)
  })
})
