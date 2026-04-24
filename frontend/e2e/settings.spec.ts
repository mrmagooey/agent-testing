import { test, expect, type Page, type Route } from '@playwright/test'
import { mockApi } from './helpers/mockApi'

// ---------------------------------------------------------------------------
// Shared fixtures — ProviderDTO shapes matching exact wire format.
// ---------------------------------------------------------------------------

const builtinProvider = {
  id: 'builtin-openai',
  name: 'openai',
  display_name: 'OpenAI',
  adapter: 'openai_compat',
  model_id: 'gpt-4o',
  api_base: null,
  auth_type: 'api_key',
  region: null,
  enabled: true,
  api_key_masked: null,
  last_probe_at: '2026-04-24T10:00:00Z',
  last_probe_status: 'fresh',
  last_probe_error: null,
  source: 'builtin',
}

const customProvider = {
  id: 'custom-myprovider',
  name: 'myprovider',
  display_name: 'My Provider',
  adapter: 'openai_compat',
  model_id: 'my-model',
  api_base: 'https://api.example.com/v1',
  auth_type: 'api_key',
  region: null,
  enabled: true,
  api_key_masked: '••••••••abcd',
  last_probe_at: '2026-04-24T09:00:00Z',
  last_probe_status: 'stale',
  last_probe_error: null,
  source: 'custom',
}

const defaultSettings = {
  allow_unavailable_models: false,
  evidence_assessor: 'heuristic',
  evidence_judge_model: null,
}

// ---------------------------------------------------------------------------
// Helper — registers mock routes for all Settings-specific endpoints.
// Call BEFORE any test-specific page.route() overrides so that those
// (registered last) win via Playwright's LIFO handler ordering.
// ---------------------------------------------------------------------------

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function mockSettingsApis(page: Page) {
  await page.route('**/api/llm-providers', (route) => {
    const method = route.request().method()
    if (method === 'GET') {
      return json(route, { builtin: [builtinProvider], custom: [customProvider] })
    }
    if (method === 'POST') {
      const body = route.request().postDataJSON() as Record<string, unknown>
      if (body.name === 'existing') {
        return json(route, { detail: 'A provider with this name already exists' }, 409)
      }
      const created = {
        ...customProvider,
        id: `custom-${body.name}`,
        name: body.name as string,
        display_name: body.display_name as string,
        adapter: body.adapter as string,
        model_id: body.model_id as string,
        api_base: (body.api_base as string | null) ?? null,
        auth_type: body.auth_type as string,
        region: (body.region as string | null) ?? null,
        api_key_masked: body.api_key ? '••••••••newk' : null,
        last_probe_status: 'fresh',
      }
      return json(route, created, 201)
    }
    return route.continue()
  })

  await page.route('**/api/llm-providers/**', (route) => {
    const method = route.request().method()
    const url = new URL(route.request().url())
    const path = url.pathname.replace(/^\/api/, '')
    if (method === 'PATCH') {
      const body = route.request().postDataJSON() as Record<string, unknown>
      return json(route, { ...customProvider, ...body })
    }
    if (method === 'DELETE') {
      return route.fulfill({ status: 204, body: '' })
    }
    if (path.endsWith('/probe') && method === 'POST') {
      return json(route, { ...customProvider, last_probe_status: 'fresh', last_probe_at: new Date().toISOString() })
    }
    return route.continue()
  })

  await page.route('**/api/settings/defaults', (route) => {
    const method = route.request().method()
    if (method === 'GET') {
      return json(route, defaultSettings)
    }
    if (method === 'PATCH') {
      const body = route.request().postDataJSON() as Record<string, unknown>
      return json(route, { ...defaultSettings, ...body })
    }
    return route.continue()
  })
}

