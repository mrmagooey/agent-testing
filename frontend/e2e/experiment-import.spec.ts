/**
 * e2e spec: experiment-import
 *
 * Covers the user story:
 *   As a security researcher, I want to import a previously exported
 *   .secrev.zip experiment bundle by selecting a file, choosing a conflict
 *   policy (reject / rename / merge), and clicking Upload — so that on
 *   success I see a summary with the experiment ID linked to the detail page,
 *   runs/findings counts, dataset rehydration/missing chips, and warnings;
 *   on failure I see the API's error message.
 *
 * Mocking strategy:
 *   mockApi() registers a catch-all GET /api/** handler. We add our own
 *   POST /api/experiments/import handler AFTER mockApi() — Playwright routes
 *   are LIFO so our handler wins. Non-matching methods fall through via
 *   route.fallback().
 *
 *   importBundle() uses XMLHttpRequest, not fetch, but Playwright intercepts
 *   both transparently through page.route().
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const EXPERIMENT_ID = 'imptest-aaaa-bbbb-cccc-dddddddddddd'
const OLD_EXPERIMENT_ID = 'origexp-aaaa-bbbb-cccc-dddddddddddd'

const IMPORT_SUMMARY_FULL: {
  experiment_id: string
  renamed_from: string | null
  runs_imported: number
  runs_skipped: number
  datasets_imported: number
  datasets_rehydrated: string[]
  datasets_missing: string[]
  dataset_labels_imported: number
  warnings: string[]
  findings_indexed: number
} = {
  experiment_id: EXPERIMENT_ID,
  renamed_from: null,
  runs_imported: 4,
  runs_skipped: 1,
  datasets_imported: 2,
  datasets_rehydrated: ['cve-2024-python', 'cve-2023-java'],
  datasets_missing: ['cve-2022-c-plus-plus'],
  dataset_labels_imported: 37,
  warnings: ['Run run-aaa-missing had no findings file — skipped', 'Dataset cve-2022-c-plus-plus not found in registry'],
  findings_indexed: 18,
}

const IMPORT_SUMMARY_RENAMED = {
  ...IMPORT_SUMMARY_FULL,
  experiment_id: 'imptest-renamed-new-id-dddddddddddd',
  renamed_from: OLD_EXPERIMENT_ID,
  runs_skipped: 0,
  datasets_rehydrated: [],
  datasets_missing: [],
  warnings: [],
}

/** Minimal fake .secrev.zip buffer — content doesn't matter for mocked tests. */
function fakeBundle(): { name: string; mimeType: string; buffer: Buffer } {
  return {
    name: 'bundle.secrev.zip',
    mimeType: 'application/zip',
    buffer: Buffer.from('PK\x03\x04fake-zip-content'),
  }
}

// ---------------------------------------------------------------------------
// Helper: register the import route mock (LIFO — wins over catch-all)
// ---------------------------------------------------------------------------

type FulfillOptions = Parameters<import('@playwright/test').Route['fulfill']>[0]

