import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const DATASET_NAME = 'cve-2024-python'

test.describe('FileTree on DatasetDetail', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto(`/datasets/${DATASET_NAME}`)
  })

  // Scope all assertions to the side-panel Files heading parent so we never
  // accidentally match text in the labels table or an open modal.
  function getFilesPanel(page: import('@playwright/test').Page) {
    return page.locator('div').filter({ has: page.getByRole('heading', { name: 'Files' }) }).first()
  }

  test('top-level directories src and tests are visible and expanded', async ({ page }) => {
    const panel = getFilesPanel(page)
    const srcBtn = panel.getByRole('button', { name: /📁.*src/ })
    const testsBtn = panel.getByRole('button', { name: /📁.*tests/ })

    await expect(srcBtn).toBeVisible()
    await expect(testsBtn).toBeVisible()

    // Expanded directories show ▼
    await expect(srcBtn).toContainText('▼')
    await expect(testsBtn).toContainText('▼')
  })

  test('nested directories inside src are visible but collapsed', async ({ page }) => {
    const panel = getFilesPanel(page)

    // depth-1 dirs inside src should be visible (src is expanded) but collapsed
    for (const dir of ['auth', 'api', 'files', 'search']) {
      const btn = panel.getByRole('button', { name: new RegExp(`📁.*${dir}`) })
      await expect(btn).toBeVisible()
      await expect(btn).toContainText('▶')
    }

    // Their children should NOT be visible yet
    await expect(panel.locator('button', { hasText: 'login.py' })).toHaveCount(0)
    await expect(panel.locator('button', { hasText: 'users.py' })).toHaveCount(0)
    await expect(panel.locator('button', { hasText: 'download.py' })).toHaveCount(0)
    await expect(panel.locator('button', { hasText: 'results.py' })).toHaveCount(0)
  })

  test('top-level files inside tests are visible', async ({ page }) => {
    const panel = getFilesPanel(page)

    // tests is depth-0 and expanded; its children are depth-1 files
    await expect(panel.locator('button', { hasText: 'test_auth.py' })).toBeVisible()
    await expect(panel.locator('button', { hasText: 'test_api.py' })).toBeVisible()
  })

  test('clicking a collapsed directory expands it and reveals its children', async ({ page }) => {
    const panel = getFilesPanel(page)
    const authBtn = panel.getByRole('button', { name: /📁.*auth/ })

    await authBtn.click()

    // Indicator flips to ▼
    await expect(authBtn).toContainText('▼')

    // Children now appear
    await expect(panel.locator('button', { hasText: 'login.py' })).toBeVisible()
    await expect(panel.locator('button', { hasText: 'logout.py' })).toBeVisible()
  })

  test('clicking an expanded top-level directory collapses it and hides its children', async ({ page }) => {
    const panel = getFilesPanel(page)
    const srcBtn = panel.getByRole('button', { name: /📁.*src/ })

    // src starts expanded — click to collapse
    await srcBtn.click()

    // Indicator flips to ▶
    await expect(srcBtn).toContainText('▶')

    // Nested dirs are no longer visible
    for (const dir of ['auth', 'api', 'files', 'search']) {
      await expect(panel.getByRole('button', { name: new RegExp(`📁.*${dir}`) })).toHaveCount(0)
    }
  })

  test('clicking a file updates the viewer panel and highlights the file row', async ({ page }) => {
    const panel = getFilesPanel(page)

    // Expand auth to reveal login.py
    await panel.getByRole('button', { name: /📁.*auth/ }).click()
    const loginBtn = panel.locator('button', { hasText: 'login.py' })
    await loginBtn.click()

    // Viewer panel shows the selected file path in its <p className="text-xs font-mono ...">
    // header (distinct from the labels table row, which renders the same string in a <td>).
    const viewerPathHeader = page.locator('p.text-xs.font-mono').filter({ hasText: 'src/auth/login.py' })
    await expect(viewerPathHeader).toBeVisible()

    await expect(page.getByText('Select a file to view')).not.toBeVisible()

    // The login.py button has the blue-highlight class
    await expect(loginBtn).toHaveClass(/bg-blue-100|bg-blue-900/)
  })

  test('label-count badge appears on files that have labels', async ({ page }) => {
    const panel = getFilesPanel(page)

    // Expand auth to reveal login.py (which has 1 label in the fixture)
    await panel.getByRole('button', { name: /📁.*auth/ }).click()
    const loginBtn = panel.locator('button', { hasText: 'login.py' })
    await expect(loginBtn).toBeVisible()

    // Badge is a span with bg-red-100 (light) or bg-red-900 (dark) containing "1"
    const badge = loginBtn.locator('.bg-red-100, .bg-red-900')
    await expect(badge).toBeVisible()
    await expect(badge).toContainText('1')
  })

  test('no label-count badge on files without labels', async ({ page }) => {
    const panel = getFilesPanel(page)

    // Expand auth — logout.py has no labels
    await panel.getByRole('button', { name: /📁.*auth/ }).click()
    const logoutBtn = panel.locator('button', { hasText: 'logout.py' })
    await expect(logoutBtn).toBeVisible()

    // Must not contain a red badge span
    await expect(logoutBtn.locator('.bg-red-100, .bg-red-900')).toHaveCount(0)
  })

  test('directories are sorted before files at every level', async ({ page }) => {
    const panel = getFilesPanel(page)

    // At the root level, src and tests are both directories — confirm no 📄 appears
    // before any 📁 inside the panel. Get all buttons in DOM order.
    const buttons = panel.getByRole('button')
    const allTexts = await buttons.allInnerTexts()

    let seenFile = false
    for (const text of allTexts) {
      const isDir = text.includes('📁')
      const isFile = text.includes('📄')
      if (isFile) seenFile = true
      // A directory appearing after a file at the same nesting would violate sort order.
      // At root level (depth 0) only dirs appear, so this catches regressions simply.
      if (isDir && seenFile) {
        // Only fail if both are at the same depth — approximate by checking indentation
        // is not deeper (i.e., a subdirectory after a top-level file is fine).
        // For our fixture, root only has dirs so this should never fire.
        throw new Error(`Directory "${text.trim()}" appeared after a file in the tree`)
      }
    }
  })
})