async function gotoSettings(page: Page) {
  await mockApi(page)
  await mockSettingsApis(page)
  await page.goto('/settings')
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

test('settings nav link exists and navigates to /settings', async ({ page }) => {
  await mockApi(page)
  await mockSettingsApis(page)
  await page.goto('/')
  const settingsLink = page.getByRole('navigation').getByRole('link', { name: 'Settings' })
  await expect(settingsLink).toBeVisible()
  await settingsLink.click()
  await expect(page).toHaveURL('/settings')
})

test('settings nav link is active on /settings', async ({ page }) => {
  await gotoSettings(page)
  const settingsLink = page.getByRole('navigation').getByRole('link', { name: 'Settings' })
  await expect(settingsLink).toHaveClass(/nav-cursor/)
})

test('/settings renders with LLM Providers tab active by default', async ({ page }) => {
  await gotoSettings(page)
  const providersTab = page.getByRole('tab', { name: 'LLM Providers' })
  await expect(providersTab).toBeVisible()
  await expect(providersTab).toHaveAttribute('data-state', 'active')
})

test('all three tab triggers are present', async ({ page }) => {
  await gotoSettings(page)
  await expect(page.getByRole('tab', { name: 'LLM Providers' })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Experiment Defaults' })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Tool Extensions' })).toBeVisible()
})

// ---------------------------------------------------------------------------
// LLM Providers tab — read path
// ---------------------------------------------------------------------------

test('built-in provider card renders without Edit/Delete/Probe buttons', async ({ page }) => {
  await gotoSettings(page)
  await expect(page.getByText('OpenAI').first()).toBeVisible()
  await expect(page.getByText(/Managed by ops/i)).toBeVisible()
  // Probe/Edit/Delete buttons only on the single custom provider card
  await expect(page.getByTitle('Probe now')).toHaveCount(1)
  await expect(page.getByTitle('Edit')).toHaveCount(1)
  await expect(page.getByTitle('Delete')).toHaveCount(1)
})

test('custom provider card has Edit, Delete, and Probe-now buttons', async ({ page }) => {
  await gotoSettings(page)
  await expect(page.getByTitle('Probe now')).toBeVisible()
  await expect(page.getByTitle('Edit')).toBeVisible()
  await expect(page.getByTitle('Delete')).toBeVisible()
})

test('probe status pill shows "stale" for stale provider and "fresh" for fresh', async ({ page }) => {
  await gotoSettings(page)
  // customProvider has last_probe_status: 'stale'
  await expect(page.getByText('stale', { exact: true })).toBeVisible()
  // builtinProvider has last_probe_status: 'fresh'
  await expect(page.getByText('fresh', { exact: true })).toBeVisible()
})

test('probe status pill shows "failed" when provider status is failed', async ({ page }) => {
  await mockApi(page)
  await mockSettingsApis(page)
  // Override the llm-providers GET to return a failed provider (registered last = wins)
  await page.route('**/api/llm-providers', (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const failedProvider = { ...customProvider, last_probe_status: 'failed', last_probe_error: 'Connection refused' }
    return json(route, { builtin: [], custom: [failedProvider] })
  })
  await page.goto('/settings')
  await expect(page.getByText('failed', { exact: true })).toBeVisible()
})

test('probe status pill shows "unknown" when last_probe_status is null', async ({ page }) => {
  await mockApi(page)
  await mockSettingsApis(page)
  await page.route('**/api/llm-providers', (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const noStatusProvider = { ...customProvider, last_probe_status: null, last_probe_error: null, last_probe_at: null }
    return json(route, { builtin: [], custom: [noStatusProvider] })
  })
  await page.goto('/settings')
  await expect(page.getByText('unknown', { exact: true })).toBeVisible()
})

test('last_probe_error is shown when present', async ({ page }) => {
  await mockApi(page)
  await mockSettingsApis(page)
  await page.route('**/api/llm-providers', (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const errorProvider = {
      ...customProvider,
      last_probe_status: 'failed',
      last_probe_error: 'Connection timeout after 10s',
    }
    return json(route, { builtin: [], custom: [errorProvider] })
  })
  await page.goto('/settings')
  await expect(page.getByText('Connection timeout after 10s')).toBeVisible()
})

test('masked api key renders for custom provider with stored key', async ({ page }) => {
  await gotoSettings(page)
  // customProvider.api_key_masked is '••••••••abcd'
  await expect(page.getByText('••••••••abcd')).toBeVisible()
})

test('empty state shows when no custom providers returned', async ({ page }) => {
  await mockApi(page)
  await mockSettingsApis(page)
  await page.route('**/api/llm-providers', (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    return json(route, { builtin: [], custom: [] })
  })
  await page.goto('/settings')
  await expect(page.getByText(/No custom providers yet/)).toBeVisible()
  // Inline Add button in the empty state
  await expect(page.getByRole('button', { name: /Add custom provider/i }).last()).toBeVisible()
})

