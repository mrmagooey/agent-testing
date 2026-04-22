import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// Completed experiment IDs from fixtures/experiments.json (status === 'completed')
// bbbbbbbb is 'running' and must not appear in the selects.
const EXPERIMENT_A_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const EXPERIMENT_B_ID = 'cccccccc-0003-0003-0003-000000000003'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto('/feedback')
})

// ─── Experiment select population ──────────────────────────────────────────────

test('experiment A select contains only completed experiments', async ({ page }) => {
  const experimentASelect = page.locator('select').nth(2)
  const options = experimentASelect.locator('option')
  // placeholder + 2 completed experiments (aaaaaaaa, cccccccc); bbbbbbbb is running
  await expect(options).toHaveCount(3)
  await expect(options.nth(1)).toHaveAttribute('value', EXPERIMENT_A_ID)
  await expect(options.nth(2)).toHaveAttribute('value', EXPERIMENT_B_ID)
})

test('experiment B select contains only completed experiments', async ({ page }) => {
  const experimentBSelect = page.locator('select').nth(3)
  const options = experimentBSelect.locator('option')
  await expect(options).toHaveCount(3)
  await expect(options.nth(1)).toHaveAttribute('value', EXPERIMENT_A_ID)
  await expect(options.nth(2)).toHaveAttribute('value', EXPERIMENT_B_ID)
})

test('running experiment does not appear in any select', async ({ page }) => {
  const allSelects = page.locator('select')
  // Check experiment selects: A (nth(2)), B (nth(3)), and FP (nth(4))
  for (let i = 2; i <= 4; i++) {
    const opts = allSelects.nth(i).locator('option')
    const count = await opts.count()
    for (let j = 0; j < count; j++) {
      const val = await opts.nth(j).getAttribute('value')
      expect(val).not.toBe('bbbbbbbb-0002-0002-0002-000000000002')
    }
  }
})

// ─── Compare button enabled / disabled state ──────────────────────────────────

test('compare button remains disabled when only experiment A is set', async ({ page }) => {
  await page.locator('select').nth(2).selectOption(EXPERIMENT_A_ID)
  await expect(page.getByRole('button', { name: 'Compare' })).toBeDisabled()
})

test('compare button remains disabled when only experiment B is set', async ({ page }) => {
  await page.locator('select').nth(3).selectOption(EXPERIMENT_B_ID)
  await expect(page.getByRole('button', { name: 'Compare' })).toBeDisabled()
})

test('compare button becomes enabled when both A and B are set to different experiments', async ({ page }) => {
  await page.locator('select').nth(2).selectOption(EXPERIMENT_A_ID)
  await page.locator('select').nth(3).selectOption(EXPERIMENT_B_ID)
  await expect(page.getByRole('button', { name: 'Compare' })).toBeEnabled()
})

test('compare button becomes enabled when both A and B are set to the same experiment', async ({ page }) => {
  // The UI does not prevent comparing an experiment with itself — just both must be non-empty
  await page.locator('select').nth(2).selectOption(EXPERIMENT_A_ID)
  await page.locator('select').nth(3).selectOption(EXPERIMENT_A_ID)
  await expect(page.getByRole('button', { name: 'Compare' })).toBeEnabled()
})

// ─── Compare request URL ──────────────────────────────────────────────────────

test('clicking Compare fires GET /api/experiments/compare with correct a and b params', async ({ page }) => {
  const requestPromise = page.waitForRequest(
    (req) =>
      req.method() === 'GET' &&
      req.url().includes('/api/experiments/compare') &&
      req.url().includes(`a=${encodeURIComponent(EXPERIMENT_A_ID)}`) &&
      req.url().includes(`b=${encodeURIComponent(EXPERIMENT_B_ID)}`)
  )

  await page.locator('select').nth(2).selectOption(EXPERIMENT_A_ID)
  await page.locator('select').nth(3).selectOption(EXPERIMENT_B_ID)
  await page.getByRole('button', { name: 'Compare' }).click()

  const req = await requestPromise
  const url = new URL(req.url())
  expect(url.searchParams.get('a')).toBe(EXPERIMENT_A_ID)
  expect(url.searchParams.get('b')).toBe(EXPERIMENT_B_ID)
})

test('compare does NOT fire a POST — only GET', async ({ page }) => {
  const postRequests: string[] = []
  page.on('request', (req) => {
    if (req.method() === 'POST' && req.url().includes('/experiments/compare')) {
      postRequests.push(req.url())
    }
  })

  await page.locator('select').nth(2).selectOption(EXPERIMENT_A_ID)
  await page.locator('select').nth(3).selectOption(EXPERIMENT_B_ID)
  await page.getByRole('button', { name: 'Compare' }).click()

  // Wait for the comparison results to render so we know the GET completed
  await expect(page.getByText('Metric Deltas')).toBeVisible()
  expect(postRequests).toHaveLength(0)
})

// ─── Compare results rendering ────────────────────────────────────────────────

test('comparison result shows experiment ID from mock response', async ({ page }) => {
  await page.locator('select').nth(2).selectOption(EXPERIMENT_A_ID)
  await page.locator('select').nth(3).selectOption(EXPERIMENT_B_ID)
  await page.getByRole('button', { name: 'Compare' }).click()

  // The mock returns experiment_id: 'gpt-4o__zero_shot__with_tools' for /experiments/compare
  await expect(page.getByText('gpt-4o__zero_shot__with_tools')).toBeVisible()
})

