import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('MatrixTable and RunDetail have no missing key prop warnings', async ({ page }) => {
  const consoleMessages: { type: string; message: string }[] = []

  // Capture console messages before navigation
  page.on('console', (msg) => {
    consoleMessages.push({
      type: msg.type(),
      message: msg.text(),
    })
  })

  // Navigate to experiment detail (should trigger MatrixTable render)
  await page.goto('/experiments/aaaaaaaa-0001-0001-0001-000000000001')
  await page.waitForLoadState('networkidle')

  // Navigate to run detail (should trigger RunDetail render)
  await page.goto('/experiments/aaaaaaaa-0001-0001-0001-000000000001/runs/run-001-aaa')
  await page.waitForLoadState('networkidle')

  // Check for missing key prop warnings
  const keyWarnings = consoleMessages.filter((msg) => /unique "key" prop/i.test(msg.message))

  expect(keyWarnings, `Should have no missing key prop warnings, but found: ${JSON.stringify(keyWarnings)}`).toEqual([])
})
