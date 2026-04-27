import { test, expect } from '@playwright/test'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { mockApi } from './helpers/mockApi'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const EXP_A = 'aaaaaaaa-0001-0001-0001-000000000001'
const EXP_B = 'bbbbbbbb-0002-0002-0002-000000000002'
const RUN_A = 'run-001-aaa'
const RUN_B = 'run-002-bbb'

const DEEP_LINK = `/compare?a_experiment=${EXP_A}&a_run=${RUN_A}&b_experiment=${EXP_B}&b_run=${RUN_B}`

async function overrideComparison(page: import('@playwright/test').Page, partial: Record<string, unknown>) {
  const base = JSON.parse(readFileSync(join(__dirname, 'fixtures/comparison.json'), 'utf-8'))
  const merged = { ...base, ...partial }
  await page.route('**/api/compare-runs*', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(merged),
    })
  })
}

test.describe('RunCompare extras: mismatch banner + experiment link', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  // 1. Banner appears when dataset_mismatch === true
  test('DatasetMismatchBanner is visible when dataset_mismatch is true', async ({ page }) => {
    await overrideComparison(page, {
      dataset_mismatch: true,
      warnings: ['Run A dataset: cve-2024-python', 'Run B dataset: cve-2024-java'],
    })
    await page.goto(DEEP_LINK)

    const banner = page.getByTestId('dataset-mismatch-banner')
    await expect(banner).toBeVisible()
    await expect(banner).toHaveAttribute('role', 'alert')
    await expect(banner.getByText('Dataset mismatch')).toBeVisible()
    await expect(banner.getByText('Run A dataset: cve-2024-python')).toBeVisible()
    await expect(banner.getByText('Run B dataset: cve-2024-java')).toBeVisible()
  })

  // 2. Banner is absent when dataset_mismatch === false
  test('DatasetMismatchBanner is absent when dataset_mismatch is false', async ({ page }) => {
    await overrideComparison(page, {
      dataset_mismatch: false,
      warnings: ['Run A dataset: cve-2024-python', 'Run B dataset: cve-2024-java'],
    })
    await page.goto(DEEP_LINK)

    // Comparison view must have loaded before we assert absence
    await expect(page.getByRole('heading', { name: 'Run A' })).toBeVisible()
    await expect(page.getByTestId('dataset-mismatch-banner')).toHaveCount(0)
  })

  // 3. Banner is absent when dataset_mismatch field is omitted entirely (default fixture)
  test('DatasetMismatchBanner is absent when dataset_mismatch field is missing', async ({ page }) => {
    // No overrideComparison call — mockApi serves the default comparison.json which
    // has no dataset_mismatch field. Falsy → no banner rendered.
    await page.goto(DEEP_LINK)

    await expect(page.getByRole('heading', { name: 'Run A' })).toBeVisible()
    await expect(page.getByTestId('dataset-mismatch-banner')).toHaveCount(0)
  })

  // 4. Multiple warnings each render as a separate <p>, heading counts as first <p>
  test('three warnings render as three separate paragraphs inside the banner', async ({ page }) => {
    await overrideComparison(page, {
      dataset_mismatch: true,
      warnings: ['Warning alpha', 'Warning beta', 'Warning gamma'],
    })
    await page.goto(DEEP_LINK)

    const banner = page.getByTestId('dataset-mismatch-banner')
    await expect(banner).toBeVisible()
    await expect(banner.getByText('Warning alpha')).toBeVisible()
    await expect(banner.getByText('Warning beta')).toBeVisible()
    await expect(banner.getByText('Warning gamma')).toBeVisible()
    // heading <p> + 3 warning <p> = 4 total
    await expect(banner.locator('p')).toHaveCount(4)
  })

  // 5. Empty warnings array still shows the heading paragraph only
  test('empty warnings array renders banner with only the heading paragraph', async ({ page }) => {
    await overrideComparison(page, {
      dataset_mismatch: true,
      warnings: [],
    })
    await page.goto(DEEP_LINK)

    const banner = page.getByTestId('dataset-mismatch-banner')
    await expect(banner).toBeVisible()
    await expect(banner.getByText('Dataset mismatch')).toBeVisible()
    await expect(banner.locator('p')).toHaveCount(1)
  })

  // 6. Experiment link renders and navigates when run.experiment_name is set
  test('Experiment link in Run A card navigates to the experiment detail page', async ({ page }) => {
    const base = JSON.parse(readFileSync(join(__dirname, 'fixtures/comparison.json'), 'utf-8'))
    const merged = {
      ...base,
      run_a: {
        ...base.run_a,
        experiment_name: 'GPT-4o Zero-Shot April 2026',
        experiment_id: EXP_A,
      },
    }
    await page.route('**/api/compare-runs*', async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(merged),
      })
    })

    await page.goto(DEEP_LINK)

    // Scope link lookup to the Run A card via its heading
    const runACard = page.getByRole('heading', { name: 'Run A' }).locator('../..')
    const expLink = runACard.getByRole('link', { name: 'GPT-4o Zero-Shot April 2026' })
    await expect(expLink).toBeVisible()
    await expect(expLink).toHaveAttribute('href', `/experiments/${EXP_A}`)

    await expLink.click()
    await page.waitForURL(`**/experiments/${EXP_A}`)
    expect(page.url()).toContain(`/experiments/${EXP_A}`)
  })

  // 7. Experiment row is absent when run.experiment_name is null/missing
  test('Experiment row is absent from Run A card when experiment_name is not set', async ({ page }) => {
    // Default fixture has no experiment_name on run_a — the filter(v !== null) drops the row.
    await page.goto(DEEP_LINK)

    await expect(page.getByRole('heading', { name: 'Run A' })).toBeVisible()

    const runACard = page.getByRole('heading', { name: 'Run A' }).locator('../..')
    await expect(runACard.locator('dt').filter({ hasText: /^Experiment$/ })).toHaveCount(0)
  })
})
