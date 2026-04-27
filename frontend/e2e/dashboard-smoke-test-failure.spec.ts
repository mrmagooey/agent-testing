import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const SMOKE_EXPERIMENT_ID = 'smoke-test-experiment-id-0000000000001'

async function failSmokeTest(
  page: import('@playwright/test').Page,
  status: number,
  body: unknown,
  contentType = 'application/json',
) {
  await page.route('**/api/smoke-test', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const bodyStr = contentType === 'application/json' ? JSON.stringify(body) : String(body)
    await route.fulfill({ status, contentType, body: bodyStr })
  })
}

test.describe('Dashboard smoke-test failure path', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  test('422 with JSON detail shows error banner and re-enables button', async ({ page }) => {
    const detail = 'No datasets available; create or import one first.'
    await failSmokeTest(page, 422, { detail })
    await page.goto('/')

    await page.getByRole('button', { name: 'Run Smoke Test' }).click()

    await expect(page.getByText(detail)).toBeVisible()
    await expect(page.getByRole('link', { name: 'View experiment →' })).toHaveCount(0)

    const btn = page.getByRole('button', { name: 'Run Smoke Test' })
    await expect(btn).toBeVisible()
    await expect(btn).toBeEnabled()
  })

  test('500 with JSON detail shows detail message in error banner', async ({ page }) => {
    const detail = 'Coordinator timed out while scheduling smoke test'
    await failSmokeTest(page, 500, { detail })
    await page.goto('/')

    await page.getByRole('button', { name: 'Run Smoke Test' }).click()

    await expect(page.getByText(detail)).toBeVisible()
  })

  test('400 with message field (no detail) shows message in error banner', async ({ page }) => {
    const message = 'Smoke test feature is disabled in this build'
    await failSmokeTest(page, 400, { message })
    await page.goto('/')

    await page.getByRole('button', { name: 'Run Smoke Test' }).click()

    await expect(page.getByText(message)).toBeVisible()
  })

  test('503 with non-JSON body shows "API error 503" fallback message', async ({ page }) => {
    await failSmokeTest(page, 503, 'Service Unavailable', 'text/plain')
    await page.goto('/')

    await page.getByRole('button', { name: 'Run Smoke Test' }).click()

    await expect(page.getByText('API error 503')).toBeVisible()
  })

  test('network abort shows non-empty error banner', async ({ page }) => {
    await page.route('**/api/smoke-test', (route) => route.abort('failed'))
    await page.goto('/')

    await page.getByRole('button', { name: 'Run Smoke Test' }).click()

    const smokeSection = page.locator('section').filter({
      has: page.getByRole('button', { name: 'Run Smoke Test' }),
    })
    const banner = smokeSection.locator('div.text-signal-danger.font-mono')
    await expect(banner).toBeVisible()
    const text = await banner.textContent()
    expect(text?.trim().length).toBeGreaterThan(0)
  })

  test('failure then retry: error banner replaced by success banner', async ({ page }) => {
    const firstDetail = 'First error'
    await failSmokeTest(page, 500, { detail: firstDetail })
    await page.goto('/')

    await page.getByRole('button', { name: 'Run Smoke Test' }).click()
    await expect(page.getByText(firstDetail)).toBeVisible()

    // Remove the failure override so the global mockApi success handler takes over.
    await page.unroute('**/api/smoke-test')

    await page.getByRole('button', { name: 'Run Smoke Test' }).click()

    await expect(page.getByText(firstDetail)).toHaveCount(0)
    const viewLink = page.getByRole('link', { name: 'View experiment →' })
    await expect(viewLink).toBeVisible()
    const href = await viewLink.getAttribute('href')
    expect(href).toContain(`/experiments/${SMOKE_EXPERIMENT_ID}`)
  })

  test('fresh navigation after failure resets to idle (no stale error banner)', async ({ page }) => {
    await failSmokeTest(page, 422, { detail: 'Transient failure' })
    await page.goto('/')

    await page.getByRole('button', { name: 'Run Smoke Test' }).click()
    await expect(page.getByText('Transient failure')).toBeVisible()

    // Navigate away and back — component-local state should be gone.
    await page.goto('/')
    await expect(page.getByText('Transient failure')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Run Smoke Test' })).toBeEnabled()
  })
})
