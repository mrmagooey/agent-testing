import { test, expect } from '@playwright/test'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { mockApi } from './helpers/mockApi'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const comparisonFixture = JSON.parse(
  readFileSync(join(__dirname, 'fixtures/comparison.json'), 'utf-8'),
)

const EXP_A = 'aaaaaaaa-0001-0001-0001-000000000001'
const EXP_B = 'bbbbbbbb-0002-0002-0002-000000000002'
const RUN_A = 'run-001-aaa'
const RUN_B = 'run-002-bbb'

const DEEP_LINK = `/compare?a_experiment=${EXP_A}&a_run=${RUN_A}&b_experiment=${EXP_B}&b_run=${RUN_B}`

function makeFindings(count: number, prefix: string) {
  return Array.from({ length: count }, (_, i) => ({
    finding_id: `${prefix}-find-${i + 1}`,
    run_id: RUN_A,
    experiment_id: EXP_A,
    title: `${prefix} Finding ${i + 1}`,
    description: `Description for ${prefix} finding ${i + 1}.`,
    vuln_class: 'sqli',
    severity: 'high',
    match_status: 'tp',
    file_path: `src/${prefix}/vuln${i + 1}.py`,
    line_start: 10,
    line_end: 15,
  }))
}

test.describe('RunCompare cross-experiment rendering', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  // 1. No-selection state
  test('no-selection state shows pickers and prompt, no comparison content', async ({ page }) => {
    await page.goto('/compare')

    await expect(page.locator('select').first()).toBeVisible()
    await expect(page.getByText('Select two runs above to compare them.')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Run A' })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Run B' })).toHaveCount(0)
    await expect(page.getByText('Loading comparison...')).toHaveCount(0)
  })

  // 2. Tab switching: Only in A reveals path-traversal finding and hides SQL injection
  test('clicking "Only in A" tab swaps panel content and applies active styling', async ({ page }) => {
    await page.goto(DEEP_LINK)

    // Default tab is "Found by Both" — SQL Injection must be visible
    await expect(page.getByRole('button', { name: /Found by Both \(1\)/ })).toBeVisible()
    await expect(page.getByText('SQL Injection in user login handler')).toBeVisible()

    // Click the Only in A tab
    await page.getByRole('button', { name: /Only in A \(1\)/ }).click()

    // Path traversal finding must now be visible
    await expect(page.getByText('Path traversal in file download endpoint')).toBeVisible()

    // SQL Injection must no longer be in the tab content
    await expect(page.getByText('SQL Injection in user login handler')).toHaveCount(0)

    // Active tab styling
    await expect(page.getByRole('button', { name: /Only in A \(1\)/ })).toHaveClass(/border-amber-600/)
  })

  // 3. Tab switching: Only in B shows empty state
  test('clicking "Only in B" tab shows empty-state message', async ({ page }) => {
    await page.goto(DEEP_LINK)

    await page.getByRole('button', { name: /Only in B \(0\)/ }).click()

    await expect(page.getByText('No findings in this category.')).toBeVisible()

    // None of the known finding titles from the fixture must appear
    await expect(page.getByText('SQL Injection in user login handler')).toHaveCount(0)
    await expect(page.getByText('Path traversal in file download endpoint')).toHaveCount(0)
  })

  // 4. Cross-experiment URL params reach the API with a_experiment ≠ b_experiment
  test('cross-experiment URL params are sent correctly to the API', async ({ page }) => {
    const reqPromise = page.waitForRequest(
      (req) => req.url().includes('/api/compare-runs') && req.method() === 'GET',
    )

    await page.goto(DEEP_LINK)

    const req = await reqPromise
    const url = new URL(req.url())

    expect(url.searchParams.get('a_experiment')).toBe(EXP_A)
    expect(url.searchParams.get('b_experiment')).toBe(EXP_B)
    expect(url.searchParams.get('a_run')).toBe(RUN_A)
    expect(url.searchParams.get('b_run')).toBe(RUN_B)

    // Cross-experiment: the two experiment IDs must differ
    expect(url.searchParams.get('a_experiment')).not.toBe(url.searchParams.get('b_experiment'))
  })

  // 5. Loading state is visible during a delayed fetch
  test('loading state appears while fetch is in flight then disappears', async ({ page }) => {
    await page.route(
      '**/api/compare-runs*',
      async (route) => {
        if (route.request().method() !== 'GET') return route.fallback()
        await new Promise((r) => setTimeout(r, 2000))
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(comparisonFixture),
        })
      },
    )

    await page.goto(DEEP_LINK, { waitUntil: 'domcontentloaded' })

    // Loading text must be present before the response arrives
    await expect(page.getByText('Loading comparison...')).toBeVisible()

    // After the delayed response arrives the loading text must disappear
    await expect(page.getByText('Loading comparison...')).toHaveCount(0, { timeout: 6000 })
    await expect(page.getByRole('button', { name: /Found by Both/ })).toBeVisible()
  })

  // 6. API error renders the red error block with the detail message
  test('API error response renders red error block with detail message', async ({ page }) => {
    await page.route(
      '**/api/compare-runs*',
      async (route) => {
        if (route.request().method() !== 'GET') return route.fallback()
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Comparison engine timed out' }),
        })
      },
    )

    await page.goto(DEEP_LINK)

    await expect(page.getByText('Comparison engine timed out')).toBeVisible()

    // The red error block must be present (border-red-200 / bg-red-50)
    const errorBlock = page.locator('.border-red-200, .border-red-800')
    await expect(errorBlock).toBeVisible()

    // No tab buttons — comparison view must not have rendered
    await expect(page.getByRole('button', { name: /Found by Both/ })).toHaveCount(0)
  })

  // 7. Tab counts reflect the API response, not hardcoded values
  test('tab counts match the lengths returned by the API', async ({ page }) => {
    const customComparison = {
      run_a: {
        run_id: RUN_A,
        experiment_id: EXP_A,
        model: 'gpt-4o',
        strategy: 'zero_shot',
        tool_variant: 'with_tools',
        profile: 'default',
        verification: 'none',
        status: 'completed',
        precision: 0.9,
        recall: 0.85,
        f1: 0.874,
        fpr: 0.03,
        tp_count: 30,
        fp_count: 3,
        fn_count: 5,
        cost_usd: 0.5,
        duration_seconds: 200,
      },
      run_b: {
        run_id: RUN_B,
        experiment_id: EXP_B,
        model: 'claude-3-5-sonnet-20241022',
        strategy: 'chain_of_thought',
        tool_variant: 'without_tools',
        profile: 'default',
        verification: 'none',
        status: 'completed',
        precision: 0.88,
        recall: 0.82,
        f1: 0.849,
        fpr: 0.04,
        tp_count: 28,
        fp_count: 4,
        fn_count: 6,
        cost_usd: 0.45,
        duration_seconds: 180,
      },
      found_by_both: makeFindings(3, 'both'),
      only_in_a: makeFindings(5, 'only-a'),
      only_in_b: makeFindings(2, 'only-b'),
    }

    await page.route(
      '**/api/compare-runs*',
      async (route) => {
        if (route.request().method() !== 'GET') return route.fallback()
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(customComparison),
        })
      },
    )

    await page.goto(DEEP_LINK)

    await expect(page.getByRole('button', { name: /Found by Both \(3\)/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Only in A \(5\)/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /Only in B \(2\)/ })).toBeVisible()
  })
})
