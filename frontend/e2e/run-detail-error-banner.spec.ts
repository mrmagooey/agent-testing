import { test, expect } from '@playwright/test'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { mockApi } from './helpers/mockApi'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'

const runFullFailed = JSON.parse(
  readFileSync(join(__dirname, 'fixtures/run-full-failed.json'), 'utf-8'),
)

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('banner renders for failed run', async ({ page }) => {
  await page.route(`**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(runFullFailed),
    })
  })

  await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)

  const banner = page.getByTestId('run-error-banner')
  await expect(banner).toBeVisible()
  await expect(banner).toContainText('Run error')
  await expect(banner).toContainText(
    "run_strategy: pydantic-ai produced an unexpected response for strategy 'builtin.single_agent': Exceeded maximum retries (1) for output validation",
  )
})

test('banner has role="alert" for screen readers', async ({ page }) => {
  await page.route(`**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(runFullFailed),
    })
  })

  await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)

  const banner = page.getByTestId('run-error-banner')
  await expect(banner).toBeVisible()
  await expect(banner).toHaveAttribute('role', 'alert')
})

test('banner does NOT render for completed runs', async ({ page }) => {
  const runFullCompleted = JSON.parse(
    readFileSync(join(__dirname, 'fixtures/run-full.json'), 'utf-8'),
  )
  // Ensure status is completed and no error
  const completedRun = { ...runFullCompleted, status: 'completed', error: null }

  await page.route(`**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(completedRun),
    })
  })

  await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)

  await expect(page.getByTestId('run-error-banner')).toHaveCount(0)
})

test('status badge still shows "failed" when error banner is rendered', async ({ page }) => {
  await page.route(`**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(runFullFailed),
    })
  })

  await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)

  await expect(page.getByTestId('run-error-banner')).toBeVisible()
  await expect(page.getByText('failed', { exact: true }).first()).toBeVisible()
})

test('long error string containing newline preserves whitespace and wraps', async ({ page }) => {
  const multilineError =
    "run_strategy: pydantic-ai produced an unexpected response\nExceeded maximum retries (1) for output validation"
  const runWithNewline = { ...runFullFailed, error: multilineError }

  await page.route(`**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(runWithNewline),
    })
  })

  await page.goto(`/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`)

  const banner = page.getByTestId('run-error-banner')
  await expect(banner).toBeVisible()
  // Playwright's textContent collapses whitespace; innerText preserves \n for whitespace-pre-wrap elements
  const innerText = await banner.locator('p.whitespace-pre-wrap').innerText()
  expect(innerText).toContain('\n')
})
