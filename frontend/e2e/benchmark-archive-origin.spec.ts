import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const DATASET_NAME = 'benchmark-archive-2024'

// ─── Fixture helpers ───────────────────────────────────────────────────────────

function makeArchiveDatasetRow(overrides: {
  archive_url?: string | null
  archive_sha256?: string | null
  archive_format?: string | null
} = {}) {
  return {
    name: DATASET_NAME,
    kind: 'archive' as const,
    origin_url: null,
    origin_commit: null,
    origin_ref: null,
    cve_id: null,
    base_dataset: null,
    recipe_json: null,
    metadata: {},
    created_at: '2026-01-15T00:00:00Z',
    materialized_at: '2026-01-16T00:00:00Z',
    archive_url: 'https://example.com/datasets/benchmark-2024.tar.gz',
    archive_sha256: 'abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890',
    archive_format: 'tar.gz',
    ...overrides,
  }
}

async function mockDatasetGet(
  page: Parameters<typeof mockApi>[0],
  row: ReturnType<typeof makeArchiveDatasetRow>,
) {
  await page.route(`**/api/datasets/${DATASET_NAME}`, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(row),
    })
  })
}

// ─── Tests ────────────────────────────────────────────────────────────────────

test('archive dataset: heading reads "Archive origin"', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, makeArchiveDatasetRow())
  await page.goto(`/datasets/${DATASET_NAME}`)

  await expect(page.getByText('Archive origin', { exact: true })).toBeVisible()
})

test('archive dataset: URL row links externally to archive_url', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, makeArchiveDatasetRow())
  await page.goto(`/datasets/${DATASET_NAME}`)

  const link = page.getByTestId('archive-url-link')
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('href', 'https://example.com/datasets/benchmark-2024.tar.gz')
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', 'noopener noreferrer')
})

test('archive dataset: sha256 row shows truncated value (12 chars) and copy button', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, makeArchiveDatasetRow())
  await page.goto(`/datasets/${DATASET_NAME}`)

  const sha256Cell = page.getByTestId('archive-sha256')
  await expect(sha256Cell).toBeVisible()
  // Should show the first 12 chars of the sha256
  await expect(sha256Cell).toContainText('abcdef123456')
  // Full sha256 should NOT be displayed verbatim (it's truncated)
  const fullSha = 'abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890'
  const cellText = await sha256Cell.innerText()
  expect(cellText).not.toContain(fullSha)

  // Copy button is present
  const copyBtn = sha256Cell.getByTestId('copy-button')
  await expect(copyBtn).toBeVisible()
})

test('archive dataset: format row shows the format value', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, makeArchiveDatasetRow())
  await page.goto(`/datasets/${DATASET_NAME}`)

  const formatCell = page.getByTestId('archive-format')
  await expect(formatCell).toBeVisible()
  await expect(formatCell).toContainText('tar.gz')
})

test('archive dataset with null archive_url: URL row shows em-dash', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, makeArchiveDatasetRow({ archive_url: null }))
  await page.goto(`/datasets/${DATASET_NAME}`)

  // Heading still shows Archive origin
  await expect(page.getByText('Archive origin', { exact: true })).toBeVisible()

  // No external link for URL
  await expect(page.getByTestId('archive-url-link')).not.toBeAttached()

  // URL row contains em-dash
  const urlRow = page.locator('div').filter({ has: page.getByText('URL', { exact: true }) }).first()
  await expect(urlRow).toContainText('—')
})

test('archive dataset with null archive_sha256: sha256 row shows em-dash', async ({ page }) => {
  await mockApi(page)
  await mockDatasetGet(page, makeArchiveDatasetRow({ archive_sha256: null }))
  await page.goto(`/datasets/${DATASET_NAME}`)

  // No sha256 testid element (the em-dash branch doesn't have it)
  await expect(page.getByTestId('archive-sha256')).not.toBeAttached()

  // Sha256 row shows em-dash
  const sha256Row = page.locator('div').filter({ has: page.getByText('Sha256', { exact: true }) }).first()
  await expect(sha256Row).toContainText('—')
})

test('sanity: git dataset still renders "Git origin" (regression guard)', async ({ page }) => {
  const gitDataset = {
    name: DATASET_NAME,
    kind: 'git' as const,
    origin_url: 'https://github.com/example/repo',
    origin_commit: 'deadbeef1234',
    origin_ref: 'main',
    cve_id: null,
    base_dataset: null,
    recipe_json: null,
    metadata: {},
    created_at: '2026-01-15T00:00:00Z',
    materialized_at: '2026-01-16T00:00:00Z',
  }

  await mockApi(page)
  await page.route(`**/api/datasets/${DATASET_NAME}`, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(gitDataset),
    })
  })
  await page.goto(`/datasets/${DATASET_NAME}`)

  await expect(page.getByText('Git origin', { exact: true })).toBeVisible()
  await expect(page.getByText('Archive origin', { exact: true })).not.toBeVisible()
})
