import { test, expect, Page } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

/**
 * Regression test for the dark-mode contrast bug on the accuracy heatmap.
 * The PASS/WARN/FAIL sublabel used `text-signal-*` + `opacity-80` over an amber
 * cell ramp, which rendered the label essentially invisible in dark mode.
 *
 * These tests verify that the sublabel color is distinct from the cell
 * background (by luminance delta) in both themes.
 */

function luminance({ r, g, b }: { r: number; g: number; b: number }): number {
  return 0.299 * r + 0.587 * g + 0.114 * b
}

// Colors in computed style may be rgb(...) or modern formats like oklch(...).
// A 1x1 canvas normalises any browser-parseable color string to rgb bytes.
async function colorToRgb(page: Page, color: string): Promise<{ r: number; g: number; b: number }> {
  return page.evaluate((c) => {
    const canvas = document.createElement('canvas')
    canvas.width = 1
    canvas.height = 1
    const ctx = canvas.getContext('2d')!
    ctx.fillStyle = c
    ctx.fillRect(0, 0, 1, 1)
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
    return { r, g, b }
  }, color)
}

async function readCellAndSignalColors(page: Page) {
  const cell = page.getByTestId('heatmap-cell').first()
  const signal = cell.getByTestId('heatmap-cell-signal')
  await expect(cell).toBeVisible()
  await expect(signal).toBeVisible()

  const bgStr = await cell.evaluate((el) => window.getComputedStyle(el).backgroundColor)
  const fgStr = await signal.evaluate((el) => window.getComputedStyle(el).color)
  const bg = await colorToRgb(page, bgStr)
  const fg = await colorToRgb(page, fgStr)
  return { bg, fg, bgStr, fgStr }
}

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('light mode: heatmap cell sublabel has sufficient contrast against cell background', async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem('theme', 'light'))
  await page.goto('/')
  const { bg, fg, bgStr, fgStr } = await readCellAndSignalColors(page)
  const delta = Math.abs(luminance(bg) - luminance(fg))
  expect(
    delta,
    `Expected luminance delta > 50 in light mode but got ${delta.toFixed(1)} (bg=${bgStr}, fg=${fgStr})`,
  ).toBeGreaterThan(50)
})

test('dark mode: heatmap cell sublabel has sufficient contrast against cell background', async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem('theme', 'dark'))
  await page.goto('/')
  const { bg, fg, bgStr, fgStr } = await readCellAndSignalColors(page)
  const delta = Math.abs(luminance(bg) - luminance(fg))
  expect(
    delta,
    `Expected luminance delta > 50 in dark mode but got ${delta.toFixed(1)} (bg=${bgStr}, fg=${fgStr})`,
  ).toBeGreaterThan(50)
})

test('dark mode: heatmap sublabel color is not equal to cell background', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('theme', 'dark'))
  await page.goto('/')
  const { bg, fg } = await readCellAndSignalColors(page)
  // Exact-color match is the clearest regression signal.
  expect(`${fg.r},${fg.g},${fg.b}`).not.toBe(`${bg.r},${bg.g},${bg.b}`)
})
