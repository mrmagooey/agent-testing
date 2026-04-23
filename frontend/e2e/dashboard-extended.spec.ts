/**
 * Extended Dashboard tests covering gaps from the audit:
 * - "Compare" button navigates to /compare
 * - PollingIndicator elapsed-time text
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

// ---------------------------------------------------------------------------
// Compare button navigation
// ---------------------------------------------------------------------------

test.describe('Compare button', () => {
  test('"Compare" button navigates to /compare', async ({ page }) => {
    await page.goto('/')
    const compareBtn = page.getByRole('button', { name: 'Compare' })
    await expect(compareBtn).toBeVisible()
    await compareBtn.click()
    await expect(page).toHaveURL('/compare')
  })

  test('"Compare" button is separate from "New Experiment" button', async ({ page }) => {
    await page.goto('/')
    const compareBtn = page.getByRole('button', { name: 'Compare' })
    const newBtn = page.getByRole('button', { name: 'New Experiment' })
    await expect(compareBtn).toBeVisible()
    await expect(newBtn).toBeVisible()
    // They are distinct
    const compareBtnBox = await compareBtn.boundingBox()
    const newBtnBox = await newBtn.boundingBox()
    expect(compareBtnBox?.x).not.toEqual(newBtnBox?.x)
  })
})

// ---------------------------------------------------------------------------
// PollingIndicator
// ---------------------------------------------------------------------------

test.describe('PollingIndicator', () => {
  test('shows elapsed time after initial load', async ({ page }) => {
    await page.goto('/')

    // After data loads, PollingIndicator should show "just now" or a time string.
    // The PollingIndicator renders "just now" or "Xs ago" next to a pulsing dot.
    // Wait for the page to be fully settled (experiments loaded)
    await page.waitForLoadState('networkidle')

    // The "just now" text should appear next to the polling dot
    // The PollingIndicator is a small flex row with a dot + time text
    const justNowText = page.getByText('just now')
    // It may briefly show "just now" right after load
    await expect(justNowText).toBeVisible({ timeout: 5000 })
  })

  test('polling dot is present after initial load', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    // The PollingIndicator renders a small dot (animate-pulse when polling, muted when idle)
    // After the initial fetch settles, the dot should be present
    const dot = page.locator('.rounded-full').first()
    await expect(dot).toBeVisible()
  })
})