// ---------------------------------------------------------------------------
// Add custom provider — happy path
// ---------------------------------------------------------------------------

test('Add Custom Provider modal opens when button clicked', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('button', { name: 'Add Custom Provider' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('Add Custom Provider')).toBeVisible()
})

test('add provider happy path: POST body matches entered fields; modal closes', async ({ page }) => {
  await gotoSettings(page)

  const postPromise = page.waitForRequest(
    (req) => req.url().includes('/api/llm-providers') && req.method() === 'POST',
  )

  await page.getByRole('button', { name: 'Add Custom Provider' }).click()
  const dialog = page.getByRole('dialog')

  await dialog.getByPlaceholder('my-provider').fill('newprovider')
  await dialog.getByPlaceholder('My Provider').fill('New Provider')

  // Select adapter: openai_compat
  await dialog.getByRole('combobox').nth(0).click()
  await page.getByRole('option', { name: 'openai_compat' }).click()

  await dialog.getByPlaceholder('gpt-4o').fill('gpt-4-turbo')
  await dialog.getByPlaceholder('https://api.example.com/v1').fill('https://api.newprovider.com/v1')

  // Select auth_type: api_key
  await dialog.getByRole('combobox').nth(1).click()
  await page.getByRole('option', { name: 'api_key' }).click()

  // api_key input in Add modal has no placeholder; locate by type="password" inside dialog
  await dialog.locator('input[type="password"]').fill('sk-testkey')

  await dialog.getByRole('button', { name: 'Add Provider' }).click()

  const req = await postPromise
  const body = req.postDataJSON() as Record<string, unknown>

  expect(body.name).toBe('newprovider')
  expect(body.display_name).toBe('New Provider')
  expect(body.adapter).toBe('openai_compat')
  expect(body.model_id).toBe('gpt-4-turbo')
  expect(body.api_base).toBe('https://api.newprovider.com/v1')
  expect(body.auth_type).toBe('api_key')
  expect(body.api_key).toBe('sk-testkey')
  // No stray fields
  expect(body.region).toBeUndefined()

  // Modal closes after successful save
  await expect(dialog).not.toBeVisible()
})

// ---------------------------------------------------------------------------
// Add custom provider — validation and error paths
// ---------------------------------------------------------------------------

