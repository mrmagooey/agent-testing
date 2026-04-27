/**
 * PromptInjectionViewer e2e tests
 *
 * Covers the three mutually-exclusive rendering paths of PromptInjectionViewer:
 *  1. Injection-applied path  — both clean_prompt and injected_prompt present
 *  2. Profile-modifier path   — review_profile_modifier set, no injection
 *  3. Plain path              — no injection, no modifier
 *
 * The existing run-detail-interactions.spec.ts already covers the basic
 * expand/collapse mechanic — these tests focus on banner content, template_id
 * rendering, per-side diff filtering, and path-distinction logic.
 */

import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'
const BASE_URL = `/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`
const RUN_ENDPOINT = `**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`

// ---------------------------------------------------------------------------
// Fixture helpers
// ---------------------------------------------------------------------------

type RunFull = Record<string, unknown> & {
  prompt_snapshot: Record<string, unknown>
}

function loadRunFull(): RunFull {
  return JSON.parse(
    readFileSync(join(__dirname, 'fixtures/run-full.json'), 'utf-8')
  ) as RunFull
}

/** Deep-clone the base fixture and apply overrides to prompt_snapshot. */
function withPromptSnapshot(overrides: Record<string, unknown>): RunFull {
  const base = loadRunFull()
  return {
    ...base,
    prompt_snapshot: {
      ...base.prompt_snapshot,
      ...overrides,
    },
  }
}

