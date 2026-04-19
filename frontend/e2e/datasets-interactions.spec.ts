import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// Fixture data constants derived from fixtures/datasets.json
// Sorted ascending by name: cve-2024-js, cve-2024-python, injected-java-2024
const DATASETS = [
  { name: 'cve-2024-python', source: 'cve', label_count: 14, size_bytes: 524288, created_at: '2026-03-01T00:00:00Z' },
  { name: 'cve-2024-js',     source: 'cve', label_count: 9,  size_bytes: 312000, created_at: '2026-03-15T00:00:00Z' },
  { name: 'injected-java-2024', source: 'injected', label_count: 5, size_bytes: 204800, created_at: '2026-04-01T00:00:00Z' },
]

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto('/datasets')
})

// ---------------------------------------------------------------------------
// 1. "Discover CVEs" button navigates to /datasets/discover
// ---------------------------------------------------------------------------

test('Discover CVEs button navigates to /datasets/discover', async ({ page }) => {
  const btn = page.getByRole('button', { name: 'Discover CVEs' })
  await expect(btn).toBeVisible()
  await btn.click()
  await expect(page).toHaveURL('/datasets/discover')
})

// ---------------------------------------------------------------------------
// 2. Filter input: substring match hides non-matching rows
// ---------------------------------------------------------------------------

test('filter input shows only rows whose name contains the typed substring', async ({ page }) => {
  const input = page.getByPlaceholder('Filter datasets…')
  await expect(input).toBeVisible()

  // Type a substring that matches only 'injected-java-2024'
  await input.fill('injected')

  // The matching row must be visible
  await expect(page.getByRole('cell', { name: 'injected-java-2024' })).toBeVisible()

  // The non-matching rows must not be in the DOM / visible
  await expect(page.getByRole('cell', { name: 'cve-2024-python' })).not.toBeVisible()
  await expect(page.getByRole('cell', { name: 'cve-2024-js' })).not.toBeVisible()
})

test('filter input shows count indicator while a filter is active', async ({ page }) => {
  const input = page.getByPlaceholder('Filter datasets…')
  await input.fill('cve')

  // Two datasets match "cve"; the component renders "{n} of {total}"
  await expect(page.getByText('2 of 3')).toBeVisible()
})

test('filter input restores all rows when cleared', async ({ page }) => {
  const input = page.getByPlaceholder('Filter datasets…')
  await input.fill('injected')
  await expect(page.getByRole('cell', { name: 'cve-2024-python' })).not.toBeVisible()

  await input.clear()

  await expect(page.getByRole('cell', { name: 'cve-2024-python' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'cve-2024-js' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'injected-java-2024' })).toBeVisible()
})

test('filter shows empty-state message when no datasets match', async ({ page }) => {
  const input = page.getByPlaceholder('Filter datasets…')
  await input.fill('zzz-no-match')
  await expect(page.getByText(/No datasets match/)).toBeVisible()
})

// ---------------------------------------------------------------------------
// 3. Sortable column headers toggle sort direction / row order
// ---------------------------------------------------------------------------

// Helper: returns the text of the first data cell in each tbody row for a given column index (0-based).
async function getColumnValues(page: ReturnType<typeof test.info extends never ? never : any>, colIndex: number): Promise<string[]> {
  const rows = page.locator('tbody tr')
  const count = await rows.count()
  const values: string[] = []
  for (let i = 0; i < count; i++) {
    const cell = rows.nth(i).locator('td').nth(colIndex)
    values.push((await cell.textContent()) ?? '')
  }
  return values
}

test('clicking Name column header sorts rows ascending then descending', async ({ page }) => {
  const nameHeader = page.getByRole('columnheader', { name: 'Name' })
  await expect(nameHeader).toBeVisible()

  // Initial state: sorted by name asc (default).
  // Ascending order: cve-2024-js, cve-2024-python, injected-java-2024
  const ascValues = await getColumnValues(page, 0)
  expect(ascValues[0].trim()).toBe('cve-2024-js')
  expect(ascValues[ascValues.length - 1].trim()).toBe('injected-java-2024')

  // First click on already-active Name header → flips to desc
  await nameHeader.click()

  // Sort indicator should now show the down arrow
  await expect(nameHeader.locator('span')).toHaveText('↓')

  const descValues = await getColumnValues(page, 0)
  expect(descValues[0].trim()).toBe('injected-java-2024')
  expect(descValues[descValues.length - 1].trim()).toBe('cve-2024-js')

  // Second click → back to asc
  await nameHeader.click()
  await expect(nameHeader.locator('span')).toHaveText('↑')
})