test('slug with invalid characters shows inline error', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('button', { name: 'Add Custom Provider' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByPlaceholder('my-provider').fill('Bad Name')
  await dialog.getByRole('button', { name: 'Add Provider' }).click()
  await expect(dialog.getByText(/lowercase letters/i)).toBeVisible()
})

test('slug over 32 chars shows "Maximum 32 characters" error', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('button', { name: 'Add Custom Provider' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByPlaceholder('my-provider').fill('a'.repeat(33))
  await dialog.getByRole('button', { name: 'Add Provider' }).click()
  await expect(dialog.getByText('Maximum 32 characters')).toBeVisible()
})

test('409 from server shows inline "already exists" error on name field', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('button', { name: 'Add Custom Provider' }).click()
  const dialog = page.getByRole('dialog')

  await dialog.getByPlaceholder('my-provider').fill('existing')
  await dialog.getByPlaceholder('My Provider').fill('Existing Provider')
  await dialog.getByRole('combobox').nth(0).click()
  await page.getByRole('option', { name: 'openai_compat' }).click()
  await dialog.getByPlaceholder('gpt-4o').fill('gpt-4o')
  await dialog.getByRole('combobox').nth(1).click()
  await page.getByRole('option', { name: 'api_key' }).click()

  await dialog.getByRole('button', { name: 'Add Provider' }).click()
  await expect(dialog.getByText(/already exists/i)).toBeVisible()
  // Modal stays open on 409
  await expect(dialog).toBeVisible()
})

test('switching adapter from openai_compat to anthropic_compat removes api_base from POST body', async ({ page }) => {
  // Regression test: adapter change must clear stale api_base (fix: 8d07b79)
  await gotoSettings(page)

  const postPromise = page.waitForRequest(
    (req) => req.url().includes('/api/llm-providers') && req.method() === 'POST',
  )

  await page.getByRole('button', { name: 'Add Custom Provider' }).click()
  const dialog = page.getByRole('dialog')

  await dialog.getByPlaceholder('my-provider').fill('compat-test')
  await dialog.getByPlaceholder('My Provider').fill('Compat Test')

  // Select openai_compat first and fill api_base
  await dialog.getByRole('combobox').nth(0).click()
  await page.getByRole('option', { name: 'openai_compat' }).click()
  await dialog.getByPlaceholder('https://api.example.com/v1').fill('https://api.example.com/v1')

  // Switch to anthropic_compat — api_base field disappears and must not be sent
  await dialog.getByRole('combobox').nth(0).click()
  await page.getByRole('option', { name: 'anthropic_compat' }).click()

  await dialog.getByPlaceholder('gpt-4o').fill('claude-3-haiku')
  await dialog.getByRole('combobox').nth(1).click()
  await page.getByRole('option', { name: 'api_key' }).click()

  await dialog.getByRole('button', { name: 'Add Provider' }).click()

  const req = await postPromise
  const body = req.postDataJSON() as Record<string, unknown>
  expect(body.adapter).toBe('anthropic_compat')
  // api_base must NOT be present after adapter switch
  expect(body.api_base).toBeUndefined()
})

test('switching auth_type from aws to api_key removes region from POST body', async ({ page }) => {
  // Regression test: auth_type change must clear stale region (fix: 8d07b79)
  await gotoSettings(page)

  const postPromise = page.waitForRequest(
    (req) => req.url().includes('/api/llm-providers') && req.method() === 'POST',
  )

  await page.getByRole('button', { name: 'Add Custom Provider' }).click()
  const dialog = page.getByRole('dialog')

  await dialog.getByPlaceholder('my-provider').fill('aws-test')
  await dialog.getByPlaceholder('My Provider').fill('AWS Test')
  await dialog.getByRole('combobox').nth(0).click()
  await page.getByRole('option', { name: 'bedrock' }).click()
  await dialog.getByPlaceholder('gpt-4o').fill('anthropic.claude-3-haiku')

  // Select aws auth_type and fill region (bedrock has no api_base, so auth_type is combobox nth(1))
  await dialog.getByRole('combobox').nth(1).click()
  await page.getByRole('option', { name: 'aws' }).click()
  await dialog.getByPlaceholder('us-east-1').fill('us-west-2')

  // Switch to api_key — region must be cleared
  await dialog.getByRole('combobox').nth(1).click()
  await page.getByRole('option', { name: 'api_key' }).click()

  await dialog.getByRole('button', { name: 'Add Provider' }).click()

  const req = await postPromise
  const body = req.postDataJSON() as Record<string, unknown>
  expect(body.auth_type).toBe('api_key')
  // region must NOT be present after auth_type switch
  expect(body.region).toBeUndefined()
})

// ---------------------------------------------------------------------------
// Edit modal
// ---------------------------------------------------------------------------

test('Edit modal opens pre-filled with provider values', async ({ page }) => {
  await gotoSettings(page)
  await page.getByTitle('Edit').click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  // Display name input is pre-filled (locate by its placeholder attribute)
  const displayInput = dialog.getByPlaceholder('My Provider')
  await expect(displayInput).toBeVisible()
  await expect(displayInput).toHaveValue('My Provider')
  // Title shows provider slug
  await expect(dialog.getByText(/Edit — myprovider/i)).toBeVisible()
  // Slug input is hidden in edit mode (isEdit = true → no Name field rendered)
  await expect(dialog.getByPlaceholder('my-provider')).not.toBeVisible()
})

test('edit provider: PATCH body contains display_name but not api_key when left blank', async ({ page }) => {
  await gotoSettings(page)

  const patchPromise = page.waitForRequest(
    (req) => req.url().includes('/api/llm-providers/') && req.method() === 'PATCH',
  )

  await page.getByTitle('Edit').click()
  const dialog = page.getByRole('dialog')

  // Clear display name and type a new one; leave api_key blank
  const displayNameInput = dialog.getByPlaceholder('My Provider')
  await displayNameInput.clear()
  await displayNameInput.fill('My Provider Updated')

  await dialog.getByRole('button', { name: 'Save Changes' }).click()

  const req = await patchPromise
  const body = req.postDataJSON() as Record<string, unknown>

  expect(body.display_name).toBe('My Provider Updated')
  // api_key was left blank — must NOT appear in PATCH body
  expect(body.api_key).toBeUndefined()
})

// ---------------------------------------------------------------------------
// Delete confirm
// ---------------------------------------------------------------------------

test('Delete confirm dialog has role="alertdialog"', async ({ page }) => {
  await gotoSettings(page)
  await page.getByTitle('Delete').click()
  const alertDialog = page.getByRole('alertdialog')
  await expect(alertDialog).toBeVisible()
  await expect(alertDialog.getByRole('heading', { name: 'Delete Provider' })).toBeVisible()
})

test('Cancel in delete dialog closes dialog without firing DELETE', async ({ page }) => {
  await gotoSettings(page)

  let deleteRequestFired = false
  page.on('request', (req) => {
    if (req.url().includes('/api/llm-providers/') && req.method() === 'DELETE') {
      deleteRequestFired = true
    }
  })

  await page.getByTitle('Delete').click()
  const alertDialog = page.getByRole('alertdialog')
  await expect(alertDialog).toBeVisible()

  await alertDialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(alertDialog).not.toBeVisible()
  expect(deleteRequestFired).toBe(false)
})

test('Confirm in delete dialog fires DELETE and list refreshes to empty state', async ({ page }) => {
  // Set up standard mocks first; then override with post-delete empty list
  await mockApi(page)
  await mockSettingsApis(page)

  // Override the llm-providers GET after delete to return empty custom list.
  // Registered LAST so it takes priority over mockSettingsApis for the second call.
  let deleted = false
  await page.route('**/api/llm-providers', (route) => {
    if (route.request().method() === 'GET') {
      const body = deleted
        ? { builtin: [], custom: [] }
        : { builtin: [], custom: [customProvider] }
      return json(route, body)
    }
    return route.continue()
  })

  // Override delete endpoint — registered after mockSettingsApis so it wins
  await page.route('**/api/llm-providers/custom-myprovider', (route) => {
    if (route.request().method() === 'DELETE') {
      deleted = true
      return route.fulfill({ status: 204, body: '' })
    }
    return route.continue()
  })

  await page.goto('/settings')
  await expect(page.getByText('My Provider')).toBeVisible()

  await page.getByTitle('Delete').click()
  const alertDialog = page.getByRole('alertdialog')
  await alertDialog.getByRole('button', { name: 'Delete' }).click()
  await expect(alertDialog).not.toBeVisible()

  // List refreshes → empty state appears
  await expect(page.getByText(/No custom providers yet/)).toBeVisible()
})

// ---------------------------------------------------------------------------
// Probe-now
// ---------------------------------------------------------------------------

test('Probe-now fires POST to /probe endpoint; card status updates to fresh', async ({ page }) => {
  await gotoSettings(page)
  // customProvider starts as 'stale'; builtin is 'fresh'
  await expect(page.getByText('stale', { exact: true })).toBeVisible()
  await expect(page.getByText('fresh', { exact: true })).toHaveCount(1)

  const probePromise = page.waitForRequest(
    (req) => req.url().includes('/probe') && req.method() === 'POST',
  )

  await page.getByTitle('Probe now').click()
  await probePromise

  // After probe response the custom card's pill changes from stale → fresh
  // (stale pill should be gone; both cards now show fresh)
  await expect(page.getByText('stale', { exact: true })).not.toBeVisible()
  await expect(page.getByText('fresh', { exact: true })).toHaveCount(2)
})

test('Probe-now button is disabled while request is in flight', async ({ page }) => {
  await mockApi(page)
  await mockSettingsApis(page)

  // Override probe to add delay so we can observe the disabled state
  await page.route('**/api/llm-providers/**', async (route) => {
    if (route.request().url().includes('/probe') && route.request().method() === 'POST') {
      await new Promise<void>((resolve) => setTimeout(resolve, 300))
      return json(route, { ...customProvider, last_probe_status: 'fresh' })
    }
    return route.continue()
  })

  await page.goto('/settings')
  await page.getByTitle('Probe now').click()
  // Button becomes disabled during the in-flight request
  await expect(page.getByTitle('Probe now')).toBeDisabled()
  // Wait for it to re-enable after response
  await expect(page.getByTitle('Probe now')).toBeEnabled({ timeout: 5000 })
})

// ---------------------------------------------------------------------------
// Experiment Defaults tab
// ---------------------------------------------------------------------------

test('clicking Experiment Defaults tab switches content', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('tab', { name: 'Experiment Defaults' }).click()
  await expect(page.getByRole('tab', { name: 'Experiment Defaults' })).toHaveAttribute('data-state', 'active')
  await expect(page.getByText('Allow unavailable models')).toBeVisible()
})

