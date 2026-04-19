import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

const DATASET_NAME = 'cve-2024-python'
const TEMPLATE_VULN_CLASS = 'sqli'
const TEMPLATE_ID = 'sqli-string-concat'
// Leaf file reachable by expanding src > auth in the file tree
const LEAF_FILE = 'src/auth/login.py'
// Placeholder from templates.json: "{{user_input}}"
const PLACEHOLDER_KEY = 'user_input'
const PLACEHOLDER_VALUE = 'test_payload'

test.describe('inject vulnerability modal — full workflow', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto(`/datasets/${DATASET_NAME}`)
  })

  // -----------------------------------------------------------------------
  // 1. "Inject Vulnerability" button opens modal at step 1
  // -----------------------------------------------------------------------
  test('Inject Vulnerability button opens modal at step 1', async ({ page }) => {
    await page.getByRole('button', { name: 'Inject Vulnerability' }).click()

    await expect(page.getByText('Inject Vulnerability — Step 1/5')).toBeVisible()
    await expect(page.getByText('Select a vulnerability template:')).toBeVisible()
  })

  // -----------------------------------------------------------------------
  // 2. Close button dismisses modal when no state is dirty (step 1)
  // -----------------------------------------------------------------------
  test('close button dismisses modal when substitutions are empty', async ({ page }) => {
    await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
    await expect(page.getByText('Inject Vulnerability — Step 1/5')).toBeVisible()

    // The × button has no aria-label; locate by text content
    await page.getByRole('button', { name: '×' }).click()

    await expect(page.getByText('Inject Vulnerability — Step 1/5')).not.toBeVisible()
  })

  // -----------------------------------------------------------------------
  // 3. Selecting a template advances to step 2
  // -----------------------------------------------------------------------
  test('selecting a template advances wizard to step 2 (file tree)', async ({ page }) => {
    await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
    await expect(page.getByText('Inject Vulnerability — Step 1/5')).toBeVisible()

    // Template button text includes the vuln_class
    await page.getByRole('button', { name: new RegExp(TEMPLATE_VULN_CLASS) }).first().click()

    await expect(page.getByText('Inject Vulnerability — Step 2/5')).toBeVisible()
    // Step 2 shows a file tree to pick the target file
    await expect(page.getByText(new RegExp(`filtered to`))).toBeVisible()
  })

  // -----------------------------------------------------------------------
  // 4. Clicking a leaf file in the file tree (step 2) advances to step 3
  //    and exposes substitution inputs for the template's placeholders
  // -----------------------------------------------------------------------
  test('selecting a leaf file advances to step 3 with substitution inputs', async ({ page }) => {
    // Navigate to step 2
    await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
    await page.getByRole('button', { name: new RegExp(TEMPLATE_VULN_CLASS) }).first().click()
    await expect(page.getByText('Inject Vulnerability — Step 2/5')).toBeVisible()

    // The file tree inside the modal: depth-0 nodes (e.g. "src") start expanded
    // by default (FileTree initialises expanded=true at depth===0), so we must
    // NOT click "src" — clicking it would collapse it.  We only need to expand
    // the depth-1 "auth" subdirectory, then click the leaf file.
    const modalFileTree = page.locator('.fixed.inset-0')
    // Expand the auth subdirectory (depth 1, starts collapsed)
    await modalFileTree.getByRole('button', { name: /📁 auth/ }).click()
    // Click the leaf file
    await modalFileTree.locator('button', { hasText: 'login.py' }).click()

    await expect(page.getByText('Inject Vulnerability — Step 3/5')).toBeVisible()
    // Template sqli-string-concat has placeholder {{user_input}}
    await expect(page.locator(`label`, { hasText: `{{${PLACEHOLDER_KEY}}}` })).toBeVisible()
    await expect(page.locator('.fixed.inset-0').locator('input[type="text"]')).toBeVisible()
  })

  // -----------------------------------------------------------------------
  // 5. Filling substitution inputs and clicking "Preview Injection"
  //    fires POST /api/datasets/{name}/inject/preview and shows step 4 diff
  // -----------------------------------------------------------------------
  test('Preview Injection fires POST preview and advances to step 4 diff view', async ({ page }) => {
    let previewRequestBody: unknown = null

    await page.route(`**/api/datasets/${DATASET_NAME}/inject/preview`, async (route) => {
      if (route.request().method() === 'POST') {
        previewRequestBody = route.request().postDataJSON()
      }
      // Let mockApi handler respond (continue to the already-registered handler)
      return route.fallback()
    })

    // Navigate to step 3
    await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
    await page.getByRole('button', { name: new RegExp(TEMPLATE_VULN_CLASS) }).first().click()
    await expect(page.getByText('Inject Vulnerability — Step 2/5')).toBeVisible()

    const modalFileTree = page.locator('.fixed.inset-0')
    // depth-0 "src" is already expanded; only expand depth-1 "auth" then click leaf
    await modalFileTree.getByRole('button', { name: /📁 auth/ }).click()
    await modalFileTree.locator('button', { hasText: 'login.py' }).click()
    await expect(page.getByText('Inject Vulnerability — Step 3/5')).toBeVisible()

    // Fill in the substitution input
    await page.locator('.fixed.inset-0').locator('input[type="text"]').fill(PLACEHOLDER_VALUE)

    // Click Preview Injection
    const previewRequest = page.waitForRequest(
      (req) =>
        req.url().includes(`/api/datasets/${DATASET_NAME}/inject/preview`) &&
        req.method() === 'POST'
    )
    await page.getByRole('button', { name: 'Preview Injection' }).click()
    await previewRequest

    // Step 4: diff view
    await expect(page.getByText('Inject Vulnerability — Step 4/5')).toBeVisible()
    await expect(page.getByText('Review the diff before confirming:')).toBeVisible()
    // Confirm & Inject button should be present
    await expect(page.getByRole('button', { name: /Confirm & Inject/ })).toBeVisible()

    // The preview POST was intercepted; body should include template and file
    expect(previewRequestBody).not.toBeNull()
  })

  // -----------------------------------------------------------------------
  // 6. "Confirm & Inject" fires POST /api/datasets/{name}/inject and
  //    advances to step 5 success state
  // -----------------------------------------------------------------------
  test('Confirm & Inject fires POST inject and shows step 5 success', async ({ page }) => {
    // Navigate all the way to step 4
    await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
    await page.getByRole('button', { name: new RegExp(TEMPLATE_VULN_CLASS) }).first().click()

    const modalFileTree = page.locator('.fixed.inset-0')
    // depth-0 "src" is already expanded; only expand depth-1 "auth" then click leaf
    await modalFileTree.getByRole('button', { name: /📁 auth/ }).click()
    await modalFileTree.locator('button', { hasText: 'login.py' }).click()

    await page.locator('.fixed.inset-0').locator('input[type="text"]').fill(PLACEHOLDER_VALUE)

    const injectRequest = page.waitForRequest(
      (req) =>
        req.url().includes(`/api/datasets/${DATASET_NAME}/inject`) &&
        !req.url().includes('preview') &&
        req.method() === 'POST'
    )

    await page.getByRole('button', { name: 'Preview Injection' }).click()
    await expect(page.getByText('Inject Vulnerability — Step 4/5')).toBeVisible()

    await page.getByRole('button', { name: /Confirm & Inject/ }).click()
    await injectRequest

    // Step 5: success state
    await expect(page.getByText('Inject Vulnerability — Step 5/5')).toBeVisible()
    await expect(page.getByText('Injection successful!')).toBeVisible()
    // The mocked response returns label_id: 'label-new-injected'
    await expect(page.getByText('label-new-injected')).toBeVisible()
  })

  // -----------------------------------------------------------------------
  // 7. Step 5 close button resets modal
  // -----------------------------------------------------------------------
  test('close button on step 5 success state dismisses modal', async ({ page }) => {
    // Drive through the full flow to step 5
    await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
    await page.getByRole('button', { name: new RegExp(TEMPLATE_VULN_CLASS) }).first().click()

    const modalFileTree = page.locator('.fixed.inset-0')
    // depth-0 "src" is already expanded; only expand depth-1 "auth" then click leaf
    await modalFileTree.getByRole('button', { name: /📁 auth/ }).click()
    await modalFileTree.locator('button', { hasText: 'login.py' }).click()

    await page.locator('.fixed.inset-0').locator('input[type="text"]').fill(PLACEHOLDER_VALUE)
    await page.getByRole('button', { name: 'Preview Injection' }).click()
    await expect(page.getByText('Inject Vulnerability — Step 4/5')).toBeVisible()

    await page.getByRole('button', { name: /Confirm & Inject/ }).click()
    await expect(page.getByText('Injection successful!')).toBeVisible()

    // The step-5 close button has label "Close"
    await page.getByRole('button', { name: 'Close' }).click()

    await expect(page.getByText('Injection successful!')).not.toBeVisible()
    await expect(page.getByText('Inject Vulnerability — Step 5/5')).not.toBeVisible()
  })

  // -----------------------------------------------------------------------
  // 8. Closing the modal mid-step with dirty substitutions triggers
  //    window.confirm; accepting it closes the modal
  // -----------------------------------------------------------------------
  test('closing modal at step 3 with dirty substitutions shows browser confirm', async ({ page }) => {
    // window.confirm is used by handleCloseModal when hasUnsavedSubstitutions
    // Accept the dialog automatically
    page.on('dialog', (dialog) => {
      expect(dialog.message()).toContain('Discard')
      dialog.accept()
    })

    // Navigate to step 3 and fill in a substitution value
    await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
    await page.getByRole('button', { name: new RegExp(TEMPLATE_VULN_CLASS) }).first().click()

    const modalFileTree = page.locator('.fixed.inset-0')
    // depth-0 "src" is already expanded; only expand depth-1 "auth" then click leaf
    await modalFileTree.getByRole('button', { name: /📁 auth/ }).click()
    await modalFileTree.locator('button', { hasText: 'login.py' }).click()

    await expect(page.getByText('Inject Vulnerability — Step 3/5')).toBeVisible()
    await page.locator('.fixed.inset-0').locator('input[type="text"]').fill(PLACEHOLDER_VALUE)

    // Click the × close button — should trigger confirm dialog
    await page.getByRole('button', { name: '×' }).click()

    // After accepting the confirm, the modal should be gone
    await expect(page.getByText('Inject Vulnerability — Step 3/5')).not.toBeVisible()
  })

  // -----------------------------------------------------------------------
  // 8b. Cancelling the confirm dialog keeps the modal open
  // -----------------------------------------------------------------------
  test('dismissing the confirm dialog keeps the modal open', async ({ page }) => {
    // Dismiss (cancel) the dialog — modal should remain open
    page.on('dialog', (dialog) => dialog.dismiss())

    await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
    await page.getByRole('button', { name: new RegExp(TEMPLATE_VULN_CLASS) }).first().click()

    const modalFileTree = page.locator('.fixed.inset-0')
    // depth-0 "src" is already expanded; only expand depth-1 "auth" then click leaf
    await modalFileTree.getByRole('button', { name: /📁 auth/ }).click()
    await modalFileTree.locator('button', { hasText: 'login.py' }).click()

    await expect(page.getByText('Inject Vulnerability — Step 3/5')).toBeVisible()
    await page.locator('.fixed.inset-0').locator('input[type="text"]').fill(PLACEHOLDER_VALUE)

    await page.getByRole('button', { name: '×' }).click()

    // Modal remains open because user cancelled the confirm
    await expect(page.getByText('Inject Vulnerability — Step 3/5')).toBeVisible()
  })

  // -----------------------------------------------------------------------
  // 9. Template list in step 1 shows all fixture templates
  // -----------------------------------------------------------------------
  test('step 1 lists all templates from fixture', async ({ page }) => {
    await page.getByRole('button', { name: 'Inject Vulnerability' }).click()

    // sqli-string-concat is the first template
    await expect(page.getByRole('button', { name: /sqli/ })).toBeVisible()
    // Use locator('span') to avoid strict-mode violation: the button element and
    // its inner <span> both contain the text "CWE-89" / "CWE-79"
    await expect(page.locator('span', { hasText: 'CWE-89' }).first()).toBeVisible()
    await expect(page.locator('span', { hasText: 'critical' }).first()).toBeVisible()

    // xss-reflected is the second template
    await expect(page.getByRole('button', { name: /xss/ })).toBeVisible()
    await expect(page.locator('span', { hasText: 'CWE-79' }).first()).toBeVisible()
  })

  // -----------------------------------------------------------------------
  // 10. Verify the inject POST body contains expected fields
  // -----------------------------------------------------------------------
  test('inject POST body contains template_id, file_path, and substitutions', async ({ page }) => {
    let capturedBody: Record<string, unknown> | null = null

    await page.route(`**/api/datasets/${DATASET_NAME}/inject`, async (route) => {
      if (route.request().method() === 'POST' && !route.request().url().includes('preview')) {
        capturedBody = route.request().postDataJSON() as Record<string, unknown>
      }
      return route.fallback()
    })

    await page.getByRole('button', { name: 'Inject Vulnerability' }).click()
    await page.getByRole('button', { name: new RegExp(TEMPLATE_VULN_CLASS) }).first().click()

    const modalFileTree = page.locator('.fixed.inset-0')
    // depth-0 "src" is already expanded; only expand depth-1 "auth" then click leaf
    await modalFileTree.getByRole('button', { name: /📁 auth/ }).click()
    await modalFileTree.locator('button', { hasText: 'login.py' }).click()

    await page.locator('.fixed.inset-0').locator('input[type="text"]').fill(PLACEHOLDER_VALUE)
    await page.getByRole('button', { name: 'Preview Injection' }).click()
    await expect(page.getByText('Inject Vulnerability — Step 4/5')).toBeVisible()

    const injectDone = page.waitForRequest(
      (req) =>
        req.url().includes(`/api/datasets/${DATASET_NAME}/inject`) &&
        !req.url().includes('preview') &&
        req.method() === 'POST'
    )
    await page.getByRole('button', { name: /Confirm & Inject/ }).click()
    await injectDone

    expect(capturedBody).not.toBeNull()
    expect(capturedBody).toHaveProperty('template_id', TEMPLATE_ID)
    expect(capturedBody).toHaveProperty('file_path', LEAF_FILE)
    expect((capturedBody as Record<string, unknown>).substitutions).toMatchObject({
      [PLACEHOLDER_KEY]: PLACEHOLDER_VALUE,
    })
  })
})
