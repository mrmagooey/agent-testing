/**
 * Story 65: MatrixTable surfaces failed-run status pill and error in the expand row.
 *
 * Five tests covering:
 *   1. Failed run shows a red status pill with text "failed".
 *   2. Cancelled run shows a gray status pill with text "cancelled".
 *   3. Completed run does NOT show any status pill.
 *   4. Expanding the failed-run row reveals the error message block.
 *   5. Expanding a completed-run row does NOT show an error block.
 */
import { test, expect } from '@playwright/test'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'
import { mockApi } from './helpers/mockApi'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'

const FAILED_RUN_TEXT = 'few_shot'       // unique text in the failed run row (strategy)
const CANCELLED_RUN_TEXT = 'agent'       // unique text in the cancelled run row (strategy)
const COMPLETED_RUN_ID = 'run-001-aaa'  // the first completed run
const COMPLETED_RUN_TEXT = 'zero_shot'  // strategy shared by completed rows — use with_tools variant

const FAILED_ERROR =
  "run_strategy: pydantic-ai produced an unexpected response for strategy 'builtin.single_agent': Exceeded maximum retries (1) for output validation"

const failuresFixture = JSON.parse(
  readFileSync(join(__dirname, 'fixtures/experiment-results-with-failures.json'), 'utf-8'),
)

test.describe('MatrixTable failed-run status pill and error block', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    // Override the shared experiment-results route with a fixture that includes
    // failed + cancelled runs alongside one completed run. Other specs that read
    // the default fixture stay unaffected.
    await page.route(`**/api/experiments/${EXPERIMENT_ID}/results`, async (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(failuresFixture),
      })
    })
    await page.goto(`/experiments/${EXPERIMENT_ID}`)
  })

  test('failed run shows red status pill', async ({ page }) => {
    // Locate the row containing "few_shot" (the failed run's strategy)
    // and "with_tools" to disambiguate from other zero_shot rows
    const failedRow = page.locator('tr').filter({ hasText: FAILED_RUN_TEXT }).first()
    await expect(failedRow).toBeVisible()

    const pill = failedRow.locator('[data-testid="matrix-row-status-pill"]')
    await expect(pill).toBeVisible()
    await expect(pill).toContainText('failed')
  })

  test('cancelled run shows gray status pill', async ({ page }) => {
    // The cancelled run uses strategy "agent" — unique in the fixture
    const cancelledRow = page.locator('tr').filter({ hasText: CANCELLED_RUN_TEXT }).first()
    await expect(cancelledRow).toBeVisible()

    const pill = cancelledRow.locator('[data-testid="matrix-row-status-pill"]')
    await expect(pill).toBeVisible()
    await expect(pill).toContainText('cancelled')
  })

  test('completed run does NOT show a status pill', async ({ page }) => {
    // run-001-aaa: gpt-4o / zero_shot / with_tools — all three completed zero_shot rows
    // share "zero_shot" text; narrow to "with_tools" and gpt-4o to hit run-001.
    // Simpler: target by run_id text which appears in a td of the expanded row isn't
    // available without expanding. Instead filter by distinct combination.
    // run-001-aaa is gpt-4o + zero_shot + with_tools; run-004-failed is gpt-4o + few_shot.
    // Filter to rows with "zero_shot" and "with_tools" and gpt-4o:
    const completedRows = page.locator('tr').filter({ hasText: 'zero_shot' }).filter({ hasText: 'with_tools' })
    // Wait for at least one completed row to render before counting
    await expect(completedRows.first()).toBeVisible()
    const count = await completedRows.count()
    expect(count).toBeGreaterThan(0)
    for (let i = 0; i < count; i++) {
      const pill = completedRows.nth(i).locator('[data-testid="matrix-row-status-pill"]')
      await expect(pill).toHaveCount(0)
    }
  })

  test('expanding the failed-run row reveals the error message', async ({ page }) => {
    const failedRow = page.locator('tr').filter({ hasText: FAILED_RUN_TEXT }).first()
    // Click the expand button inside this row
    const expandBtn = failedRow.locator('button[title="Expand details"]')
    await expandBtn.click()

    const errorBlock = page.locator('[data-testid="matrix-row-error"]')
    await expect(errorBlock).toBeVisible()
    await expect(errorBlock).toContainText(FAILED_ERROR)
  })

  test('expanding a completed-run row does NOT reveal an error block', async ({ page }) => {
    // Expand the first completed run (run-001-aaa): gpt-4o / zero_shot / with_tools
    const completedRow = page
      .locator('tr')
      .filter({ hasText: 'zero_shot' })
      .filter({ hasText: 'with_tools' })
      .first()
    const expandBtn = completedRow.locator('button[title="Expand details"]')
    await expandBtn.click()

    const errorBlock = page.locator('[data-testid="matrix-row-error"]')
    await expect(errorBlock).toHaveCount(0)
  })
})
