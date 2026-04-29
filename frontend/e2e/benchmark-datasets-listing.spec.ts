/**
 * Story 57 — Datasets list surfaces benchmark cards with multi-language tags.
 *
 * The page renders all datasets (CVE/injected + benchmarks) in a single table.
 * Languages are comma-joined in one cell (not separate pill elements).
 * label_count and file_count are plain integers (no thousands separators).
 */

import { test, expect } from '@playwright/test'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { mockApi } from './helpers/mockApi'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const datasetsFixture = JSON.parse(
  readFileSync(join(__dirname, 'fixtures/datasets.json'), 'utf-8')
) as unknown[]

const BENCHMARK_ROWS = [
  {
    name: 'benchmark-python-1.0',
    source: 'benchmark',
    languages: ['python'],
    label_count: 21000,
    file_count: 7000,
    size_bytes: 12_345_678,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    name: 'benchmark-java-1.0',
    source: 'benchmark',
    languages: ['java'],
    label_count: 21000,
    file_count: 7000,
    size_bytes: 18_345_678,
    created_at: '2026-01-02T00:00:00Z',
  },
  {
    name: 'sard-c',
    source: 'sard',
    languages: ['c'],
    label_count: 4000,
    file_count: 4000,
    size_bytes: 50_000_000,
    created_at: '2026-01-03T00:00:00Z',
  },
  {
    name: 'mitre-cwe-demonstrative',
    source: 'mitre_demo',
    languages: ['c', 'cpp', 'java', 'python', 'javascript'],
    label_count: 800,
    file_count: 1600,
    size_bytes: 2_000_000,
    created_at: '2026-01-04T00:00:00Z',
  },
  {
    name: 'bigvul-c',
    source: 'benchmark',
    languages: ['c', 'cpp'],
    label_count: 8000,
    file_count: 16000,
    size_bytes: 100_000_000,
    created_at: '2026-01-05T00:00:00Z',
  },
]

const ALL_DATASETS = [...BENCHMARK_ROWS, ...datasetsFixture]

function jsonBody(body: unknown) {
  return {
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  }
}

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  // Override /api/datasets to include benchmark rows (LIFO — registered after mockApi)
  await page.route('**/api/datasets', async (route) => {
    if (route.request().method() !== 'GET') {
      return route.fallback()
    }
    return route.fulfill(jsonBody(ALL_DATASETS))
  })
  await page.goto('/datasets')
})

// ---------------------------------------------------------------------------
// 1. All rows are rendered
// ---------------------------------------------------------------------------

test('renders all 5 benchmark dataset rows', async ({ page }) => {
  for (const row of BENCHMARK_ROWS) {
    await expect(page.getByText(row.name)).toBeVisible()
  }
})

test('renders the original 3 CVE/injected dataset rows alongside benchmarks', async ({ page }) => {
  await expect(page.getByText('cve-2024-python')).toBeVisible()
  await expect(page.getByText('cve-2024-js')).toBeVisible()
  await expect(page.getByText('injected-java-2024')).toBeVisible()
})

test('total row count in table body is 8 (5 benchmarks + 3 existing)', async ({ page }) => {
  const rows = page.locator('tbody tr')
  await expect(rows).toHaveCount(8)
})

// ---------------------------------------------------------------------------
// 2. Language cells render as comma-joined strings
// ---------------------------------------------------------------------------

test('single-language benchmark rows show the language', async ({ page }) => {
  // benchmark-python-1.0 row — find the row then its language cell
  const pythonRow = page.locator('tbody tr').filter({ hasText: 'benchmark-python-1.0' })
  // Languages column is the 6th cell (0-indexed: name, source, labels, files, size, languages, created)
  const langCell = pythonRow.locator('td').nth(5)
  await expect(langCell).toHaveText('python')
})

test('single-language java benchmark row shows java', async ({ page }) => {
  const javaRow = page.locator('tbody tr').filter({ hasText: 'benchmark-java-1.0' })
  const langCell = javaRow.locator('td').nth(5)
  await expect(langCell).toHaveText('java')
})

test('sard-c row shows c language', async ({ page }) => {
  const sardRow = page.locator('tbody tr').filter({ hasText: 'sard-c' })
  const langCell = sardRow.locator('td').nth(5)
  await expect(langCell).toHaveText('c')
})

