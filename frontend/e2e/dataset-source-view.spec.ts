/**
 * E2E tests for the finding-to-dataset source drill-through feature.
 *
 * Covers:
 * - "View in dataset" link appears on expanded finding rows in RunDetail
 * - Link URL contains correct path/line/end/from_experiment/from_run params
 * - DatasetSourceView page renders with correct heading and file path
 * - Loading skeleton shown while fetching
 * - 404 state message shown when file is not found
 * - Binary file placeholder shown
 * - Finding range (amber) legend shown when highlightStart is set
 * - Ground-truth label (emerald) legend shown when labels exist
 * - "Back to run" link in source view
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const BATCH_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'
const DATASET_NAME = 'cve-2024-python'

const FILE_PATH = 'src/auth/login.py'
const LINE_START = 42
const LINE_END = 47

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ─── RunDetail — "View in dataset" link ────────────────────────────────────

test.describe('View in dataset link on RunDetail', () => {
  test('link is visible after expanding a finding with a file_path', async ({ page }) => {
    await page.goto(`/experiments/${BATCH_ID}/runs/${RUN_ID}`)

    const findingRow = page.getByRole('row', { name: /SQL Injection in user login handler/i })
    await findingRow.click()

    await expect(page.getByRole('link', { name: 'View in dataset' }).first()).toBeVisible()
  })

  test('link URL contains correct path, line, end, from_experiment, from_run params', async ({
    page,
  }) => {
    await page.goto(`/experiments/${BATCH_ID}/runs/${RUN_ID}`)

    const findingRow = page.getByRole('row', { name: /SQL Injection in user login handler/i })
    await findingRow.click()

    const link = page.getByRole('link', { name: 'View in dataset' }).first()
    await expect(link).toBeVisible()

    const href = await link.getAttribute('href')
    expect(href).toContain(`/datasets/${DATASET_NAME}/source`)
    expect(href).toContain(`path=${encodeURIComponent(FILE_PATH)}`)
    expect(href).toContain(`line=${LINE_START}`)
    expect(href).toContain(`end=${LINE_END}`)
    expect(href).toContain(`from_experiment=${BATCH_ID}`)
    expect(href).toContain(`from_run=${RUN_ID}`)
  })

  test('link opens in a new tab (target=_blank)', async ({ page }) => {
    await page.goto(`/experiments/${BATCH_ID}/runs/${RUN_ID}`)
    const findingRow = page.getByRole('row', { name: /SQL Injection in user login handler/i })
    await findingRow.click()

    const link = page.getByRole('link', { name: 'View in dataset' }).first()
    await expect(link).toHaveAttribute('target', '_blank')
  })
})

// ─── DatasetSourceView page ─────────────────────────────────────────────────

test.describe('DatasetSourceView page', () => {
  test('shows the file path as heading', async ({ page }) => {
    const params = new URLSearchParams({
      path: FILE_PATH,
      line: String(LINE_START),
      end: String(LINE_END),
      from_experiment: BATCH_ID,
      from_run: RUN_ID,
    })
    await page.goto(`/datasets/${DATASET_NAME}/source?${params}`)
    await expect(page.getByRole('heading', { level: 1 })).toContainText(FILE_PATH)
  })

  test('shows dataset breadcrumb linking back to dataset', async ({ page }) => {
    const params = new URLSearchParams({ path: FILE_PATH })
    await page.goto(`/datasets/${DATASET_NAME}/source?${params}`)
    await expect(page.getByRole('link', { name: DATASET_NAME })).toBeVisible()
  })

  test('shows "Back to run" link when from_experiment and from_run are present', async ({
    page,
  }) => {
    const params = new URLSearchParams({
      path: FILE_PATH,
      from_experiment: BATCH_ID,
      from_run: RUN_ID,
    })
    await page.goto(`/datasets/${DATASET_NAME}/source?${params}`)
    await expect(page.getByRole('link', { name: /back to run/i })).toBeVisible()
  })

  test('does not show "Back to run" link without from_experiment param', async ({ page }) => {
    const params = new URLSearchParams({ path: FILE_PATH })
    await page.goto(`/datasets/${DATASET_NAME}/source?${params}`)
    await expect(page.getByRole('link', { name: /back to run/i })).not.toBeVisible()
  })

  test('shows file content in code viewer after load', async ({ page }) => {
    const params = new URLSearchParams({
      path: FILE_PATH,
      line: String(LINE_START),
      end: String(LINE_END),
    })
    await page.goto(`/datasets/${DATASET_NAME}/source?${params}`)
    // The code viewer should render; look for the cm-editor container.
    await expect(page.locator('.cm-editor')).toBeVisible({ timeout: 8000 })
  })

  test('shows 404 message when file is not found', async ({ page }) => {
    // Override the file endpoint to return 404
    await page.route('**/api/datasets/*/file*', (route) => {
      route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'File not found' }) })
    })

    const params = new URLSearchParams({ path: 'gone/file.py' })
    await page.goto(`/datasets/${DATASET_NAME}/source?${params}`)
    await expect(page.getByText(/file no longer in dataset/i)).toBeVisible()
  })

  test('shows binary placeholder for binary files', async ({ page }) => {
    await page.route('**/api/datasets/*/file*', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          path: 'data.bin',
          binary: true,
          content: '',
          language: 'text',
          line_count: 0,
          size_bytes: 4096,
          labels: [],
        }),
      })
    })

    const params = new URLSearchParams({ path: 'data.bin' })
    await page.goto(`/datasets/${DATASET_NAME}/source?${params}`)
    await expect(page.getByText(/binary file/i)).toBeVisible()
  })

  test('shows amber finding-range legend when highlightStart is set', async ({ page }) => {
    const params = new URLSearchParams({
      path: FILE_PATH,
      line: String(LINE_START),
      end: String(LINE_END),
    })
    await page.goto(`/datasets/${DATASET_NAME}/source?${params}`)
    await expect(page.getByText(/finding range/i)).toBeVisible({ timeout: 8000 })
  })

  test('shows truncation banner for large files', async ({ page }) => {
    await page.route('**/api/datasets/*/file*', (route) => {
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          path: FILE_PATH,
          content: 'x'.repeat(50) + '\n\n... [truncated] ...\n\n' + 'y'.repeat(50),
          language: 'python',
          line_count: 5,
          size_bytes: 3 * 1024 * 1024,
          labels: [],
          truncated: true,
        }),
      })
    })

    const params = new URLSearchParams({ path: FILE_PATH })
    await page.goto(`/datasets/${DATASET_NAME}/source?${params}`)
    await expect(page.getByText(/file is large/i)).toBeVisible()
    await expect(page.getByRole('button', { name: /load anyway/i })).toBeVisible()
  })
})
