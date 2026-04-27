import { test, expect } from '@playwright/test'
import { mockApi } from './helpers/mockApi'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const EXPERIMENT_ID = 'aaaaaaaa-0001-0001-0001-000000000001'
const RUN_ID = 'run-001-aaa'
const BASE_URL = `/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`
const RUN_ENDPOINT = `**/api/experiments/${EXPERIMENT_ID}/runs/${RUN_ID}`

type RunFull = Record<string, unknown>

function loadRunFull(): RunFull {
  return JSON.parse(
    readFileSync(join(__dirname, 'fixtures/run-full.json'), 'utf-8')
  ) as RunFull
}

async function overrideRunMessages(
  page: import('@playwright/test').Page,
  messages: unknown[]
) {
  await page.route(RUN_ENDPOINT, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const base = loadRunFull()
    const body = { ...base, messages }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })
  })
}

async function expandConversationTranscript(page: import('@playwright/test').Page) {
  const toggle = page.getByRole('button', { name: /Conversation Transcript/ })
  await toggle.click()
}

test.describe('ConversationViewer', () => {
  test.beforeEach(async ({ page }) => {
    await mockApi(page)
    await page.goto(BASE_URL)
  })

  test('empty conversation: collapsible header shows 0 messages and body shows no-messages text', async ({ page }) => {
    await overrideRunMessages(page, [])
    await page.goto(BASE_URL)

    await expect(
      page.getByRole('button', { name: /Conversation Transcript \(0 messages\)/ })
    ).toBeVisible()

    await expandConversationTranscript(page)

    await expect(page.getByText('No messages recorded.')).toBeVisible()
  })

  test('all three roles render with correct badge text', async ({ page }) => {
    await expandConversationTranscript(page)

    const messageContainers = page.locator('div.rounded-r-lg')
    await expect(messageContainers).toHaveCount(3)

    const badges = messageContainers.getByText(/^(user|assistant|tool)$/)
    await expect(badges).toHaveCount(3)
    expect(await badges.allTextContents()).toEqual(['user', 'assistant', 'tool'])
  })

  test('renders user role with blue border and background', async ({ page }) => {
    await expandConversationTranscript(page)

    const userBadge = page.getByText('user', { exact: true }).first()
    await expect(userBadge).toBeVisible()

    const container = userBadge.locator('xpath=ancestor::div[contains(@class, "rounded-r-lg")]')
    await expect(container).toHaveClass(/border-blue-500/)
    await expect(container).toHaveClass(/bg-blue-50/)
  })

  test('renders assistant role with green border and background', async ({ page }) => {
    await expandConversationTranscript(page)

    const assistantBadge = page.getByText('assistant', { exact: true }).first()
    await expect(assistantBadge).toBeVisible()

    const container = assistantBadge.locator('xpath=ancestor::div[contains(@class, "rounded-r-lg")]')
    await expect(container).toHaveClass(/border-green-500/)
    await expect(container).toHaveClass(/bg-green-50/)
  })

  test('renders tool role with gray border and background', async ({ page }) => {
    await expandConversationTranscript(page)

    const toolBadge = page.getByText('tool', { exact: true }).first()
    await expect(toolBadge).toBeVisible()

    const container = toolBadge.locator('xpath=ancestor::div[contains(@class, "rounded-r-lg")]')
    await expect(container).toHaveClass(/border-gray-400/)
    await expect(container).toHaveClass(/bg-gray-50/)
  })

  test('message ordering preserved across five messages in non-alphabetical role order', async ({ page }) => {
    const orderedMessages = [
      { role: 'tool', content: 'tool result 1', timestamp: '2026-04-17T08:02:01Z' },
      { role: 'user', content: 'user message 1', timestamp: '2026-04-17T08:02:02Z' },
      { role: 'tool', content: 'tool result 2', timestamp: '2026-04-17T08:02:03Z' },
      { role: 'assistant', content: 'assistant reply', timestamp: '2026-04-17T08:02:04Z' },
      { role: 'user', content: 'user message 2', timestamp: '2026-04-17T08:02:05Z' },
    ]
    await overrideRunMessages(page, orderedMessages)
    await page.goto(BASE_URL)

    await expandConversationTranscript(page)

    const badges = page.getByText(/^(user|assistant|tool)$/)
    await expect(badges).toHaveCount(5)
    const texts = await badges.allTextContents()
    expect(texts).toEqual(['tool', 'user', 'tool', 'assistant', 'user'])
  })

  test('timestamp rendered when present — time pattern visible next to badge', async ({ page }) => {
    await overrideRunMessages(page, [
      { role: 'user', content: 'hello', timestamp: '2026-04-17T08:02:00Z' },
    ])
    await page.goto(BASE_URL)

    await expandConversationTranscript(page)

    const userBadge = page.getByText('user', { exact: true }).first()
    const container = userBadge.locator('xpath=ancestor::div[contains(@class, "rounded-r-lg")]')

    // toLocaleTimeString produces formats like "8:02:00 AM" or "08:02:00" depending on locale.
    await expect(container.getByText(/\d{1,2}:\d{2}/)).toBeVisible()
  })

  test('timestamp absent — no time text appears in message container', async ({ page }) => {
    await overrideRunMessages(page, [
      { role: 'assistant', content: 'no timestamp here' },
    ])
    await page.goto(BASE_URL)

    await expandConversationTranscript(page)

    const assistantBadge = page.getByText('assistant', { exact: true }).first()
    const container = assistantBadge.locator('xpath=ancestor::div[contains(@class, "rounded-r-lg")]')

    await expect(container.getByText(/\d{1,2}:\d{2}/)).toHaveCount(0)
  })

  test('unknown role falls through to tool styling and badge shows raw role text', async ({ page }) => {
    await overrideRunMessages(page, [
      { role: 'system', content: 'system context message', timestamp: '2026-04-17T08:02:00Z' },
    ])
    await page.goto(BASE_URL)

    await expandConversationTranscript(page)

    const systemBadge = page.getByText('system', { exact: true }).first()
    await expect(systemBadge).toBeVisible()

    const container = systemBadge.locator('xpath=ancestor::div[contains(@class, "rounded-r-lg")]')
    await expect(container).toHaveClass(/border-gray-400/)
  })

  test('collapsible header count is dynamic — three messages shows correct count', async ({ page }) => {
    await overrideRunMessages(page, [
      { role: 'user', content: 'msg 1', timestamp: '2026-04-17T08:02:01Z' },
      { role: 'assistant', content: 'msg 2', timestamp: '2026-04-17T08:02:02Z' },
      { role: 'tool', content: 'msg 3', timestamp: '2026-04-17T08:02:03Z' },
    ])
    await page.goto(BASE_URL)

    await expect(
      page.getByRole('button', { name: /Conversation Transcript \(3 messages\)/ })
    ).toBeVisible()
  })
})
