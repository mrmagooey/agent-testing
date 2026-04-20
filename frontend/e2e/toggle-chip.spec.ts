import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// ToggleChip integration tests
// ---------------------------------------------------------------------------

test.describe('ToggleChip on /batches/new', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto('/batches/new')
  })

  test('checking a model chip marks the checkbox as checked', async ({ page }) => {
    const gpt4oCheckbox = page
      .locator('label')
      .filter({ hasText: 'gpt-4o' })
      .first()
      .locator('input[type="checkbox"]')

    await expect(gpt4oCheckbox).not.toBeChecked()
    await gpt4oCheckbox.check()
    await expect(gpt4oCheckbox).toBeChecked()
  })

  test('checked model chip label has indigo background class', async ({ page }) => {
    const gpt4oLabel = page.locator('label').filter({ hasText: 'gpt-4o' }).first()
    const gpt4oCheckbox = gpt4oLabel.locator('input[type="checkbox"]')

    await gpt4oCheckbox.check()
    await expect(gpt4oLabel).toHaveClass(/bg-indigo-600/)
  })

  test('unchecked model chip label does not have indigo background', async ({ page }) => {
    const gpt4oLabel = page.locator('label').filter({ hasText: 'gpt-4o' }).first()
    await expect(gpt4oLabel).not.toHaveClass(/bg-indigo-600/)
  })

  test('disabled chip (DevDocs) cannot be toggled', async ({ page }) => {
    const devdocsLabel = page.locator('label').filter({ hasText: 'DevDocs' })
    const devdocsCheckbox = devdocsLabel.locator('input[type="checkbox"]')

    await expect(devdocsCheckbox).toBeDisabled()
    await expect(devdocsCheckbox).not.toBeChecked()

    // Clicking the label (which covers the whole chip) must NOT toggle a
    // disabled chip. Use force to bypass Playwright's own disabled-actionability
    // check — we're asserting the DOM/React behavior, not Playwright's guard.
    await devdocsLabel.click({ force: true })
    await expect(devdocsCheckbox).not.toBeChecked()
  })
})

test.describe('ToggleChip on /datasets/discover', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto('/datasets/discover')
  })

  test('toggling a Language chip marks the checkbox checked', async ({ page }) => {
    const pythonCheckbox = page
      .locator('label')
      .filter({ hasText: /^python$/ })
      .locator('input[type="checkbox"]')

    await expect(pythonCheckbox).not.toBeChecked()
    await pythonCheckbox.check()
    await expect(pythonCheckbox).toBeChecked()
  })

  test('checked language chip label has indigo background class', async ({ page }) => {
    const pythonLabel = page.locator('label').filter({ hasText: /^python$/ })
    const pythonCheckbox = pythonLabel.locator('input[type="checkbox"]')

    await pythonCheckbox.check()
    await expect(pythonLabel).toHaveClass(/bg-indigo-600/)
  })

  test('Any pill and chip pills share the same filled/outlined style contract', async ({ page }) => {
    // "Any" button is active (no filters selected) — should have bg-indigo-600
    const anyBtn = page.getByRole('button', { name: 'Any' }).first()
    await expect(anyBtn).toHaveClass(/bg-indigo-600/)

    // A chip pill (unchecked) should NOT have bg-indigo-600 (outlined style)
    const pythonLabel = page.locator('label').filter({ hasText: /^python$/ })
    await expect(pythonLabel).not.toHaveClass(/bg-indigo-600/)

    // After checking python, the chip becomes filled
    await pythonLabel.locator('input[type="checkbox"]').check()
    await expect(pythonLabel).toHaveClass(/bg-indigo-600/)
  })
})