async function mockImportRoute(
  page: import('@playwright/test').Page,
  response: FulfillOptions,
) {
  await page.route('**/api/experiments/import', (route) => {
    const method = route.request().method()
    if (method !== 'POST') return route.fallback()
    return route.fulfill(response)
  })
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// Test 1: Page structure — heading, dropzone, radios, disabled Upload button
// ---------------------------------------------------------------------------

test('page shows heading, dropzone prompt, three conflict-policy radios, and disabled Upload button', async ({
  page,
}) => {
  await page.goto('/experiments/import')

  // Heading
  await expect(page.getByRole('heading', { name: 'Import Experiment' })).toBeVisible()

  // Dropzone text (the <code> element is inside the <p> so text matching works)
  await expect(page.getByRole('button', { name: 'Drop zone for experiment bundle' })).toBeVisible()
  await expect(page.getByText('.secrev.zip')).toBeVisible()
  await expect(page.getByText('or click to select a file')).toBeVisible()

  // Three conflict policy radios
  await expect(page.getByRole('radio', { name: /reject/i })).toBeVisible()
  await expect(page.getByRole('radio', { name: /rename/i })).toBeVisible()
  await expect(page.getByRole('radio', { name: /merge/i })).toBeVisible()

  // Upload button exists but is disabled (no file selected)
  const uploadBtn = page.getByRole('button', { name: 'Upload' })
  await expect(uploadBtn).toBeVisible()
  await expect(uploadBtn).toBeDisabled()
})

// ---------------------------------------------------------------------------
// Test 2: Selecting a file shows its name/size and enables Upload
// ---------------------------------------------------------------------------

test('selecting a file via the hidden file input shows name and size, enables Upload button', async ({
  page,
}) => {
  await page.goto('/experiments/import')

  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles(fakeBundle())

  // File name should be visible in the dropzone
  await expect(page.getByText('bundle.secrev.zip')).toBeVisible()
  // Size rendered as "0.00 MB — click or drop to change"
  await expect(page.getByText(/MB — click or drop to change/)).toBeVisible()

  // Upload button must now be enabled
  await expect(page.getByRole('button', { name: 'Upload' })).toBeEnabled()
})

// ---------------------------------------------------------------------------
// Test 3: Default conflict policy is "reject"; clicking "rename" selects it
// ---------------------------------------------------------------------------

test('default conflict policy is reject; clicking rename radio selects it', async ({ page }) => {
  await page.goto('/experiments/import')

  const rejectRadio = page.getByRole('radio', { name: /reject/i })
  const renameRadio = page.getByRole('radio', { name: /rename/i })

  // Default: reject is checked
  await expect(rejectRadio).toBeChecked()
  await expect(renameRadio).not.toBeChecked()

  // Click rename
  await renameRadio.click()

  await expect(renameRadio).toBeChecked()
  await expect(rejectRadio).not.toBeChecked()
})

// ---------------------------------------------------------------------------
// Test 4: Happy-path upload — full ImportSummary renders correctly
// ---------------------------------------------------------------------------

test('happy-path upload renders Import successful summary with all fields', async ({ page }) => {
  await mockImportRoute(page, {
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(IMPORT_SUMMARY_FULL),
  })

  await page.goto('/experiments/import')

  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles(fakeBundle())

  await page.getByRole('button', { name: 'Upload' }).click()

  // Success heading
  await expect(page.getByRole('heading', { name: 'Import successful' })).toBeVisible()

  // Experiment ID rendered as a link to /experiments/<id>
  const experimentLink = page.getByRole('link', { name: EXPERIMENT_ID })
  await expect(experimentLink).toBeVisible()
  await expect(experimentLink).toHaveAttribute('href', `/experiments/${EXPERIMENT_ID}`)

  // Counts
  await expect(page.getByText(/Runs imported:.*4/)).toBeVisible()
  await expect(page.getByText(/Runs skipped:.*1/)).toBeVisible()
  await expect(page.getByText(/Findings indexed:.*18/)).toBeVisible()
  await expect(page.getByText(/Datasets imported:.*2/)).toBeVisible()
  await expect(page.getByText(/Dataset labels imported:.*37/)).toBeVisible()

  // Rehydrated chips
  const rehydratedChips = page.locator('[data-testid="chip-rehydrated"]')
  await expect(rehydratedChips).toHaveCount(2)
  await expect(rehydratedChips.filter({ hasText: 'cve-2024-python' })).toBeVisible()
  await expect(rehydratedChips.filter({ hasText: 'cve-2023-java' })).toBeVisible()

  // Missing chip
  const missingChips = page.locator('[data-testid="chip-missing"]')
  await expect(missingChips).toHaveCount(1)
  await expect(missingChips.filter({ hasText: 'cve-2022-c-plus-plus' })).toBeVisible()

  // Warnings
  await expect(page.getByText('Run run-aaa-missing had no findings file — skipped')).toBeVisible()
  await expect(page.getByText('Dataset cve-2022-c-plus-plus not found in registry')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 5: renamed_from line renders when API returns a non-null renamed_from
// ---------------------------------------------------------------------------

test('renamed_from field renders "Renamed from <old-id>" when API returns non-null renamed_from', async ({
  page,
}) => {
  await mockImportRoute(page, {
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(IMPORT_SUMMARY_RENAMED),
  })

  await page.goto('/experiments/import')

  // Select rename policy
  await page.getByRole('radio', { name: /rename/i }).click()

  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles(fakeBundle())

  await page.getByRole('button', { name: 'Upload' }).click()

  await expect(page.getByRole('heading', { name: 'Import successful' })).toBeVisible()

  // "Renamed from <old-id>" line
  await expect(page.getByText(/Renamed from/)).toBeVisible()
  await expect(page.getByText(OLD_EXPERIMENT_ID)).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 6: Error path — API 400 renders alert with detail; no success block
// ---------------------------------------------------------------------------

test('API 400 response renders role=alert with the detail message; success summary absent', async ({
  page,
}) => {
  await mockImportRoute(page, {
    status: 400,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'Experiment ID already exists — use rename or merge policy' }),
  })

  await page.goto('/experiments/import')

  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles(fakeBundle())

  await page.getByRole('button', { name: 'Upload' }).click()

  // Error alert must be visible with the API's detail message
  const alert = page.getByRole('alert')
  await expect(alert).toBeVisible()
  await expect(alert).toContainText('Experiment ID already exists — use rename or merge policy')

  // Success summary must NOT be present
  await expect(page.getByRole('heading', { name: 'Import successful' })).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 7: Upload button is disabled while uploading is in progress
// ---------------------------------------------------------------------------

test('Upload button is disabled while upload is in progress', async ({ page }) => {
  // Use a route handler that waits for a signal before fulfilling, so we can
  // observe the mid-flight UI state. We resolve via a Promise that we control.
  let resolveUpload!: () => void
  const uploadComplete = new Promise<void>((resolve) => {
    resolveUpload = resolve
  })

  await page.route('**/api/experiments/import', async (route) => {
    const method = route.request().method()
    if (method !== 'POST') return route.fallback()
    // Pause until the test signals completion
    await uploadComplete
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(IMPORT_SUMMARY_FULL),
    })
  })

  await page.goto('/experiments/import')

  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles(fakeBundle())

  const uploadBtn = page.getByRole('button', { name: 'Upload' })
  await expect(uploadBtn).toBeEnabled()

  // Click upload — the handler is paused so the button should go disabled
  await uploadBtn.click()

  // While in flight the button should show "Uploading…" and be disabled
  const uploadingBtn = page.getByRole('button', { name: /Uploading/i })
  await expect(uploadingBtn).toBeDisabled()

  // Progress bar should appear
  await expect(page.getByRole('progressbar')).toBeVisible()

  // Allow the upload to complete
  resolveUpload()

  // After completion the success heading appears and the button reverts to Upload
  await expect(page.getByRole('heading', { name: 'Import successful' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Upload' })).toBeEnabled()
})

// ---------------------------------------------------------------------------
// Test 8: Clicking the experiment-id link navigates to /experiments/<id>
// ---------------------------------------------------------------------------

test('clicking the experiment-id link in the summary navigates to /experiments/<id>', async ({
  page,
}) => {
  await mockImportRoute(page, {
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(IMPORT_SUMMARY_FULL),
  })

  await page.goto('/experiments/import')

  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles(fakeBundle())

  await page.getByRole('button', { name: 'Upload' }).click()

  await expect(page.getByRole('heading', { name: 'Import successful' })).toBeVisible()

  // Click the experiment-id link
  await page.getByRole('link', { name: EXPERIMENT_ID }).click()

  // Should navigate to the experiment detail page
  await expect(page).toHaveURL(new RegExp(`/experiments/${EXPERIMENT_ID}`))
})
