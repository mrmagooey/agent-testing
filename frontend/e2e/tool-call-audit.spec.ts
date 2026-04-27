import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'
const BASE_URL = `/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`
const RUN_ENDPOINT = `**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`

function loadRunFull() {
  return JSON.parse(readFileSync(join(__dirname, 'fixtures/run-full.json'), 'utf-8'))
}

async function overrideToolCalls(page: import('@playwright/test').Page, toolCalls: unknown[]) {
  await page.route(RUN_ENDPOINT, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const base = loadRunFull()
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ...base, tool_calls: toolCalls }),
    })
  })
}

test.describe('Tool Call Audit on RunDetail', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
  })

  test('heading shows the count: Tool Call Audit (2) for default fixture', async ({ page }) => {
    await page.goto(BASE_URL)
    await expect(page.getByRole('heading', { name: /Tool Call Audit \(2\)/ })).toBeVisible()
  })

  test('both default-fixture rows render with tool names and inputs', async ({ page }) => {
    await page.goto(BASE_URL)

    const auditHeading = page.getByRole('heading', { name: /Tool Call Audit/ })
    const auditSection = auditHeading.locator('xpath=..')

    await expect(auditSection.getByText('read_file')).toBeVisible()
    await expect(auditSection.getByText('search_code')).toBeVisible()

    // Input previews — these are short enough that no truncation occurs
    await expect(auditSection.getByText('{"path":"src/auth/login.py"}')).toBeVisible()
    await expect(auditSection.getByText('{"query":"SELECT","path":"src/"}')).toBeVisible()
  })

  test('default-fixture rows are NOT flagged', async ({ page }) => {
    await page.goto(BASE_URL)

    const auditHeading = page.getByRole('heading', { name: /Tool Call Audit/ })
    const auditSection = auditHeading.locator('xpath=..')

    const rows = auditSection.locator('tbody tr')
    await expect(rows).toHaveCount(2)

    // No row should carry the red background class
    const row0 = rows.nth(0)
    const row1 = rows.nth(1)
    await expect(row0).not.toHaveClass(/bg-red-50/)
    await expect(row1).not.toHaveClass(/bg-red-50/)

    // The warning badge must not appear at all
    await expect(auditSection.getByText('⚠ URL')).toHaveCount(0)
  })

  test('URL in input auto-flags the row', async ({ page }) => {
    // The flagged logic checks /https?:\/\//.test(inputStr) — an http(s) URL in the
    // serialized input is sufficient to trigger the flag even when tc.flagged is false.
    await overrideToolCalls(page, [
      {
        tool_name: 'fetch_url',
        input: { url: 'https://example.com/api/data' },
        timestamp: '2026-04-17T08:02:15Z',
        flagged: false,
      },
    ])
    await page.goto(BASE_URL)

    const auditHeading = page.getByRole('heading', { name: /Tool Call Audit/ })
    const auditSection = auditHeading.locator('xpath=..')

    const rows = auditSection.locator('tbody tr')
    await expect(rows).toHaveCount(1)

    await expect(rows.nth(0)).toHaveClass(/bg-red-50/)
    await expect(auditSection.getByText('⚠ URL')).toBeVisible()
  })

  test('tc.flagged === true flags the row even without a URL', async ({ page }) => {
    await overrideToolCalls(page, [
      {
        tool_name: 'write_file',
        input: { path: '/etc/passwd', content: 'root::0:0:root:/root:/bin/sh' },
        timestamp: '2026-04-17T08:02:15Z',
        flagged: true,
      },
    ])
    await page.goto(BASE_URL)

    const auditHeading = page.getByRole('heading', { name: /Tool Call Audit/ })
    const auditSection = auditHeading.locator('xpath=..')

    const rows = auditSection.locator('tbody tr')
    await expect(rows).toHaveCount(1)

    await expect(rows.nth(0)).toHaveClass(/bg-red-50/)
    await expect(auditSection.getByText('⚠ URL')).toBeVisible()
  })

  test('long input is truncated to 100 chars + ellipsis; title attribute holds full JSON', async ({ page }) => {
    // Build an input whose JSON serialization exceeds 100 characters
    const longInput = {
      level1: {
        level2: {
          level3: {
            value: 'a string long enough to push the serialized json past one hundred characters total',
          },
        },
      },
    }
    const fullJson = JSON.stringify(longInput)
    // Verify the fixture actually exceeds 100 chars before the test runs
    expect(fullJson.length).toBeGreaterThan(100)

    await overrideToolCalls(page, [
      {
        tool_name: 'deep_call',
        input: longInput,
        timestamp: '2026-04-17T08:02:15Z',
        flagged: false,
      },
    ])
    await page.goto(BASE_URL)

    const auditHeading = page.getByRole('heading', { name: /Tool Call Audit/ })
    const auditSection = auditHeading.locator('xpath=..')

    // The input <td> shows sliced text + ellipsis
    const inputCell = auditSection.locator('tbody tr td').nth(1)
    await expect(inputCell).toBeVisible()

    const visibleText = await inputCell.textContent()
    const expectedPreview = fullJson.slice(0, 100) + '…'
    expect(visibleText).toBe(expectedPreview)

    // The title attribute carries the untruncated JSON
    const titleAttr = await inputCell.getAttribute('title')
    expect(titleAttr).toBe(fullJson)
  })

  test('clicking a row expands the JSON in a CodeViewer', async ({ page }) => {
    await page.goto(BASE_URL)

    const auditHeading = page.getByRole('heading', { name: /Tool Call Audit/ })
    const auditSection = auditHeading.locator('xpath=..')

    // Before clicking no CodeMirror editor should be present in the audit section
    await expect(auditSection.locator('.cm-editor')).toHaveCount(0)

    const firstRow = auditSection.locator('tbody tr').nth(0)
    await firstRow.click()

    // After clicking the expanded row with colspan=4 should contain a cm-editor
    await expect(auditSection.locator('.cm-editor')).toHaveCount(1)
  })

  test('clicking an expanded row again collapses it', async ({ page }) => {
    await page.goto(BASE_URL)

    const auditHeading = page.getByRole('heading', { name: /Tool Call Audit/ })
    const auditSection = auditHeading.locator('xpath=..')

    const firstRow = auditSection.locator('tbody tr').nth(0)

    await firstRow.click()
    await expect(auditSection.locator('.cm-editor')).toHaveCount(1)

    await firstRow.click()
    await expect(auditSection.locator('.cm-editor')).toHaveCount(0)
  })

  test('single-row expansion: clicking a different row collapses the previous', async ({ page }) => {
    await page.goto(BASE_URL)

    const auditHeading = page.getByRole('heading', { name: /Tool Call Audit/ })
    const auditSection = auditHeading.locator('xpath=..')

    const bodyRows = auditSection.locator('tbody tr')

    // Click row 0 (read_file) — it should expand
    await bodyRows.nth(0).click()
    await expect(auditSection.locator('.cm-editor')).toHaveCount(1)

    // Click row 1 (search_code) — row 0 should collapse, row 1 should expand
    // After row 0 expands, tbody has an extra expansion row; row 1 (search_code data)
    // is now at index 2. Use the tool-name cell to locate the right row instead.
    const searchCodeRow = auditSection.locator('tbody tr').filter({ hasText: 'search_code' }).first()
    await searchCodeRow.click()

    // Only one editor should be visible at any time
    await expect(auditSection.locator('.cm-editor')).toHaveCount(1)
  })
})
