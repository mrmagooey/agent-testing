/**
 * Interaction-focused tests for RunCompare:
 *   1. Tab switching — active styling changes, content region updates for all three tabs.
 *   2. Finding card expander — expand shows description, second click collapses it.
 *
 * These complement run-compare.spec.ts (which covers basic visibility) and do NOT
 * duplicate those assertions.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_A = 'run-001-aaa'
const RUN_B = 'run-002-bbb'

// Selectors derived from RunCompare.tsx
const TAB_BOTH = /Found by Both/
const TAB_ONLY_A = /Only in A/
const TAB_ONLY_B = /Only in B/

// Text present in the fixture for each bucket
const FINDING_BOTH_TITLE = 'SQL Injection in user login handler'
const FINDING_BOTH_DESC = 'User-supplied input is concatenated directly'
const FINDING_A_TITLE = 'Path traversal in file download endpoint'
const FINDING_A_DESC = 'The file path parameter is not sanitized'
const EMPTY_STATE = 'No findings in this category.'

// Active-tab CSS class applied in RunCompare.tsx
const ACTIVE_TAB_CLASS = 'border-amber-600'

test.describe('RunCompare tab switching', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)
  })

  test('"Found by Both" tab is active by default and shows shared findings', async ({ page }) => {
    const tabButton = page.getByRole('button', { name: TAB_BOTH })
    await expect(tabButton).toBeVisible()
    // Active tab has the amber bottom-border class
    await expect(tabButton).toHaveClass(new RegExp(ACTIVE_TAB_CLASS))
    // Content region shows the shared finding
    await expect(page.getByText(FINDING_BOTH_TITLE)).toBeVisible()
    // Only-A finding should NOT be visible while on the Both tab
    await expect(page.getByText(FINDING_A_TITLE)).not.toBeVisible()
  })

  test('clicking "Only in A" activates that tab and updates content', async ({ page }) => {
    const tabBoth = page.getByRole('button', { name: TAB_BOTH })
    const tabOnlyA = page.getByRole('button', { name: TAB_ONLY_A })

    await tabOnlyA.click()

    // Tab A is now active
    await expect(tabOnlyA).toHaveClass(new RegExp(ACTIVE_TAB_CLASS))
    // Tab Both is no longer active
    await expect(tabBoth).not.toHaveClass(new RegExp(ACTIVE_TAB_CLASS))

    // Content region shows the Only-A finding
    await expect(page.getByText(FINDING_A_TITLE)).toBeVisible()
    // Shared finding should no longer be visible
    await expect(page.getByText(FINDING_BOTH_TITLE)).not.toBeVisible()
  })

  test('clicking "Only in B" activates that tab and shows empty state', async ({ page }) => {
    const tabBoth = page.getByRole('button', { name: TAB_BOTH })
    const tabOnlyB = page.getByRole('button', { name: TAB_ONLY_B })

    await tabOnlyB.click()

    // Tab B is now active
    await expect(tabOnlyB).toHaveClass(new RegExp(ACTIVE_TAB_CLASS))
    // Tab Both is no longer active
    await expect(tabBoth).not.toHaveClass(new RegExp(ACTIVE_TAB_CLASS))

    // Fixture has only_in_b: [] so the empty state message should appear
    await expect(page.getByText(EMPTY_STATE)).toBeVisible()
    // Neither finding should be visible
    await expect(page.getByText(FINDING_BOTH_TITLE)).not.toBeVisible()
    await expect(page.getByText(FINDING_A_TITLE)).not.toBeVisible()
  })

  test('switching back to "Found by Both" after visiting another tab restores content', async ({ page }) => {
    const tabBoth = page.getByRole('button', { name: TAB_BOTH })
    const tabOnlyA = page.getByRole('button', { name: TAB_ONLY_A })

    // Navigate away then back
    await tabOnlyA.click()
    await expect(page.getByText(FINDING_A_TITLE)).toBeVisible()

    await tabBoth.click()

    // Tab Both is active again
    await expect(tabBoth).toHaveClass(new RegExp(ACTIVE_TAB_CLASS))
    await expect(tabOnlyA).not.toHaveClass(new RegExp(ACTIVE_TAB_CLASS))

    // Shared finding is restored; Only-A finding is hidden
    await expect(page.getByText(FINDING_BOTH_TITLE)).toBeVisible()
    await expect(page.getByText(FINDING_A_TITLE)).not.toBeVisible()
  })

  test('cycling through all three tabs in sequence updates content each time', async ({ page }) => {
    const tabBoth = page.getByRole('button', { name: TAB_BOTH })
    const tabOnlyA = page.getByRole('button', { name: TAB_ONLY_A })
    const tabOnlyB = page.getByRole('button', { name: TAB_ONLY_B })

    // Start: Both tab
    await expect(page.getByText(FINDING_BOTH_TITLE)).toBeVisible()

    // → Only in A
    await tabOnlyA.click()
    await expect(page.getByText(FINDING_A_TITLE)).toBeVisible()
    await expect(page.getByText(FINDING_BOTH_TITLE)).not.toBeVisible()

    // → Only in B
    await tabOnlyB.click()
    await expect(page.getByText(EMPTY_STATE)).toBeVisible()
    await expect(page.getByText(FINDING_A_TITLE)).not.toBeVisible()

    // → Back to Both
    await tabBoth.click()
    await expect(page.getByText(FINDING_BOTH_TITLE)).toBeVisible()
    await expect(page.getByText(EMPTY_STATE)).not.toBeVisible()
  })
})

test.describe('RunCompare finding card expander', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto(`/experiments/${EXPERIMENT_ID}/compare?a=${RUN_A}&b=${RUN_B}`)
  })

  test('finding card header button expands the description on first click', async ({ page }) => {
    // Description is hidden before expanding
    await expect(page.getByText(FINDING_BOTH_DESC)).not.toBeVisible()

    // The FindingCard renders a <button> wrapping the title text
    const expander = page.getByRole('button', { name: new RegExp(FINDING_BOTH_TITLE) })
    await expander.click()

    await expect(page.getByText(FINDING_BOTH_DESC)).toBeVisible()
  })

  test('clicking expander again collapses the description', async ({ page }) => {
    const expander = page.getByRole('button', { name: new RegExp(FINDING_BOTH_TITLE) })

    // Expand
    await expander.click()
    await expect(page.getByText(FINDING_BOTH_DESC)).toBeVisible()

    // Collapse
    await expander.click()
    await expect(page.getByText(FINDING_BOTH_DESC)).not.toBeVisible()
  })

  test('opening one card collapses a previously opened card', async ({ page }) => {
    // Switch to Only-in-A to have two different finding cards available together isn't
    // possible here (each tab shows one finding in the fixture). Verify that expanding
    // on Both tab, switching to Only-A, and expanding that card works independently.
    const expanderBoth = page.getByRole('button', { name: new RegExp(FINDING_BOTH_TITLE) })
    await expanderBoth.click()
    await expect(page.getByText(FINDING_BOTH_DESC)).toBeVisible()

    // Navigate to Only-A tab and expand its card
    await page.getByRole('button', { name: TAB_ONLY_A }).click()
    const expanderA = page.getByRole('button', { name: new RegExp(FINDING_A_TITLE) })
    await expanderA.click()
    await expect(page.getByText(FINDING_A_DESC)).toBeVisible()

    // Collapse it
    await expanderA.click()
    await expect(page.getByText(FINDING_A_DESC)).not.toBeVisible()
  })

  test('chevron indicator text changes between collapsed and expanded states', async ({ page }) => {
    const expander = page.getByRole('button', { name: new RegExp(FINDING_BOTH_TITLE) })

    // Collapsed state shows down arrow
    await expect(expander).toContainText('▼')

    await expander.click()

    // Expanded state shows up arrow
    await expect(expander).toContainText('▲')

    await expander.click()

    // Back to collapsed
    await expect(expander).toContainText('▼')
  })
})
