import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const DATASET_NAME = 'origin-test-dataset'

const BASE_ROW = {
  name: DATASET_NAME,
  kind: 'git' as const,
  origin_url: null as string | null,
  origin_commit: null as string | null,
  origin_ref: null as string | null,
  cve_id: null as string | null,
  base_dataset: null as string | null,
  recipe_json: null as string | null,
  metadata: {},
  created_at: '2026-03-01T00:00:00Z',
  materialized_at: '2026-03-02T00:00:00Z',
}

async function mockDatasetGet(
  page: Parameters<typeof mockApi>[0],
  overrides: Partial<typeof BASE_ROW> & { kind?: 'git' | 'derived' },
) {
  const row = { ...BASE_ROW, ...overrides }
  await page.route(`**/api/datasets/${DATASET_NAME}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(row),
    })
  })
}

/** Locate the OriginCard by scoping to a div that contains the "Git origin" or "Derived from" heading. */
function originCard(page: Parameters<typeof mockApi>[0]) {
  return page
    .locator('div')
    .filter({ has: page.getByRole('heading', { name: /^(Git origin|Derived from)$/ }) })
    .first()
}

test.describe('DatasetDetail OriginCard + RecipeSummary', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  // Test 1: Git origin URL link with correct attributes
  test('git origin URL link is visible with target=_blank and rel=noopener noreferrer', async ({ page }) => {
    await mockDatasetGet(page, {
      kind: 'git',
      origin_url: 'https://github.com/example/cve-repo',
    })
    await page.goto(`/datasets/${DATASET_NAME}`)

    const card = originCard(page)
    const link = card.getByRole('link', { name: 'https://github.com/example/cve-repo' })
    await expect(link).toBeVisible()
    await expect(link).toHaveAttribute('href', 'https://github.com/example/cve-repo')
    await expect(link).toHaveAttribute('target', '_blank')
    const rel = await link.getAttribute('rel')
    expect(rel).toContain('noopener')
    expect(rel).toContain('noreferrer')
  })

  // Test 2: Short commit hash rendered as slice(0, 12)
  test('short commit hash shows first 12 characters', async ({ page }) => {
    await mockDatasetGet(page, {
      kind: 'git',
      origin_commit: 'abc123def456789012345',
    })
    await page.goto(`/datasets/${DATASET_NAME}`)

    const card = originCard(page)
    await expect(card.getByText('abc123def456')).toBeVisible()
    // Ensure the full 21-char hash is NOT displayed as text (truncation is observable)
    await expect(card.getByText('abc123def456789012345')).toHaveCount(0)
  })

  // Test 3: CopyButton click writes full commit hash to clipboard
  test('copy button writes full commit hash to clipboard', async ({ page, context, browserName }) => {
    test.skip(browserName === 'firefox', 'Firefox clipboard readback requires user gesture; skip clipboard read assertion')
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await mockDatasetGet(page, {
      kind: 'git',
      origin_commit: 'abc123def456789012345',
    })
    await page.goto(`/datasets/${DATASET_NAME}`)

    const card = originCard(page)
    await card.getByTestId('copy-button').click()

    const clipboardText = await page.evaluate(() => navigator.clipboard.readText())
    expect(clipboardText).toBe('abc123def456789012345')
  })

  // Test 4: CopyButton icon flips from copy to check then back
  test('copy button icon flips to check then back after 1500ms', async ({ page, context, browserName }) => {
    // clipboard-read is not supported by Firefox grantPermissions; only request on Chromium
    if (browserName !== 'firefox') {
      await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    }
    await mockDatasetGet(page, {
      kind: 'git',
      origin_commit: 'abc123def456789012345',
    })
    await page.goto(`/datasets/${DATASET_NAME}`)

    const card = originCard(page)
    const copyBtn = card.getByTestId('copy-button')

    await copyBtn.click()
    await expect(copyBtn).toContainText('✓')

    await expect(copyBtn).toContainText('⎘', { timeout: 3000 })
  })

  // Test 5a: Origin ref renders when set
  test('origin ref row is visible when origin_ref is set', async ({ page }) => {
    await mockDatasetGet(page, {
      kind: 'git',
      origin_ref: 'v1.2.3',
    })
    await page.goto(`/datasets/${DATASET_NAME}`)

    const card = originCard(page)
    await expect(card.getByText('Ref')).toBeVisible()
    await expect(card.getByText('v1.2.3')).toBeVisible()
  })

  // Test 5b: Origin ref row absent when null
  test('origin ref row is not visible when origin_ref is null', async ({ page }) => {
    await mockDatasetGet(page, {
      kind: 'git',
      origin_ref: null,
    })
    await page.goto(`/datasets/${DATASET_NAME}`)

    const card = originCard(page)
    await expect(card.getByText('Ref', { exact: true })).toHaveCount(0)
  })

  // Test 6: CVE link points to /cve-discovery?id=<cve_id>
  test('CVE link is visible and points to cve-discovery route', async ({ page }) => {
    await mockDatasetGet(page, {
      kind: 'git',
      cve_id: 'CVE-2024-12345',
    })
    await page.goto(`/datasets/${DATASET_NAME}`)

    const card = originCard(page)
    const cveLink = card.getByRole('link', { name: 'CVE-2024-12345' })
    await expect(cveLink).toBeVisible()
    await expect(cveLink).toHaveAttribute('href', /\/cve-discovery\?id=CVE-2024-12345/)
  })

  // Test 7: Non-http origin_url renders as plain mono span, not a link
  test('non-http origin_url renders as plain text, not a link', async ({ page }) => {
    await mockDatasetGet(page, {
      kind: 'git',
      origin_url: 'git@github.com:example/cve-repo.git',
    })
    await page.goto(`/datasets/${DATASET_NAME}`)

    const card = originCard(page)
    await expect(card.getByText('git@github.com:example/cve-repo.git')).toBeVisible()
    await expect(card.getByRole('link', { name: /git@github\.com/ })).toHaveCount(0)
  })

  // Test 8: Derived dataset shows "Derived from" header and base_dataset link
  test('derived dataset shows "Derived from" header and base dataset link', async ({ page }) => {
    await mockDatasetGet(page, {
      kind: 'derived',
      base_dataset: 'cve-2024-python-clean',
    })
    await page.goto(`/datasets/${DATASET_NAME}`)

    const card = originCard(page)
    await expect(card.getByRole('heading', { name: 'Derived from' })).toBeVisible()
    const baseLink = card.getByRole('link', { name: 'cve-2024-python-clean' })
    await expect(baseLink).toBeVisible()
    await expect(baseLink).toHaveAttribute('href', /\/datasets\/cve-2024-python-clean/)
  })

  // Test 9: RecipeSummary applications count and expandable details
  test('recipe summary shows templates_version, apps count, and expandable app list', async ({ page }) => {
    const recipeJson = JSON.stringify({
      templates_version: 'v3',
      applications: [
        { template_id: 'sqli-v1', target_file: 'src/login.py', seed: 42 },
        { template_id: 'xss-v2', target_file: 'src/render.py' },
      ],
    })
    await mockDatasetGet(page, {
      kind: 'derived',
      recipe_json: recipeJson,
    })
    await page.goto(`/datasets/${DATASET_NAME}`)

    const card = originCard(page)

    // templates_version and app count
    await expect(card.getByText(/Templates version/)).toBeVisible()
    await expect(card.getByText('v3')).toBeVisible()
    await expect(card.getByText(/Applications:\s*2/)).toBeVisible()

    // Expand the details
    const summary = card.getByText('Show applications')
    await expect(summary).toBeVisible()
    await summary.click()

    // Assert individual app fields are now visible
    await expect(card.getByText('sqli-v1')).toBeVisible()
    await expect(card.getByText('src/login.py')).toBeVisible()
    await expect(card.getByText('seed: 42')).toBeVisible()
    await expect(card.getByText('xss-v2')).toBeVisible()
    await expect(card.getByText('src/render.py')).toBeVisible()
  })

  // Test 10: Invalid recipe JSON shows error message
  test('invalid recipe JSON shows error message and no Applications line', async ({ page }) => {
    await mockDatasetGet(page, {
      kind: 'derived',
      recipe_json: 'not-valid-json',
    })
    await page.goto(`/datasets/${DATASET_NAME}`)

    const card = originCard(page)
    await expect(card.locator('p.text-red-500').filter({ hasText: 'Invalid recipe JSON' })).toBeVisible()
    await expect(card.getByText(/Applications:/)).toHaveCount(0)
  })
})
