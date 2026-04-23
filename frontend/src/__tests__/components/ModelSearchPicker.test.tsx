import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ModelSearchPicker from '../../components/ModelSearchPicker'
import type { ModelProviderGroup } from '../../api/client'

// ─── Fixtures ─────────────────────────────────────────────────────────────────

/** Returns an ISO-8601 UTC timestamp N minutes in the past. */
function minutesAgo(n: number): string {
  return new Date(Date.now() - n * 60_000).toISOString().replace(/\.\d{3}Z$/, 'Z')
}

const GROUPS: ModelProviderGroup[] = [
  {
    provider: 'openai',
    probe_status: 'fresh',
    fetched_at: minutesAgo(3),
    last_error: null,
    models: [
      { id: 'gpt-4o', display_name: 'GPT-4o', status: 'available' },
      { id: 'gpt-3.5-turbo', display_name: 'GPT-3.5 Turbo', status: 'available' },
      { id: 'gpt-4-vision', display_name: null, status: 'not_listed' },
    ],
  },
  {
    provider: 'anthropic',
    probe_status: 'stale',
    fetched_at: minutesAgo(12),
    last_error: 'Connection timeout after 30s',
    models: [
      { id: 'claude-3-opus', display_name: 'Claude 3 Opus', status: 'available' },
      { id: 'claude-3-haiku', display_name: 'Claude 3 Haiku', status: 'key_missing' },
    ],
  },
  {
    provider: 'mistral',
    probe_status: 'failed',
    fetched_at: null,
    last_error: 'API returned 503',
    models: [
      { id: 'mistral-large', display_name: 'Mistral Large', status: 'probe_failed' },
    ],
  },
  {
    provider: 'cohere',
    probe_status: 'disabled',
    fetched_at: null,
    last_error: null,
    models: [
      { id: 'command-r', display_name: 'Command R', status: 'key_missing' },
      { id: 'command-r-plus', display_name: 'Command R+', status: 'key_missing' },
    ],
  },
]

function renderPicker(
  overrides: Partial<Parameters<typeof ModelSearchPicker>[0]> = {},
) {
  const onChange = vi.fn()
  const props = {
    groups: GROUPS,
    selected: [] as string[],
    onChange,
    ...overrides,
  }
  const result = render(<ModelSearchPicker {...props} />)
  return { ...result, onChange }
}

// ─── Group rendering ──────────────────────────────────────────────────────────

describe('group rendering', () => {
  it('renders every provider group header', () => {
    renderPicker({ allowUnavailableDefault: true })
    expect(screen.getByText('Openai')).toBeInTheDocument()
    expect(screen.getByText('Anthropic')).toBeInTheDocument()
    expect(screen.getByText('Mistral')).toBeInTheDocument()
    expect(screen.getByText('Cohere')).toBeInTheDocument()
  })

  it('shows all-key_missing placeholder instead of rows', () => {
    // cohere has all key_missing — its placeholder should appear by default
    renderPicker()
    expect(
      screen.getByText(/No Cohere key configured/i),
    ).toBeInTheDocument()
    // anthropic has mixed (one available, one key_missing) — no placeholder for it
    expect(screen.queryByText(/No Anthropic key configured/i)).not.toBeInTheDocument()
  })

  it('omits groups with zero models', () => {
    const groups: ModelProviderGroup[] = [
      ...GROUPS,
      { provider: 'empty-provider', probe_status: 'fresh', fetched_at: null, last_error: null, models: [] },
    ]
    renderPicker({ groups })
    expect(screen.queryByText('Empty-provider')).not.toBeInTheDocument()
  })
})

// ─── Selection ────────────────────────────────────────────────────────────────

describe('selection', () => {
  it('clicking an available row calls onChange with that id added', async () => {
    const user = userEvent.setup()
    const { onChange } = renderPicker()

    const item = screen.getByRole('option', { name: /GPT-4o/ })
    await user.click(item)

    expect(onChange).toHaveBeenCalledWith(['gpt-4o'])
  })

  it('clicking an already-selected row calls onChange without that id', async () => {
    const user = userEvent.setup()
    const { onChange } = renderPicker({ selected: ['gpt-4o'] })

    const item = screen.getByRole('option', { name: /GPT-4o/ })
    await user.click(item)

    expect(onChange).toHaveBeenCalledWith([])
  })
})

// ─── Unavailable visibility ───────────────────────────────────────────────────

