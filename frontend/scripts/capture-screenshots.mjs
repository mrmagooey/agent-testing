import { chromium } from 'playwright'
import { mockApi } from '../e2e/helpers/mockApi.ts'
import { mkdir } from 'node:fs/promises'
import { existsSync } from 'node:fs'

const OUT_DIR = process.env.OUT_DIR || '/tmp/frontend-screenshots'
const BASE_URL = process.env.BASE_URL || 'http://localhost:5173'

const routes = [
  { name: 'dashboard', path: '/' },
  { name: 'experiment-new', path: '/experiments/new' },
  { name: 'run-compare', path: '/compare' },
  { name: 'datasets', path: '/datasets' },
  { name: 'cve-discovery', path: '/datasets/discover' },
  { name: 'findings', path: '/findings' },
  { name: 'feedback', path: '/feedback' },
  { name: 'experiment-detail', path: '/experiments/aaaaaaaa-0001-0001-0001-000000000001' },
  { name: 'experiment-compare', path: '/experiments/aaaaaaaa-0001-0001-0001-000000000001/compare' },
  { name: 'run-detail', path: '/experiments/aaaaaaaa-0001-0001-0001-000000000001/runs/run-001-aaa' },
  { name: 'dataset-detail', path: '/datasets/cve-2024-python' },
  { name: 'dataset-source', path: '/datasets/cve-2024-python/source' },
]

const themes = ['light', 'dark']

async function run() {
  if (!existsSync(OUT_DIR)) await mkdir(OUT_DIR, { recursive: true })
  const browser = await chromium.launch()
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  })

  const page = await context.newPage()
  await mockApi(page)

  const consoleErrors = {}
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const key = page.url()
      consoleErrors[key] = consoleErrors[key] || []
      consoleErrors[key].push(msg.text())
    }
  })
  page.on('pageerror', (err) => {
    const key = page.url()
    consoleErrors[key] = consoleErrors[key] || []
    consoleErrors[key].push('PAGEERROR: ' + err.message)
  })

  // prime origin so we can set localStorage before app loads
  await page.goto(BASE_URL + '/')
  for (const theme of themes) {
    await page.evaluate((t) => localStorage.setItem('theme', t), theme)
    for (const r of routes) {
      const url = BASE_URL + r.path
      try {
        await page.goto(url, { waitUntil: 'networkidle', timeout: 20000 })
      } catch (e) {
        console.log(`[warn] navigation ${url} did not reach networkidle: ${e.message}`)
      }
      // give react a moment for any post-render async work
      await page.waitForTimeout(600)
      const file = `${OUT_DIR}/${theme}__${r.name}.png`
      await page.screenshot({ path: file, fullPage: true })
      console.log(`saved ${file}`)
    }
  }

  await browser.close()

  const anyErrors = Object.keys(consoleErrors).length
  if (anyErrors) {
    console.log('\n--- Console / page errors ---')
    for (const [k, v] of Object.entries(consoleErrors)) {
      console.log(k)
      for (const m of v) console.log('  ' + m)
    }
  } else {
    console.log('\nNo console errors captured.')
  }
}

run().catch((e) => {
  console.error(e)
  process.exit(1)
})
