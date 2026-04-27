/**
 * E2E tests for dataset injection error paths.
 *
 * Covers preview and inject failures: 4xx/5xx HTTP errors, non-JSON bodies,
 * network aborts, and recovery via page reload.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const DATASET_NAME = 'cve-2024-python'
const TEMPLATE_VULN_CLASS = 'sqli'
const PLACEHOLDER_VALUE = 'test_payload'

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

// Verbatim copy from dataset-inject.spec.ts — the file-tree navigation is fragile.
async function navigateToStep3(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
  await page.getByRole('button', { name: new RegExp(TEMPLATE_VULN_CLASS) }).first().click()
  await expect(page.getByText('Inject Vulnerability — Step 2/5')).toBeVisible()

  // depth-0 "src" is already expanded; expand depth-1 "auth" then click leaf
  const modalFileTree = page.locator('.fixed.inset-0')
  await modalFileTree.getByRole('button', { name: /📁 auth/ }).click()
  await modalFileTree.locator('button', { hasText: 'login.py' }).click()

  await expect(page.getByText('Inject Vulnerability — Step 3/5')).toBeVisible()
  await page.locator('.fixed.inset-0').locator('input[type="text"]').fill(PLACEHOLDER_VALUE)
}

async function failPreview(
  page: import('@playwright/test').Page,
  status: number,
  body: unknown,
  contentType = 'application/json',
) {
  await page.route(`**/api/datasets/${DATASET_NAME}/inject/preview`, async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const bodyStr = contentType === 'application/json' ? JSON.stringify(body) : String(body)
    await route.fulfill({ status, contentType, body: bodyStr })
  })
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

test.describe('Dataset injection error paths', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  // -------------------------------------------------------------------------
  // 1. Preview 422 with detail
  // -------------------------------------------------------------------------
  test('preview 422 with detail short-circuits page to red error block', async ({ page }) => {
    const detail = 'Template anchor not found in target file'
    await failPreview(page, 422, { detail })
    await page.goto(`/datasets/${DATASET_NAME}`)
    await navigateToStep3(page)

    await page.getByRole('button', { name: 'Preview Injection' }).click()

    // The page-level error block is the only content rendered when error is set.
    // The wizard modal is gone and dataset content short-circuits.
    await expect(page.getByText(detail)).toBeVisible()
    await expect(page.getByText('Inject Vulnerability — Step 4/5')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Inject Vulnerability' })).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // 2. Preview 500 with detail
  // -------------------------------------------------------------------------
  test('preview 500 with detail shows detail text and short-circuits page', async ({ page }) => {
    const detail = 'Coordinator failed to materialize the template'
    await failPreview(page, 500, { detail })
    await page.goto(`/datasets/${DATASET_NAME}`)
    await navigateToStep3(page)

    await page.getByRole('button', { name: 'Preview Injection' }).click()

    await expect(page.getByText(detail)).toBeVisible()
    await expect(page.getByText('Inject Vulnerability — Step 4/5')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Inject Vulnerability' })).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // 3. Preview 400 with message field (no detail)
  // -------------------------------------------------------------------------
  test('preview 400 with message field surfaces message text', async ({ page }) => {
    const message = 'Substitution placeholder mismatch'
    await failPreview(page, 400, { message })
    await page.goto(`/datasets/${DATASET_NAME}`)
    await navigateToStep3(page)

    await page.getByRole('button', { name: 'Preview Injection' }).click()

    await expect(page.getByText(message)).toBeVisible()
    await expect(page.getByText('Inject Vulnerability — Step 4/5')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // 4. Preview returns non-JSON body (503)
  // -------------------------------------------------------------------------
  test('preview 503 with non-JSON body shows "API error 503" fallback', async ({ page }) => {
    await failPreview(page, 503, 'Service Unavailable', 'text/plain')
    await page.goto(`/datasets/${DATASET_NAME}`)
    await navigateToStep3(page)

    await page.getByRole('button', { name: 'Preview Injection' }).click()

    await expect(page.getByText('API error 503')).toBeVisible()
    await expect(page.getByText('Inject Vulnerability — Step 4/5')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // 5. Inject (confirm) returns 422
  // -------------------------------------------------------------------------
  test('inject 422 with detail short-circuits page and hides step 5', async ({ page }) => {
    const detail = 'Concurrent edit collision; refresh and retry'

    // Let the preview succeed (default mockApi handler); override only the confirm endpoint.
    // The URL **/inject does NOT include a trailing /preview wildcard, so it will not
    // match the /inject/preview path — Playwright globs do not extend beyond the given suffix.
    await page.route(`**/api/datasets/${DATASET_NAME}/inject`, async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      await route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({ detail }),
      })
    })

    await page.goto(`/datasets/${DATASET_NAME}`)
    await navigateToStep3(page)

    await page.getByRole('button', { name: 'Preview Injection' }).click()
    await expect(page.getByText('Inject Vulnerability — Step 4/5')).toBeVisible()

    await page.getByRole('button', { name: /Confirm & Inject/ }).click()

    await expect(page.getByText(detail)).toBeVisible()
    await expect(page.getByText('Inject Vulnerability — Step 5/5')).toHaveCount(0)
    await expect(page.getByText('Injection successful!')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // 6. Inject network abort
  // -------------------------------------------------------------------------
  test('inject network abort shows non-empty error text', async ({ page }) => {
    // Preview succeeds (default mock); abort the confirm POST.
    await page.route(`**/api/datasets/${DATASET_NAME}/inject`, async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      await route.abort('failed')
    })

    await page.goto(`/datasets/${DATASET_NAME}`)
    await navigateToStep3(page)

    await page.getByRole('button', { name: 'Preview Injection' }).click()
    await expect(page.getByText('Inject Vulnerability — Step 4/5')).toBeVisible()

    await page.getByRole('button', { name: /Confirm & Inject/ }).click()

    // The exact abort message is browser-specific; assert it is non-empty.
    const errorBlock = page.locator(
      'div.rounded-lg.border.border-red-200, div.rounded-lg.border.border-red-800',
    ).first()
    await expect(errorBlock).toBeVisible()
    const text = await errorBlock.textContent()
    expect(text?.trim().length).toBeGreaterThan(0)
  })

  // -------------------------------------------------------------------------
  // 7. Recovery via page reload
  // -------------------------------------------------------------------------
  test('reloading after a failed preview restores the dataset detail page', async ({ page }) => {
    const detail = 'Template anchor not found in target file'

    // Register the failing route for only one request, then it expires.
    // After reload, the default mockApi preview handler is used instead.
    let callCount = 0
    await page.route(`**/api/datasets/${DATASET_NAME}/inject/preview`, async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      callCount++
      if (callCount === 1) {
        await route.fulfill({
          status: 422,
          contentType: 'application/json',
          body: JSON.stringify({ detail }),
        })
      } else {
        return route.fallback()
      }
    })

    await page.goto(`/datasets/${DATASET_NAME}`)
    await navigateToStep3(page)
    await page.getByRole('button', { name: 'Preview Injection' }).click()
    await expect(page.getByText(detail)).toBeVisible()

    // Unregister the failing override before reload so the default mock takes over.
    await page.unroute(`**/api/datasets/${DATASET_NAME}/inject/preview`)

    await page.reload()

    // Dataset detail content is restored; error block is gone.
    await expect(page.getByRole('button', { name: 'Inject Vulnerability' })).toBeVisible()
    await expect(page.getByText(detail)).toHaveCount(0)
  })
})