test('Defaults tab loads values from GET /api/settings/defaults', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('tab', { name: 'Experiment Defaults' }).click()
  // allow_unavailable_models is false in defaultSettings → switch aria-checked="false"
  // The switch has no accessible name (label is a sibling <p>, not associated via aria)
  // so locate by role="switch" which is unique on this tab
  const toggle = page.getByRole('switch')
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
})

test('Save button is disabled when form is clean', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('tab', { name: 'Experiment Defaults' }).click()
  await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled()
})

test('toggling allow_unavailable_models enables Save; PATCH body contains only that field', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('tab', { name: 'Experiment Defaults' }).click()

  await page.getByRole('switch').click()

  const saveButton = page.getByRole('button', { name: 'Save' })
  await expect(saveButton).toBeEnabled()

  const patchPromise = page.waitForRequest(
    (req) => req.url().includes('/api/settings/defaults') && req.method() === 'PATCH',
  )
  await saveButton.click()

  const req = await patchPromise
  const body = req.postDataJSON() as Record<string, unknown>

  // Only the changed field is sent
  expect(body.allow_unavailable_models).toBe(true)
  // Untouched fields are NOT included
  expect(body.evidence_assessor).toBeUndefined()
  expect(body.evidence_judge_model).toBeUndefined()
})

