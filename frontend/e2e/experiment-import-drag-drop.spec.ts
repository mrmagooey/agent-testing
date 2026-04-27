import { test, expect, type Page, type Locator } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function getDropzone(page: Page): Promise<Locator> {
  return page.getByRole('button', { name: 'Drop zone for experiment bundle' })
}

async function dragOver(page: Page, locator: Locator): Promise<void> {
  await locator.dispatchEvent('dragover', {
    dataTransfer: await page.evaluateHandle(() => new DataTransfer()),
  })
}

async function dragLeave(page: Page, locator: Locator): Promise<void> {
  await locator.dispatchEvent('dragleave', {
    dataTransfer: await page.evaluateHandle(() => new DataTransfer()),
  })
}

async function dropFile(
  page: Page,
  locator: Locator,
  fileName: string,
  fileBytes: number,
): Promise<void> {
  await locator.dispatchEvent('drop', {
    dataTransfer: await page.evaluateHandle(
      ({ name, bytes }) => {
        const dt = new DataTransfer()
        const file = new File(['x'.repeat(bytes)], name, { type: 'application/zip' })
        dt.items.add(file)
        return dt
      },
      { name: fileName, bytes: fileBytes },
    ),
  })
}

async function dropEmpty(page: Page, locator: Locator): Promise<void> {
  await locator.dispatchEvent('drop', {
    dataTransfer: await page.evaluateHandle(() => new DataTransfer()),
  })
}

// 2 MiB exactly — renders as "2.00 MB"
const TWO_MIB = 2 * 1024 * 1024

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

test.describe('ExperimentImport drag-and-drop', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto('/experiments/import')
  })

  // -------------------------------------------------------------------------
  // 1. Initial state shows placeholder, not a file
  // -------------------------------------------------------------------------

  test('initial state shows placeholder text and no file, dropzone not in dragging state', async ({
    page,
  }) => {
    const dropzone = await getDropzone(page)
    await expect(dropzone).toBeVisible()

    // Placeholder text pieces
    await expect(page.getByText('Drop a')).toBeVisible()
    await expect(page.getByText('.secrev.zip')).toBeVisible()
    await expect(page.getByText('or click to select a file')).toBeVisible()

    // No file name shown yet
    await expect(page.getByText(/MB — click or drop to change/)).toHaveCount(0)

    // Not in dragging state
    await expect(dropzone).not.toHaveClass(/border-amber-500/)
    await expect(dropzone).not.toHaveClass(/bg-amber-50/)
  })

  // -------------------------------------------------------------------------
  // 2. dragover adds the dragging visual highlight
  // -------------------------------------------------------------------------

  test('dragover event highlights dropzone with amber border and background', async ({ page }) => {
    const dropzone = await getDropzone(page)
    await dragOver(page, dropzone)

    await expect(dropzone).toHaveClass(/border-amber-500/)
    await expect(dropzone).toHaveClass(/bg-amber-50/)

    // Placeholder text still shown during drag
    await expect(page.getByText('Drop a')).toBeVisible()
    await expect(page.getByText('.secrev.zip')).toBeVisible()
  })

  // -------------------------------------------------------------------------
  // 3. dragleave reverts the dragging visual
  // -------------------------------------------------------------------------

  test('dragleave after dragover removes amber highlight and restores default border', async ({
    page,
  }) => {
    const dropzone = await getDropzone(page)
    await dragOver(page, dropzone)
    await expect(dropzone).toHaveClass(/border-amber-500/)

    await dragLeave(page, dropzone)

    await expect(dropzone).not.toHaveClass(/border-amber-500/)
    await expect(dropzone).not.toHaveClass(/bg-amber-50/)
    // Default border classes restored
    await expect(dropzone).toHaveClass(/border-gray-300/)
  })

  // -------------------------------------------------------------------------
  // 4. drop with a file shows name and size, replacing the placeholder
  // -------------------------------------------------------------------------

  test('dropping a file shows file name and size and hides the placeholder', async ({ page }) => {
    const dropzone = await getDropzone(page)
    await dropFile(page, dropzone, 'bundle.secrev.zip', TWO_MIB)

    await expect(page.getByText('bundle.secrev.zip')).toBeVisible()
    await expect(page.getByText('2.00 MB — click or drop to change')).toBeVisible()

    // Placeholder must be gone
    await expect(page.getByText('Drop a')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // 5. drop returns dragging state to false
  // -------------------------------------------------------------------------

  test('after drop the dropzone does not retain the amber dragging highlight', async ({ page }) => {
    const dropzone = await getDropzone(page)
    // Trigger dragover first so dragging=true, then drop
    await dragOver(page, dropzone)
    await expect(dropzone).toHaveClass(/border-amber-500/)

    await dropFile(page, dropzone, 'bundle.secrev.zip', TWO_MIB)

    await expect(dropzone).not.toHaveClass(/border-amber-500/)
  })

  // -------------------------------------------------------------------------
  // 6. subsequent drop replaces the previously shown file
  // -------------------------------------------------------------------------

  test('dropping a second file replaces the first file name and size', async ({ page }) => {
    const dropzone = await getDropzone(page)

    await dropFile(page, dropzone, 'first.secrev.zip', TWO_MIB)
    await expect(page.getByText('first.secrev.zip')).toBeVisible()

    // 1.5 MiB → 1.50 MB
    const ONE_AND_HALF_MIB = Math.round(1.5 * 1024 * 1024)
    await dropFile(page, dropzone, 'second.secrev.zip', ONE_AND_HALF_MIB)

    await expect(page.getByText('second.secrev.zip')).toBeVisible()
    await expect(page.getByText('1.50 MB — click or drop to change')).toBeVisible()
    await expect(page.getByText('first.secrev.zip')).toHaveCount(0)
  })

  // -------------------------------------------------------------------------
  // 7. drop with empty DataTransfer does not crash; placeholder still shown
  // -------------------------------------------------------------------------

  test('drop with no files in DataTransfer does not crash and leaves placeholder visible', async ({
    page,
  }) => {
    const dropzone = await getDropzone(page)
    await dropEmpty(page, dropzone)

    // Placeholder still present
    await expect(page.getByText('Drop a')).toBeVisible()
    await expect(page.getByText('.secrev.zip')).toBeVisible()
    await expect(page.getByText('or click to select a file')).toBeVisible()

    // No file name shown
    await expect(page.getByText(/MB — click or drop to change/)).toHaveCount(0)
  })
})
