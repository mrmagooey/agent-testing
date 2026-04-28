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

// ---------------------------------------------------------------------------
// Global Findings page — AbortController cancellation regression
// ---------------------------------------------------------------------------

test.describe('Global Findings AbortController cancellation', () => {
  test('stale slow request A does not overwrite faster request B result', async ({ page }) => {
    const { mockApi } = await import('./helpers/mockApi')

    // Fixture for the initial page load (and any StrictMode double-invocation):
    // returns 3 items so the page starts in a stable "3 findings" state.
    const fixtureInitial = {
      total: 3,
      limit: 50,
      offset: 0,
      facets: { vuln_class: {}, severity: {}, match_status: {}, model_id: {}, strategy: {}, dataset_name: {} },
      items: [
        {
          finding_id: 'init-001',
          run_id: 'run-001',
          experiment_id: 'exp-001',
          title: 'Initial finding one',
          description: 'Initial',
          vuln_class: 'sqli',
          severity: 'critical',
          match_status: 'tp',
          file_path: 'src/a.py',
          line_start: 1,
          line_end: 2,
          recommendation: '',
          evidence_quality: 'strong',
          matched_label_id: null,
          experiment_name: 'Exp',
          model_id: 'gpt-4o',
          strategy: 'zero_shot',
          dataset_name: 'ds',
          created_at: '2026-01-01T00:00:00Z',
          cwe_ids: [],
        },
        {
          finding_id: 'init-002',
          run_id: 'run-001',
          experiment_id: 'exp-001',
          title: 'Initial finding two',
          description: 'Initial',
          vuln_class: 'xss',
          severity: 'high',
          match_status: 'fp',
          file_path: 'src/b.py',
          line_start: 5,
          line_end: 6,
          recommendation: '',
          evidence_quality: 'adequate',
          matched_label_id: null,
          experiment_name: 'Exp',
          model_id: 'gpt-4o',
          strategy: 'zero_shot',
          dataset_name: 'ds',
          created_at: '2026-01-02T00:00:00Z',
          cwe_ids: [],
        },
        {
          finding_id: 'init-003',
          run_id: 'run-001',
          experiment_id: 'exp-001',
          title: 'Initial finding three',
          description: 'Initial',
          vuln_class: 'ssrf',
          severity: 'medium',
          match_status: 'tp',
          file_path: 'src/c.py',
          line_start: 10,
          line_end: 11,
          recommendation: '',
          evidence_quality: 'strong',
          matched_label_id: null,
          experiment_name: 'Exp',
          model_id: 'gpt-4o',
          strategy: 'zero_shot',
          dataset_name: 'ds',
          created_at: '2026-01-03T00:00:00Z',
          cwe_ids: [],
        },
      ],
    }

    // Fixture A (slow/stale): would clobber fresh results if cancellation is broken.
    const fixtureA = {
      total: 1,
      limit: 50,
      offset: 0,
      facets: { vuln_class: {}, severity: {}, match_status: {}, model_id: {}, strategy: {}, dataset_name: {} },
      items: [
        {
          finding_id: 'stale-001',
          run_id: 'run-001',
          experiment_id: 'exp-001',
          title: 'STALE result should not appear',
          description: 'Stale',
          vuln_class: 'sqli',
          severity: 'critical',
          match_status: 'tp',
          file_path: 'src/stale.py',
          line_start: 1,
          line_end: 5,
          recommendation: '',
          evidence_quality: 'strong',
          matched_label_id: null,
          experiment_name: 'Stale Exp',
          model_id: 'gpt-4o',
          strategy: 'zero_shot',
          dataset_name: 'ds-stale',
          created_at: '2026-01-01T00:00:00Z',
          cwe_ids: [],
        },
      ],
    }

    // Fixture B (fast/fresh): the result we expect to see after cancellation.
    const fixtureB = {
      total: 2,
      limit: 50,
      offset: 0,
      facets: { vuln_class: {}, severity: {}, match_status: {}, model_id: {}, strategy: {}, dataset_name: {} },
      items: [
        {
          finding_id: 'fresh-001',
          run_id: 'run-001',
          experiment_id: 'exp-001',
          title: 'Fresh XSS result',
          description: 'Fresh',
          vuln_class: 'xss',
          severity: 'high',
          match_status: 'fp',
          file_path: 'src/fresh.py',
          line_start: 10,
          line_end: 15,
          recommendation: '',
          evidence_quality: 'adequate',
          matched_label_id: null,
          experiment_name: 'Fresh Exp',
          model_id: 'gpt-4o',
          strategy: 'zero_shot',
          dataset_name: 'ds-fresh',
          created_at: '2026-01-02T00:00:00Z',
          cwe_ids: [],
        },
        {
          finding_id: 'fresh-002',
          run_id: 'run-001',
          experiment_id: 'exp-001',
          title: 'Fresh SSRF result',
          description: 'Fresh',
          vuln_class: 'ssrf',
          severity: 'high',
          match_status: 'tp',
          file_path: 'src/fresh2.py',
          line_start: 20,
          line_end: 25,
          recommendation: '',
          evidence_quality: 'strong',
          matched_label_id: 'label-b',
          experiment_name: 'Fresh Exp',
          model_id: 'gpt-4o',
          strategy: 'zero_shot',
          dataset_name: 'ds-fresh',
          created_at: '2026-01-03T00:00:00Z',
          cwe_ids: [],
        },
      ],
    }

    // Track browser-level request failures (aborted fetches appear here).
    const abortedFindingsUrls: string[] = []
    page.on('requestfailed', (req) => {
      if (req.url().includes('/api/findings')) {
        abortedFindingsUrls.push(req.url())
      }
    })

    // Track all /api/findings request URLs.
    const findingsUrls: string[] = []
    page.on('request', (req) => {
      if (req.url().includes('/api/findings')) {
        findingsUrls.push(req.url())
      }
    })

    await mockApi(page)

    // Route state machine:
    //   - Requests with no q param (initial loads, including StrictMode re-mount):
    //     return fixtureInitial immediately.
    //   - The FIRST request with q=sqli: hold for 1500 ms (slow / stale = request A).
    //   - The FIRST request with q=xss: return fixtureB immediately (fresh = request B).
    let slowFired = false
    await page.route('**/api/findings*', async (route) => {
      const url = new URL(route.request().url())
      const q = url.searchParams.get('q') ?? ''

      if (!q) {
        // Initial / StrictMode loads — fast response with the 3-item fixture.
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(fixtureInitial),
        })
      }

      if (q === 'sqli' && !slowFired) {
        // Request A: slow. Hold for 1500 ms to simulate an in-flight stale request.
        slowFired = true
        await new Promise<void>((r) => setTimeout(r, 1500))
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(fixtureA),
        })
      }

      // All other queries (including q=xss = request B): respond immediately.
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(fixtureB),
      })
    })

    await page.goto('/findings')

    const searchInput = page.locator('input[aria-label="Search findings"]')
    await searchInput.waitFor({ state: 'visible' })

    // Wait for the initial "3 findings" to render so we know the page is stable.
    await expect(page.getByText('3 findings')).toBeVisible({ timeout: 5000 })

    // Trigger filter A (q=sqli) — this will be the slow/stale request A.
    await searchInput.fill('sqli')

    // Before A has responded (it takes 1500 ms), immediately change to q=xss.
    // The debounce is 350 ms, so we wait just long enough for the first debounce
    // to fire and the request A to begin, then change the input again.
    await page.waitForTimeout(450)
    await searchInput.fill('xss')

    // Request B (q=xss) resolves immediately → "2 findings" should appear.
    await expect(page.getByText('2 findings')).toBeVisible({ timeout: 5000 })

    // Verify the fresh B content is present.
    await expect(page.getByText('Fresh XSS result')).toBeVisible()
    await expect(page.getByText('Fresh SSRF result')).toBeVisible()

    // The stale A title must NOT be visible — cancellation prevented it from
    // overwriting the fresh B results even though A resolved after B.
    await expect(page.getByText('STALE result should not appear')).toHaveCount(0)

    // The last /api/findings URL fired should contain q=xss.
    const latestUrl = findingsUrls[findingsUrls.length - 1]
    expect(latestUrl).toContain('q=xss')

    // With the fix wired, the browser actually aborts the slow fetch (A), which
    // registers as a requestfailed event.
    expect(abortedFindingsUrls.length).toBeGreaterThanOrEqual(1)
  })
})
