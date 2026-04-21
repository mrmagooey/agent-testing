import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'
const DATASET = 'cve-2024-python'

const routes: Array<[string, string]> = [
  ['Dashboard', '/'],
  ['ExperimentNew', '/experiments/new'],
  ['ExperimentDetail', `/experiments/${EXPERIMENT_ID}`],
  ['RunDetail', `/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`],
  ['RunCompare', `/experiments/${EXPERIMENT_ID}/compare?a=${RUN_ID}&b=run-002-aaa`],
  ['Datasets', '/datasets'],
  ['DatasetDetail', `/datasets/${DATASET}`],
  ['CVEDiscovery', '/datasets/discover'],
  ['Feedback', '/feedback'],
]

for (const [name, path] of routes) {
  test(`${name} shows a non-empty page description`, async ({ page }) => {
    await page.goto(path)
    const desc = page.getByTestId('page-description')
    await expect(desc).toBeVisible()
    // Two sentences minimum — assert the rendered text has some substance.
    const text = (await desc.textContent())?.trim() ?? ''
    expect(text.length).toBeGreaterThan(50)
  })
}