test('clicking Source column header applies sort and shows indicator', async ({ page }) => {
  const sourceHeader = page.getByRole('columnheader', { name: 'Source' })
  await sourceHeader.click()

  // Indicator should be visible on Source (asc first click because it was not the active column)
  await expect(sourceHeader.locator('span')).toHaveText('↑')

  // Second click → desc
  await sourceHeader.click()
  await expect(sourceHeader.locator('span')).toHaveText('↓')
})

test('clicking Labels column header sorts by label count', async ({ page }) => {
  const labelsHeader = page.getByRole('columnheader', { name: 'Labels' })
  await labelsHeader.click()

  // Ascending by label_count: 5, 9, 14
  const values = await getColumnValues(page, 2)
  expect(values[0].trim()).toBe('5')
  expect(values[values.length - 1].trim()).toBe('14')

  await expect(labelsHeader.locator('span')).toHaveText('↑')

  // Second click → desc: 14, 9, 5
  await labelsHeader.click()
  const descValues = await getColumnValues(page, 2)
  expect(descValues[0].trim()).toBe('14')

  await expect(labelsHeader.locator('span')).toHaveText('↓')
})

test('clicking Size column header sorts by file size', async ({ page }) => {
  const sizeHeader = page.getByRole('columnheader', { name: 'Size' })
  await sizeHeader.click()

  // Ascending by size_bytes: 204800 (injected-java-2024), 312000 (cve-2024-js), 524288 (cve-2024-python)
  const nameValues = await getColumnValues(page, 0)
  expect(nameValues[0].trim()).toBe('injected-java-2024')
  expect(nameValues[nameValues.length - 1].trim()).toBe('cve-2024-python')

  await expect(sizeHeader.locator('span')).toHaveText('↑')

  // Second click → desc
  await sizeHeader.click()
  const descNames = await getColumnValues(page, 0)
  expect(descNames[0].trim()).toBe('cve-2024-python')

  await expect(sizeHeader.locator('span')).toHaveText('↓')
})

test('clicking Created column header sorts by creation date', async ({ page }) => {
  const createdHeader = page.getByRole('columnheader', { name: 'Created' })
  await createdHeader.click()

  // Ascending by created_at: cve-2024-python (Mar 1), cve-2024-js (Mar 15), injected-java-2024 (Apr 1)
  const nameValues = await getColumnValues(page, 0)
  expect(nameValues[0].trim()).toBe('cve-2024-python')
  expect(nameValues[nameValues.length - 1].trim()).toBe('injected-java-2024')

  await expect(createdHeader.locator('span')).toHaveText('↑')

  // Second click → desc
  await createdHeader.click()
  const descNames = await getColumnValues(page, 0)
  expect(descNames[0].trim()).toBe('injected-java-2024')

  await expect(createdHeader.locator('span')).toHaveText('↓')
})

test('only one column shows a sort indicator at a time', async ({ page }) => {
  // Start: Name has the indicator (default sort)
  const nameHeader = page.getByRole('columnheader', { name: 'Name' })
  await expect(nameHeader.locator('span')).toBeVisible()

  // Click Source
  const sourceHeader = page.getByRole('columnheader', { name: 'Source' })
  await sourceHeader.click()

  // Source now has indicator; Name no longer does
  await expect(sourceHeader.locator('span')).toBeVisible()
  await expect(nameHeader.locator('span')).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// 4. Dataset row click navigates to /datasets/{dataset.name}
// ---------------------------------------------------------------------------

test('clicking the first dataset row navigates to its detail URL', async ({ page }) => {
  // Default sort is name asc → first row is cve-2024-js
  const firstRow = page.locator('tbody tr').first()
  const firstNameCell = firstRow.locator('td').first()
  const datasetName = (await firstNameCell.textContent())?.trim() ?? ''

  expect(datasetName).toBeTruthy()

  await firstRow.click()

  await expect(page).toHaveURL(new RegExp(`/datasets/${encodeURIComponent(datasetName)}$`))
})

test('clicking the second dataset row navigates to its detail URL', async ({ page }) => {
  const secondRow = page.locator('tbody tr').nth(1)
  const nameCell = secondRow.locator('td').first()
  const datasetName = (await nameCell.textContent())?.trim() ?? ''

  expect(datasetName).toBeTruthy()

  await secondRow.click()

  await expect(page).toHaveURL(new RegExp(`/datasets/${encodeURIComponent(datasetName)}$`))
})

test('row click for fixture first entry navigates to /datasets/cve-2024-js', async ({ page }) => {
  // Fixture row 0 when sorted by name asc is cve-2024-js
  await page.locator('tbody tr').first().click()
  await expect(page).toHaveURL('/datasets/cve-2024-js')
})