describe('unavailable model visibility', () => {
  it('hides unavailable rows by default', () => {
    renderPicker()
    // gpt-4-vision is not_listed → hidden unless selected
    expect(screen.queryByText('gpt-4-vision')).not.toBeInTheDocument()
    // mistral-large is probe_failed → hidden
    expect(screen.queryByText('Mistral Large')).not.toBeInTheDocument()
  })

  it('shows unavailable rows after toggling "Show unavailable"', async () => {
    const user = userEvent.setup()
    renderPicker()

    const toggle = screen.getByLabelText('Show unavailable')
    await user.click(toggle)

    // Now unavailable rows are visible
    expect(screen.getByText('Mistral Large')).toBeInTheDocument()
    // gpt-4-vision has null display_name so falls back to id
    expect(screen.getByText('gpt-4-vision')).toBeInTheDocument()
  })

  it('hides unavailable rows again when toggle is flipped back', async () => {
    const user = userEvent.setup()
    renderPicker()

    const toggle = screen.getByLabelText('Show unavailable')
    await user.click(toggle) // show
    await user.click(toggle) // hide again

    expect(screen.queryByText('Mistral Large')).not.toBeInTheDocument()
  })

  it('always shows unavailable rows for currently-selected ids', () => {
    // claude-3-haiku is key_missing but is selected — it should appear in the list
    renderPicker({ selected: ['claude-3-haiku'] })
    // The row (option) should be visible even though unavailable
    expect(screen.getByRole('option', { name: /Claude 3 Haiku/ })).toBeInTheDocument()
  })
})

// ─── Search filtering ─────────────────────────────────────────────────────────

describe('search filtering', () => {
  it('filters visible rows by display_name (case-insensitive)', async () => {
    const user = userEvent.setup()
    renderPicker()

    const input = screen.getByPlaceholderText('Search models…')
    await user.type(input, 'gpt-4o')

    expect(screen.getByRole('option', { name: /GPT-4o/ })).toBeInTheDocument()
    // GPT-3.5 Turbo should not be visible
    expect(screen.queryByRole('option', { name: /GPT-3\.5 Turbo/ })).not.toBeInTheDocument()
  })

  it('filters by model id', async () => {
    const user = userEvent.setup()
    renderPicker({ allowUnavailableDefault: true })

    const input = screen.getByPlaceholderText('Search models…')
    await user.type(input, 'claude-3-opus')

    expect(screen.getByRole('option', { name: /Claude 3 Opus/ })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /GPT-4o/ })).not.toBeInTheDocument()
  })

  it('is case-insensitive', async () => {
    const user = userEvent.setup()
    renderPicker()

    const input = screen.getByPlaceholderText('Search models…')
    await user.type(input, 'GPT-3.5')

    expect(screen.getByRole('option', { name: /GPT-3\.5 Turbo/ })).toBeInTheDocument()
  })
})

// ─── Backspace removes last pill ──────────────────────────────────────────────

describe('backspace in empty input', () => {
  it('removes the last selected id when input is empty', async () => {
    const user = userEvent.setup()
    const { onChange } = renderPicker({ selected: ['gpt-4o', 'claude-3-opus'] })

    const input = screen.getByPlaceholderText('Search models…')
    await user.click(input)
    await user.keyboard('{Backspace}')

    expect(onChange).toHaveBeenCalledWith(['gpt-4o'])
  })

  it('does not call onChange when input has text and backspace is pressed', async () => {
    const user = userEvent.setup()
    const { onChange } = renderPicker({ selected: ['gpt-4o'] })

    const input = screen.getByPlaceholderText('Search models…')
    await user.click(input)
    await user.type(input, 'gpt')
    await user.keyboard('{Backspace}') // removes 't' from input, not the pill

    expect(onChange).not.toHaveBeenCalled()
  })
})

// ─── Probe status indicators ──────────────────────────────────────────────────

describe('probe status indicators', () => {
  it('renders clock icon for stale provider', () => {
    renderPicker()
    expect(screen.getByTestId('probe-stale')).toBeInTheDocument()
    expect(screen.getByTestId('probe-stale')).toHaveAttribute('title', expect.stringMatching(/stale/i))
  })

  it('renders warning icon for failed provider', () => {
    renderPicker({ allowUnavailableDefault: true })
    expect(screen.getByTestId('probe-failed')).toBeInTheDocument()
    expect(screen.getByTestId('probe-failed')).toHaveAttribute('title', expect.stringMatching(/failed/i))
  })

  it('renders muted text for disabled provider', () => {
    renderPicker()
    expect(screen.getByTestId('probe-disabled')).toBeInTheDocument()
    expect(screen.getByTestId('probe-disabled').textContent).toMatch(/live probing off/i)
  })

  it('renders no indicator for fresh provider', () => {
    renderPicker()
    // openai is fresh — no stale/failed/disabled indicator in its group
    const openaiHeader = screen.getByText('Openai').closest('[data-testid]')
    // No probe-fresh element exists
    expect(screen.queryByTestId('probe-fresh')).not.toBeInTheDocument()
  })
})