test('comparison result renders positive delta values with + prefix', async ({ page }) => {
  await page.locator('select').nth(2).selectOption(EXPERIMENT_A_ID)
  await page.locator('select').nth(3).selectOption(EXPERIMENT_B_ID)
  await page.getByRole('button', { name: 'Compare' }).click()

  // Mock returns precision_delta: 0.056 — DeltaCell renders "+0.056"
  await expect(page.getByText('+0.056')).toBeVisible()
})

test('comparison result includes FP patterns section from compare response', async ({ page }) => {
  await page.locator('select').nth(2).selectOption(EXPERIMENT_A_ID)
  await page.locator('select').nth(3).selectOption(EXPERIMENT_B_ID)
  await page.getByRole('button', { name: 'Compare' }).click()

  await expect(page.getByText('FP Patterns').first()).toBeVisible()
  // fp_patterns in the compare mock reuses fp-patterns.json data
  await expect(page.getByText('Template engine auto-escaping misidentified as XSS')).toBeVisible()
})

// ─── FP Pattern Browser: Load Patterns button state ──────────────────────────

test('Load Patterns button is disabled before any FP experiment is selected', async ({ page }) => {
  await expect(page.getByRole('button', { name: 'Load Patterns' })).toBeDisabled()
})

test('Load Patterns button becomes enabled after selecting an experiment', async ({ page }) => {
  await page.locator('select').nth(4).selectOption(EXPERIMENT_A_ID)
  await expect(page.getByRole('button', { name: 'Load Patterns' })).toBeEnabled()
})

test('Load Patterns button goes back to disabled when select is reset to placeholder', async ({ page }) => {
  const fpSelect = page.locator('select').nth(4)
  await fpSelect.selectOption(EXPERIMENT_A_ID)
  await expect(page.getByRole('button', { name: 'Load Patterns' })).toBeEnabled()

  await fpSelect.selectOption({ value: '' })
  await expect(page.getByRole('button', { name: 'Load Patterns' })).toBeDisabled()
})

// ─── FP Pattern Browser: request URL ─────────────────────────────────────────

test('clicking Load Patterns fires GET /api/experiments/{id}/fp-patterns with the selected id', async ({ page }) => {
  const requestPromise = page.waitForRequest(
    (req) =>
      req.method() === 'GET' &&
      req.url().includes(`/api/experiments/${EXPERIMENT_A_ID}/fp-patterns`)
  )

  await page.locator('select').nth(4).selectOption(EXPERIMENT_A_ID)
  await page.getByRole('button', { name: 'Load Patterns' }).click()

  const req = await requestPromise
  expect(req.url()).toContain(`/experiments/${EXPERIMENT_A_ID}/fp-patterns`)
})

test('clicking Load Patterns with experiment B ID calls the correct experiment endpoint', async ({ page }) => {
  const requestPromise = page.waitForRequest(
    (req) =>
      req.method() === 'GET' &&
      req.url().includes(`/api/experiments/${EXPERIMENT_B_ID}/fp-patterns`)
  )

  await page.locator('select').nth(4).selectOption(EXPERIMENT_B_ID)
  await page.getByRole('button', { name: 'Load Patterns' }).click()

  const req = await requestPromise
  expect(req.url()).toContain(`/experiments/${EXPERIMENT_B_ID}/fp-patterns`)
})

// ─── FP Pattern Browser: results rendering ───────────────────────────────────

test('FP patterns table shows all rows from fixture', async ({ page }) => {
  await page.locator('select').nth(4).selectOption(EXPERIMENT_A_ID)
  await page.getByRole('button', { name: 'Load Patterns' }).click()

  // fp-patterns.json has 2 entries
  await expect(page.getByText('Template engine auto-escaping misidentified as XSS')).toBeVisible()
  await expect(page.getByText('ORM query builder flagged as raw SQL')).toBeVisible()
})

test('FP patterns table shows model, vuln class, count and suggested action columns', async ({ page }) => {
  await page.locator('select').nth(4).selectOption(EXPERIMENT_A_ID)
  await page.getByRole('button', { name: 'Load Patterns' }).click()

  await expect(page.getByText('gpt-4o').first()).toBeVisible()
  await expect(page.getByRole('cell', { name: 'xss', exact: true })).toBeVisible()
  await expect(page.getByText('Add template engine context to system prompt')).toBeVisible()
})

test('FP patterns count cell shows numeric value', async ({ page }) => {
  await page.locator('select').nth(4).selectOption(EXPERIMENT_A_ID)
  await page.getByRole('button', { name: 'Load Patterns' }).click()

  // fixture has count: 5 and count: 3
  const cells = page.locator('td').filter({ hasText: /^5$/ })
  await expect(cells.first()).toBeVisible()
})

test('"No FP patterns found" message does not appear when patterns are loaded', async ({ page }) => {
  await page.locator('select').nth(4).selectOption(EXPERIMENT_A_ID)
  await page.getByRole('button', { name: 'Load Patterns' }).click()

  await expect(page.getByText('No FP patterns found.')).not.toBeVisible()
})