test('multi-language mitre row shows all languages comma-joined', async ({ page }) => {
  const mitreRow = page.locator('tbody tr').filter({ hasText: 'mitre-cwe-demonstrative' })
  const langCell = mitreRow.locator('td').nth(5)
  await expect(langCell).toHaveText('c, cpp, java, python, javascript')
})

test('bigvul-c row shows c, cpp comma-joined', async ({ page }) => {
  const bigvulRow = page.locator('tbody tr').filter({ hasText: 'bigvul-c' })
  const langCell = bigvulRow.locator('td').nth(5)
  await expect(langCell).toHaveText('c, cpp')
})

// ---------------------------------------------------------------------------
// 3. label_count and file_count are plain integers (no thousands separators)
// ---------------------------------------------------------------------------

test('benchmark-python-1.0 label_count renders as plain integer 21000', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'benchmark-python-1.0' })
  const labelCell = row.locator('td').nth(2)
  await expect(labelCell).toHaveText('21000')
})

test('benchmark-python-1.0 file_count renders as plain integer 7000', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'benchmark-python-1.0' })
  const fileCell = row.locator('td').nth(3)
  await expect(fileCell).toHaveText('7000')
})

test('mitre-cwe-demonstrative label_count renders as 800', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'mitre-cwe-demonstrative' })
  const labelCell = row.locator('td').nth(2)
  await expect(labelCell).toHaveText('800')
})

test('bigvul-c file_count renders as 16000', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'bigvul-c' })
  const fileCell = row.locator('td').nth(3)
  await expect(fileCell).toHaveText('16000')
})

// ---------------------------------------------------------------------------
// 4. Clicking a benchmark row navigates to /datasets/<name>
// ---------------------------------------------------------------------------

test('clicking benchmark-python-1.0 row navigates to its detail page', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'benchmark-python-1.0' })
  await row.click()
  await expect(page).toHaveURL('/datasets/benchmark-python-1.0')
})

test('clicking mitre-cwe-demonstrative row navigates to its detail page', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'mitre-cwe-demonstrative' })
  await row.click()
  await expect(page).toHaveURL('/datasets/mitre-cwe-demonstrative')
})

test('clicking sard-c row navigates to its detail page', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'sard-c' })
  await row.click()
  await expect(page).toHaveURL('/datasets/sard-c')
})

// ---------------------------------------------------------------------------
// 5. Source badges for non-standard sources fall back to gray styling
//    (benchmark, sard, mitre_demo are not in SOURCE_BADGE map)
// ---------------------------------------------------------------------------

test('benchmark source text is visible for benchmark-python-1.0', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'benchmark-python-1.0' })
  const sourceBadge = row.locator('td').nth(1).locator('span')
  await expect(sourceBadge).toHaveText('benchmark')
})

test('sard source text is visible for sard-c', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'sard-c' })
  const sourceBadge = row.locator('td').nth(1).locator('span')
  await expect(sourceBadge).toHaveText('sard')
})

test('mitre_demo source text is visible for mitre-cwe-demonstrative', async ({ page }) => {
  const row = page.locator('tbody tr').filter({ hasText: 'mitre-cwe-demonstrative' })
  const sourceBadge = row.locator('td').nth(1).locator('span')
  await expect(sourceBadge).toHaveText('mitre_demo')
})

// ---------------------------------------------------------------------------
// 6. Language filter works with benchmark datasets
// ---------------------------------------------------------------------------

test('filtering by "python" shows benchmark-python-1.0 and hides java-only rows', async ({ page }) => {
  await page.locator('input[placeholder="Filter datasets…"]').fill('python')
  await expect(page.getByText('benchmark-python-1.0')).toBeVisible()
  // java-only row should not appear
  await expect(page.locator('tbody tr').filter({ hasText: 'benchmark-java-1.0' })).not.toBeVisible()
})

test('filtering by "benchmark" shows all 3 benchmark-source rows', async ({ page }) => {
  await page.locator('input[placeholder="Filter datasets…"]').fill('benchmark')
  // benchmark-python-1.0, benchmark-java-1.0, bigvul-c all have source "benchmark"
  await expect(page.getByText('benchmark-python-1.0')).toBeVisible()
  await expect(page.getByText('benchmark-java-1.0')).toBeVisible()
  await expect(page.getByText('bigvul-c')).toBeVisible()
  // sard and mitre rows should not appear
  await expect(page.locator('tbody tr').filter({ hasText: 'sard-c' })).not.toBeVisible()
})