// ─── Orphan pills ─────────────────────────────────────────────────────────────

describe('orphan selected ids', () => {
  it('renders pill with warning icon for id not in any group', () => {
    renderPicker({ selected: ['some-unknown-model-id'] })

    // Pill should be there, with warning
    const pill = screen.getByText('some-unknown-model-id').closest('span')
    expect(pill).toBeInTheDocument()
    // Warning icon is present in pill
    const warningIcon = within(pill!.parentElement!).getByLabelText('model no longer available')
    expect(warningIcon).toBeInTheDocument()
  })

  it('unknown id pill is still removable', async () => {
    const user = userEvent.setup()
    const { onChange } = renderPicker({ selected: ['some-unknown-model-id'] })

    const removeBtn = screen.getByRole('button', { name: /Remove some-unknown-model-id/i })
    await user.click(removeBtn)

    expect(onChange).toHaveBeenCalledWith([])
  })
})

// ─── Error state ──────────────────────────────────────────────────────────────

describe('error state', () => {
  it('shows error message when error prop is set', () => {
    renderPicker({ error: 'Select at least one model' })
    expect(screen.getByText('Select at least one model')).toBeInTheDocument()
  })
})

// ─── allowUnavailableDefault reactivity ───────────────────────────────────────

describe('allowUnavailableDefault reactivity', () => {
  it('syncs showUnavailable when allowUnavailableDefault flips from false to true', () => {
    const onChange = vi.fn()
    const props = {
      groups: GROUPS,
      selected: [] as string[],
      onChange,
      allowUnavailableDefault: false,
    }
    const { rerender } = render(<ModelSearchPicker {...props} />)

    // Initially, unavailable (probe_failed) model should be hidden
    expect(screen.queryByText('Mistral Large')).not.toBeInTheDocument()

    // Parent sets allowUnavailableDefault → true
    rerender(<ModelSearchPicker {...props} allowUnavailableDefault={true} />)

    // Now unavailable model should be visible
    expect(screen.getByText('Mistral Large')).toBeInTheDocument()
  })
})

// ─── Probe staleness timestamp badges ─────────────────────────────────────────

describe('probe staleness timestamp badges', () => {
  it('renders "last probed N min ago" for a fresh group with a recent fetched_at', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'openai',
        probe_status: 'fresh',
        fetched_at: minutesAgo(5),
        last_error: null,
        models: [{ id: 'gpt-4o', display_name: 'GPT-4o', status: 'available' }],
      },
    ]
    renderPicker({ groups })
    // Timestamp badge should say "last probed 5 min ago"
    const badge = screen.getByTestId('probe-timestamp')
    expect(badge.textContent).toMatch(/last probed 5 min ago/i)
  })

  it('renders "never probed" when fetched_at is null', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'mistral',
        probe_status: 'failed',
        fetched_at: null,
        last_error: null,
        models: [{ id: 'mistral-large', display_name: 'Mistral Large', status: 'probe_failed' }],
      },
    ]
    renderPicker({ groups, allowUnavailableDefault: true })
    const badge = screen.getByTestId('probe-timestamp')
    expect(badge.textContent).toMatch(/never probed/i)
  })

  it('renders "last probed just now" when fetched_at is under 1 minute ago', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'openai',
        probe_status: 'fresh',
        fetched_at: minutesAgo(0),
        last_error: null,
        models: [{ id: 'gpt-4o', display_name: 'GPT-4o', status: 'available' }],
      },
    ]
    renderPicker({ groups })
    const badge = screen.getByTestId('probe-timestamp')
    expect(badge.textContent).toMatch(/last probed just now/i)
  })

  it('renders last_error text inline when non-null', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'anthropic',
        probe_status: 'stale',
        fetched_at: minutesAgo(20),
        last_error: 'Connection timeout after 30s',
        models: [{ id: 'claude-3-opus', display_name: 'Claude 3 Opus', status: 'available' }],
      },
    ]
    renderPicker({ groups })
    const errorEl = screen.getByTestId('probe-last-error')
    expect(errorEl).toBeInTheDocument()
    expect(errorEl.textContent).toContain('Connection timeout after 30s')
  })

  it('does not render last_error element when last_error is null', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'openai',
        probe_status: 'fresh',
        fetched_at: minutesAgo(2),
        last_error: null,
        models: [{ id: 'gpt-4o', display_name: 'GPT-4o', status: 'available' }],
      },
    ]
    renderPicker({ groups })
    expect(screen.queryByTestId('probe-last-error')).not.toBeInTheDocument()
  })

  it('renders last_error for failed provider without error styling', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'mistral',
        probe_status: 'failed',
        fetched_at: null,
        last_error: 'API returned 503',
        models: [{ id: 'mistral-large', display_name: 'Mistral Large', status: 'probe_failed' }],
      },
    ]
    renderPicker({ groups, allowUnavailableDefault: true })
    const errorEl = screen.getByTestId('probe-last-error')
    expect(errorEl.textContent).toContain('API returned 503')
    // Should use gray (informational) text, not red error class
    expect(errorEl.className).not.toMatch(/text-red/)
    expect(errorEl.className).toMatch(/text-gray/)
  })
})

