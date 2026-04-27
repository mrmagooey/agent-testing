import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const PAGE_URL = `/experiments/${EXPERIMENT_ID}`
const SEARCH_INPUT = 'input[placeholder*="Search findings"]'

const SQL_TITLE = 'SQL Injection in user login handler'
const XSS_TITLE = 'Reflected XSS in search results'
const PATH_TITLE = 'Path traversal in file download endpoint'

test.describe('FindingsSearch on ExperimentDetail', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto(PAGE_URL)
    await page.getByRole('heading', { name: 'Findings' }).waitFor()
  })

  // ---------------------------------------------------------------------------
  // 1. Input renders with placeholder and search-icon (not spinner, not clear)
  // ---------------------------------------------------------------------------

  test('search input renders with correct placeholder and initial search icon', async ({ page }) => {
    const input = page.locator(SEARCH_INPUT)
    await expect(input).toBeVisible()

    const iconArea = page.locator('div.absolute.right-3')
    await expect(iconArea.locator('svg.animate-spin')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Clear search' })).toHaveCount(0)
    await expect(iconArea.locator('svg')).toHaveCount(1)
  })

  // ---------------------------------------------------------------------------
  // 2. Initial findings count is 2 (from experiment-results fixture)
  // ---------------------------------------------------------------------------

  test('initial findings count is 2', async ({ page }) => {
    await expect(page.locator('span').filter({ hasText: /^2 findings$/ })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: SQL_TITLE })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: XSS_TITLE })).toBeVisible()
  })

  // ---------------------------------------------------------------------------
  // 3. Typing fires API request after debounce; results replace findings
  // ---------------------------------------------------------------------------

  test('typing triggers API search after debounce and replaces findings with 3 results', async ({ page }) => {
    const input = page.locator(SEARCH_INPUT)

    const requestPromise = page.waitForRequest(
      (req) =>
        req.url().includes(`/api/experiments/${EXPERIMENT_ID}/findings/search`) &&
        req.url().includes('q=injection'),
    )

    await input.fill('injection')

    const req = await requestPromise
    expect(req.url()).toContain(`/api/experiments/${EXPERIMENT_ID}/findings/search?q=injection`)

    await page.waitForResponse((res) =>
      res.url().includes(`/api/experiments/${EXPERIMENT_ID}/findings/search`),
    )

    await expect(page.locator('span').filter({ hasText: /^3 findings$/ })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: SQL_TITLE })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: XSS_TITLE })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: PATH_TITLE })).toBeVisible()
  })

  // ---------------------------------------------------------------------------
  // 4. Rapid typing fires only one request (debounce)
  // ---------------------------------------------------------------------------

  test('rapid typing fires exactly one API request due to debounce', async ({ page }) => {
    let count = 0
    await page.route('**/api/experiments/*/findings/search*', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      count++
      return route.fallback()
    })

    const input = page.locator(SEARCH_INPUT)
    await input.pressSequentially('search', { delay: 0 })

    await page.waitForResponse((res) =>
      res.url().includes(`/api/experiments/${EXPERIMENT_ID}/findings/search`),
    )

    expect(count).toBe(1)
  })

  // ---------------------------------------------------------------------------
  // 5. Loading spinner appears during in-flight request
  // ---------------------------------------------------------------------------

  test('spinner is visible while request is in-flight', async ({ page }) => {
    await page.route('**/api/experiments/*/findings/search*', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      await new Promise((r) => setTimeout(r, 1500))
      return route.fallback()
    })

    const input = page.locator(SEARCH_INPUT)
    await input.fill('x')

    const iconArea = page.locator('div.absolute.right-3')
    await expect(iconArea.locator('svg.animate-spin')).toBeVisible()

    await page.waitForResponse((res) =>
      res.url().includes(`/api/experiments/${EXPERIMENT_ID}/findings/search`),
    )

    await expect(iconArea.locator('svg.animate-spin')).toHaveCount(0)
  })

  // ---------------------------------------------------------------------------
  // 6. Clear button appears when query is non-empty and not loading
  // ---------------------------------------------------------------------------

  test('clear button appears after response and search icon is gone', async ({ page }) => {
    const input = page.locator(SEARCH_INPUT)
    await input.fill('z')

    await page.waitForResponse((res) =>
      res.url().includes(`/api/experiments/${EXPERIMENT_ID}/findings/search`),
    )

    await expect(page.getByRole('button', { name: 'Clear search' })).toBeVisible()

    const iconArea = page.locator('div.absolute.right-3')
    // Clear button is rendered as text (×), so no SVG should be present in the icon area
    await expect(iconArea.locator('svg')).toHaveCount(0)
  })

  // ---------------------------------------------------------------------------
  // 7. Clicking clear restores initial findings and resets input
  // ---------------------------------------------------------------------------

  test('clicking clear button resets input, restores 2 initial findings, and shows search icon', async ({ page }) => {
    const input = page.locator(SEARCH_INPUT)
    await input.fill('z')

    await page.waitForResponse((res) =>
      res.url().includes(`/api/experiments/${EXPERIMENT_ID}/findings/search`),
    )

    await expect(page.locator('span').filter({ hasText: /^3 findings$/ })).toBeVisible()

    await page.getByRole('button', { name: 'Clear search' }).click()

    await expect(input).toHaveValue('')
    await expect(page.locator('span').filter({ hasText: /^2 findings$/ })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: SQL_TITLE })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: XSS_TITLE })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: PATH_TITLE })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Clear search' })).toHaveCount(0)
    const iconArea = page.locator('div.absolute.right-3')
    await expect(iconArea.locator('svg')).toHaveCount(1)
  })

  // ---------------------------------------------------------------------------
  // 8. Empty input does NOT trigger an API request
  // ---------------------------------------------------------------------------

  test('typing then clearing back to empty does not fire an API request', async ({ page }) => {
    let count = 0
    await page.route('**/api/experiments/*/findings/search*', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      count++
      return route.fallback()
    })

    const input = page.locator(SEARCH_INPUT)
    await input.fill('abc')
    await input.fill('')

    await page.waitForTimeout(500)

    expect(count).toBe(0)
  })

  // ---------------------------------------------------------------------------
  // 9. API error falls back to initial findings
  // ---------------------------------------------------------------------------

  test('API error on search falls back to initial 2 findings', async ({ page }) => {
    await page.route('**/api/experiments/*/findings/search*', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'internal server error' }),
      })
    })

    const input = page.locator(SEARCH_INPUT)
    await input.fill('x')

    await page.waitForResponse((res) =>
      res.url().includes(`/api/experiments/${EXPERIMENT_ID}/findings/search`),
    )

    await expect(page.locator('span').filter({ hasText: /^2 findings$/ })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: SQL_TITLE })).toBeVisible()
    await expect(page.getByRole('row').filter({ hasText: XSS_TITLE })).toBeVisible()
  })
})
