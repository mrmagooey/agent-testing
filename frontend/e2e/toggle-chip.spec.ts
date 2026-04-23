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

  // The ModelSearchPicker renders Command.Items with a deterministic data-value
  // of "{provider} {id} {display_name}"; we address gpt-4o via that attribute
  // so the locator doesn't also resolve to gpt-4o-mini. Selection state is
  // verified via the Pill tray (a "Remove GPT-4o" button appears when selected)
  // and the text-amber-700 class the component adds to selected items — cmdk
  // owns data-selected/aria-selected for keyboard highlighting so those are
  // unreliable signals for application state.
  const GPT4O_ITEM = '[cmdk-item][data-value="openai gpt-4o GPT-4o"]'
  const REMOVE_GPT4O_PILL = { name: 'Remove GPT-4o' } as const

  test('selecting a model from the picker adds it to the pill tray', async ({ page }) => {
    await expect(page.getByRole('button', REMOVE_GPT4O_PILL)).toHaveCount(0)
    await page.locator(GPT4O_ITEM).click()
    await expect(page.getByRole('button', REMOVE_GPT4O_PILL)).toBeVisible()
  })

  test('selected model command item has amber text styling', async ({ page }) => {
    const gpt4oItem = page.locator(GPT4O_ITEM)
    await gpt4oItem.click()
    await expect(gpt4oItem).toHaveClass(/text-amber-/)
  })

  test('unselected model command item does not have amber text styling', async ({ page }) => {
    await expect(page.locator(GPT4O_ITEM)).not.toHaveClass(/text-amber-/)
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

  test('checked language chip label has amber background class', async ({ page }) => {
    const pythonLabel = page.locator('label').filter({ hasText: /^python$/ })
    const pythonCheckbox = pythonLabel.locator('input[type="checkbox"]')

    await pythonCheckbox.check()
    await expect(pythonLabel).toHaveClass(/bg-amber-600/)
  })

  test('Any pill and chip pills share the same filled/outlined style contract', async ({ page }) => {
    // "Any" button is active (no filters selected) — should have bg-amber-600
    const anyBtn = page.getByRole('button', { name: 'Any' }).first()
    await expect(anyBtn).toHaveClass(/bg-amber-600/)

    // A chip pill (unchecked) should NOT have bg-amber-600 (outlined style)
    const pythonLabel = page.locator('label').filter({ hasText: /^python$/ })
    await expect(pythonLabel).not.toHaveClass(/bg-amber-600/)

    // After checking python, the chip becomes filled
    await pythonLabel.locator('input[type="checkbox"]').check()
    await expect(pythonLabel).toHaveClass(/bg-amber-600/)
  })
})
