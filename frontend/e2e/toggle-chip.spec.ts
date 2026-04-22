import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// ToggleChip integration tests
// ---------------------------------------------------------------------------

test.describe('ToggleChip on /experiments/new', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto('/experiments/new')
  })

  test('checking a model chip marks the checkbox as checked', async ({ page }) => {
    // gpt-4o is rendered as a Command.Item in the ModelSearchPicker
    const gpt4oItem = page.getByRole('option', { name: /gpt-4o/i })

    // Initially, the item should not be selected
    await expect(gpt4oItem).toHaveAttribute('data-selected', 'false')

    // Click to select
    await gpt4oItem.click()

    // After selection, the item should have data-selected='true'
    await expect(gpt4oItem).toHaveAttribute('data-selected', 'true')
  })

  test('checked model chip label has indigo background class', async ({ page }) => {
    // gpt-4o is rendered as a Command.Item in the ModelSearchPicker
    const gpt4oItem = page.getByRole('option', { name: /gpt-4o/i })

    // Click to select
    await gpt4oItem.click()

    // After selection, the item shows indigo text styling (text-indigo-700)
    // This is the visual indicator of selection in the new UI
    await expect(gpt4oItem).toHaveClass(/text-indigo-/)
  })

  test('unchecked model chip label does not have indigo background', async ({ page }) => {
    // gpt-4o is rendered as a Command.Item in the ModelSearchPicker
    const gpt4oItem = page.getByRole('option', { name: /gpt-4o/i })

    // Before any interaction, the item should not be selected
    await expect(gpt4oItem).not.toHaveAttribute('data-selected', 'true')

    // An unselected item should not have indigo text styling
    await expect(gpt4oItem).not.toHaveClass(/text-indigo-/)
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