function fulfillRun(route: import('@playwright/test').Route, body: unknown) {
  // Only intercept GET; chain to the next handler for any other method
  // (consistent with sibling specs that use route.fallback() for non-matches).
  if (route.request().method() !== 'GET') return route.fallback()
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

// ---------------------------------------------------------------------------
// beforeEach — shared setup
// ---------------------------------------------------------------------------

test.beforeEach(async ({ page }) => {
  await mockApi(page)
  await page.goto(BASE_URL)
})

// ---------------------------------------------------------------------------
// Helper: open the Prompt Snapshot collapsible
// ---------------------------------------------------------------------------

async function expandPromptSnapshot(page: import('@playwright/test').Page) {
  const toggle = page.getByRole('button', { name: 'Prompt Snapshot' })
  await toggle.click()
}

// ===========================================================================
// PATH 1 — Injection applied (default fixture)
// ===========================================================================

test('injection path — amber banner shows "Injection applied." text', async ({ page }) => {
  await expandPromptSnapshot(page)

  // The amber banner must be visible and contain the "Injection applied." label
  await expect(page.getByText('Injection applied.')).toBeVisible()
})

test('injection path — banner shows template_id in a <code> element and diff panes are visible; profile-modifier and plain notes are absent', async ({ page }) => {
  await expandPromptSnapshot(page)

  // template_id is rendered inside a <code> element
  await expect(page.locator('code').filter({ hasText: 'hardcoded-secrets-v1' })).toBeVisible()

  // Both diff panes must be present
  await expect(page.getByTestId('clean-prompt-pane')).toBeVisible()
  await expect(page.getByTestId('injected-prompt-pane')).toBeVisible()

  // Incompatible banners/notes must be absent
  await expect(page.getByText('Profile modifier active.')).not.toBeVisible()
  await expect(page.getByText('No injection applied for this run.')).not.toBeVisible()
})

test('injection path — inserted line appears in injected pane but not in clean pane', async ({ page }) => {
  await expandPromptSnapshot(page)

  const injectedPane = page.getByTestId('injected-prompt-pane')
  const cleanPane = page.getByTestId('clean-prompt-pane')

  // The added line is only rendered on the injected side
  await expect(injectedPane.getByText(/IMPORTANT.*hardcoded/)).toBeVisible()

  // On the clean side, the added line must not appear
  await expect(cleanPane.getByText(/IMPORTANT.*hardcoded/)).toHaveCount(0)
})

test('injection path — system-prompt-pane and user-message-pane are not in the DOM', async ({ page }) => {
  await expandPromptSnapshot(page)

  // Path 1 uses DiffPane testids, not PromptPane testids
  await expect(page.getByTestId('system-prompt-pane')).not.toBeVisible()
  await expect(page.getByTestId('user-message-pane')).not.toBeVisible()
})

// ===========================================================================
// PATH 1 variant — injection without template_id
// ===========================================================================

test('injection without template_id — diff panes render but amber banner is absent', async ({ page }) => {
  // Override: both prompts present but no template id
  const modified = withPromptSnapshot({ injection_template_id: null })
  await page.route(RUN_ENDPOINT, (route) => fulfillRun(route, modified))
  await page.goto(BASE_URL)

  await expandPromptSnapshot(page)

  // DiffPanes still render (path 1 logic still applies)
  await expect(page.getByTestId('clean-prompt-pane')).toBeVisible()
  await expect(page.getByTestId('injected-prompt-pane')).toBeVisible()

  // The banner is gated on injection_template_id — must be absent
  await expect(page.getByText('Injection applied.')).not.toBeVisible()
  await expect(page.locator('code').filter({ hasText: 'hardcoded-secrets-v1' })).not.toBeVisible()
})

// ===========================================================================
// PATH 2 — Profile modifier active
// ===========================================================================

test('profile-modifier path — amber banner and system/user panes visible; diff panes absent', async ({ page }) => {
  const modified = withPromptSnapshot({
    injected_prompt: null,
    injection_template_id: null,
    review_profile_modifier: 'Focus exclusively on cryptographic flaws.',
  })
  await page.route(RUN_ENDPOINT, (route) => fulfillRun(route, modified))
  await page.goto(BASE_URL)

  await expandPromptSnapshot(page)

  // Profile-modifier banner must be visible
  await expect(page.getByText('Profile modifier active.')).toBeVisible()

  // System and user panes rendered in this path
  await expect(page.getByTestId('system-prompt-pane')).toBeVisible()
  await expect(page.getByTestId('user-message-pane')).toBeVisible()

  // Diff panes and injection banner must be absent
  await expect(page.getByTestId('clean-prompt-pane')).not.toBeVisible()
  await expect(page.getByTestId('injected-prompt-pane')).not.toBeVisible()
  await expect(page.getByText('Injection applied.')).not.toBeVisible()
})

test('profile-modifier path — system-prompt-pane contains original prompt and "[Profile modifier injected]" separator', async ({ page }) => {
  const systemPromptBase = 'You are an expert security code reviewer. Your task is to perform a thorough security audit of the provided codebase.'
  const modifier = 'Focus exclusively on cryptographic flaws.'
  const modified = withPromptSnapshot({
    injected_prompt: null,
    injection_template_id: null,
    review_profile_modifier: modifier,
  })
  await page.route(RUN_ENDPOINT, (route) => fulfillRun(route, modified))
  await page.goto(BASE_URL)

  await expandPromptSnapshot(page)

  const systemPane = page.getByTestId('system-prompt-pane')

  // Must contain the original system prompt text
  await expect(systemPane).toContainText(systemPromptBase.slice(0, 40))

  // Must contain the modifier separator (case-sensitive)
  await expect(systemPane).toContainText('[Profile modifier injected]')
})

// ===========================================================================
// PATH 3 — Plain run (no injection, no modifier)
// ===========================================================================

test('plain path — italic "No injection applied" note and system/user panes are visible; no banners', async ({ page }) => {
  const modified = withPromptSnapshot({
    injected_prompt: null,
    injection_template_id: null,
    review_profile_modifier: null,
  })
  await page.route(RUN_ENDPOINT, (route) => fulfillRun(route, modified))
  await page.goto(BASE_URL)

  await expandPromptSnapshot(page)

  // Plain-run italic note must appear
  await expect(page.getByText('No injection applied for this run.')).toBeVisible()

  // System and user panes present
  await expect(page.getByTestId('system-prompt-pane')).toBeVisible()
  await expect(page.getByTestId('user-message-pane')).toBeVisible()

  // No injection or profile-modifier banners
  await expect(page.getByText('Injection applied.')).not.toBeVisible()
  await expect(page.getByText('Profile modifier active.')).not.toBeVisible()
})