test('Save becomes disabled again after successful save', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('tab', { name: 'Experiment Defaults' }).click()

  await page.getByRole('switch').click()
  await page.getByRole('button', { name: 'Save' }).click()

  // After save, form is in sync with server response → Save should be disabled
  await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled()
})

test('switching evidence_assessor to llm_judge reveals judge model combobox with models', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('tab', { name: 'Experiment Defaults' }).click()

  // Evidence Judge Model label should not be visible with heuristic
  await expect(page.getByText('Evidence Judge Model')).not.toBeVisible()

  // Switch evidence_assessor to llm_judge
  const assessorSelect = page.getByRole('combobox')
  await assessorSelect.click()
  await page.getByRole('option', { name: 'llm_judge' }).click()

  // Evidence Judge Model label should now appear
  await expect(page.getByText('Evidence Judge Model')).toBeVisible()

  // The judge model combobox should list model IDs from GET /api/models
  const judgeSelect = page.getByRole('combobox').nth(1)
  await judgeSelect.click()
  await expect(page.getByRole('option', { name: 'gpt-4o', exact: true })).toBeVisible()
})

// ---------------------------------------------------------------------------
// Tool Extensions tab
// ---------------------------------------------------------------------------

test('clicking Tool Extensions tab switches content', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('tab', { name: 'Tool Extensions' }).click()
  await expect(page.getByRole('tab', { name: 'Tool Extensions' })).toHaveAttribute('data-state', 'active')
})

test('Tool Extensions tab renders all three extensions', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('tab', { name: 'Tool Extensions' }).click()
  await expect(page.getByText('Tree-sitter').first()).toBeVisible()
  await expect(page.getByText('LSP').first()).toBeVisible()
  await expect(page.getByText('DevDocs').first()).toBeVisible()
})

test('Tool Extensions tab shows "Configured via Helm" helper text', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('tab', { name: 'Tool Extensions' }).click()
  await expect(page.getByText(/Configured via Helm/i)).toBeVisible()
})

test('Tool Extensions tab shows two available and one unavailable pill', async ({ page }) => {
  await gotoSettings(page)
  await page.getByRole('tab', { name: 'Tool Extensions' }).click()
  // Tree-sitter and LSP are available; DevDocs is not
  await expect(page.getByText('available', { exact: true })).toHaveCount(2)
  await expect(page.getByText('unavailable', { exact: true })).toHaveCount(1)
})