// ─── Phase 5: empty-state cards for disabled+empty providers ──────────────────

describe('empty-state card for disabled providers', () => {
  it('renders API-key empty-state card with env var name in <code>', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'openai',
        probe_status: 'disabled',
        fetched_at: null,
        last_error: 'OPENAI_API_KEY not set',
        models: [],
      },
    ]
    renderPicker({ groups })
    const card = screen.getByTestId('empty-provider-card')
    expect(card).toBeInTheDocument()
    // The env var name should appear inside a <code> element
    const codeEl = card.querySelector('code')
    expect(codeEl).not.toBeNull()
    expect(codeEl!.textContent).toBe('OPENAI_API_KEY')
    // Full copy text
    expect(card.textContent).toContain('OPENAI_API_KEY')
    expect(card.textContent).toContain('to see available models')
  })

  it('renders AWS credentials empty-state card for bedrock provider', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'bedrock',
        probe_status: 'disabled',
        fetched_at: null,
        last_error: 'AWS credentials not configured',
        models: [],
      },
    ]
    renderPicker({ groups })
    const card = screen.getByTestId('empty-provider-card')
    expect(card).toBeInTheDocument()
    expect(card.textContent).toContain('Configure AWS credentials')
  })

  it('renders fallback copy for unparseable last_error', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'unknown-provider',
        probe_status: 'disabled',
        fetched_at: null,
        last_error: 'some unexpected error message',
        models: [],
      },
    ]
    renderPicker({ groups })
    const card = screen.getByTestId('empty-provider-card')
    expect(card).toBeInTheDocument()
    // Should render the verbatim last_error as fallback
    expect(card.textContent).toContain('some unexpected error message')
  })

  it('renders "No models available" fallback when last_error is null', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'unknown-provider',
        probe_status: 'disabled',
        fetched_at: null,
        last_error: null,
        models: [],
      },
    ]
    renderPicker({ groups })
    const card = screen.getByTestId('empty-provider-card')
    expect(card).toBeInTheDocument()
    expect(card.textContent).toContain('No models available')
  })

  it('does NOT render empty-state card when probe_status is fresh with models', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'openai',
        probe_status: 'fresh',
        fetched_at: minutesAgo(2),
        last_error: null,
        models: [{ id: 'gpt-4o', display_name: 'GPT-4o', status: 'available' }],
      },
    ]
    renderPicker({ groups })
    expect(screen.queryByTestId('empty-provider-card')).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: /GPT-4o/ })).toBeInTheDocument()
  })

  it('keeps provider header and probe-disabled pill visible alongside the empty-state card', () => {
    const groups: ModelProviderGroup[] = [
      {
        provider: 'openai',
        probe_status: 'disabled',
        fetched_at: null,
        last_error: 'OPENAI_API_KEY not set',
        models: [],
      },
    ]
    renderPicker({ groups })
    // Provider header is present
    expect(screen.getByText('Openai')).toBeInTheDocument()
    // Probe disabled pill is present
    expect(screen.getByTestId('probe-disabled')).toBeInTheDocument()
    // Empty-state card is present
    expect(screen.getByTestId('empty-provider-card')).toBeInTheDocument()
  })
})
