import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Settings from '../../pages/Settings'
import type { ProviderDTO, ProviderListResponse, AppSettingsDefaults, ToolExtension } from '../../api/client'

// ─── Mock the API client ──────────────────────────────────────────────────

vi.mock('../../api/client', () => ({
  listLlmProviders: vi.fn(),
  createLlmProvider: vi.fn(),
  patchLlmProvider: vi.fn(),
  deleteLlmProvider: vi.fn(),
  probeLlmProvider: vi.fn(),
  getSettingsDefaults: vi.fn(),
  patchSettingsDefaults: vi.fn(),
  listModels: vi.fn(),
  listToolExtensions: vi.fn(),
  ApiError: class ApiError extends Error {
    readonly status: number
    readonly body: unknown
    constructor(message: string, status: number, body: unknown) {
      super(message)
      this.name = 'ApiError'
      this.status = status
      this.body = body
    }
  },
}))

import {
  listLlmProviders,
  createLlmProvider,
  patchLlmProvider,
  deleteLlmProvider,
  probeLlmProvider,
  getSettingsDefaults,
  patchSettingsDefaults,
  listModels,
  listToolExtensions,
  ApiError,
} from '../../api/client'

const mockListLlmProviders = vi.mocked(listLlmProviders)
const mockCreateLlmProvider = vi.mocked(createLlmProvider)
const mockPatchLlmProvider = vi.mocked(patchLlmProvider)
const mockDeleteLlmProvider = vi.mocked(deleteLlmProvider)
const mockProbeLlmProvider = vi.mocked(probeLlmProvider)
const mockGetSettingsDefaults = vi.mocked(getSettingsDefaults)
const mockPatchSettingsDefaults = vi.mocked(patchSettingsDefaults)
const mockListModels = vi.mocked(listModels)
const mockListToolExtensions = vi.mocked(listToolExtensions)

// ─── Fixtures ─────────────────────────────────────────────────────────────

function makeBuiltinProvider(overrides: Partial<ProviderDTO> = {}): ProviderDTO {
  return {
    id: 'builtin:openai',
    name: 'openai',
    display_name: 'OpenAI',
    adapter: 'openai_compat',
    model_id: 'gpt-4o',
    api_base: 'https://api.openai.com/v1',
    auth_type: 'api_key',
    region: null,
    enabled: true,
    api_key_masked: '••••••••abcd',
    last_probe_at: new Date(Date.now() - 3 * 60 * 1000).toISOString(),
    last_probe_status: 'fresh',
    last_probe_error: null,
    source: 'builtin',
    ...overrides,
  }
}

function makeCustomProvider(overrides: Partial<ProviderDTO> = {}): ProviderDTO {
  return {
    id: 'custom-uuid-1234',
    name: 'my-llm',
    display_name: 'My LLM',
    adapter: 'litellm',
    model_id: 'local-model',
    api_base: 'http://localhost:8080',
    auth_type: 'none',
    region: null,
    enabled: true,
    api_key_masked: null,
    last_probe_at: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
    last_probe_status: 'stale',
    last_probe_error: null,
    source: 'custom',
    ...overrides,
  }
}

const DEFAULT_PROVIDER_LIST: ProviderListResponse = {
  builtin: [makeBuiltinProvider()],
  custom: [makeCustomProvider()],
}

const DEFAULT_SETTINGS: AppSettingsDefaults = {
  allow_unavailable_models: false,
  evidence_assessor: 'heuristic',
  evidence_judge_model: null,
}

const DEFAULT_EXTENSIONS: ToolExtension[] = [
  { key: 'TREE_SITTER', label: 'Tree-sitter', available: true },
  { key: 'LSP', label: 'LSP', available: false },
  { key: 'DEVDOCS', label: 'DevDocs', available: true },
]

function setupDefaultMocks() {
  mockListLlmProviders.mockResolvedValue(DEFAULT_PROVIDER_LIST)
  mockGetSettingsDefaults.mockResolvedValue(DEFAULT_SETTINGS)
  mockListToolExtensions.mockResolvedValue(DEFAULT_EXTENSIONS)
  mockListModels.mockResolvedValue([])
}

function renderSettings() {
  return render(
    <MemoryRouter>
      <Settings />
    </MemoryRouter>,
  )
}

// ─── Setup / teardown ─────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
  setupDefaultMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ─── Settings page shell ──────────────────────────────────────────────────

describe('Settings — page shell', () => {
  it('renders Settings heading', async () => {
    renderSettings()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument()
    })
  })

  it('renders three tab triggers', async () => {
    renderSettings()
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /LLM Providers/i })).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: /Experiment Defaults/i })).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: /Tool Extensions/i })).toBeInTheDocument()
    })
  })

  it('defaults to LLM Providers tab', async () => {
    renderSettings()
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /LLM Providers/i })).toHaveAttribute('data-state', 'active')
    })
  })
})

// ─── ProvidersPanel ───────────────────────────────────────────────────────

describe('ProvidersPanel — render with one builtin + one custom', () => {
  it('shows builtin provider display_name', async () => {
    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('OpenAI')).toBeInTheDocument()
    })
  })

  it('shows custom provider display_name', async () => {
    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })
  })

  it('builtin provider has no Edit or Delete button', async () => {
    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('OpenAI')).toBeInTheDocument()
    })
    // Edit and Delete buttons are rendered for custom providers only.
    // Each is a <button> with a title attribute.
    const editButtons = screen.getAllByTitle('Edit')
    const deleteButtons = screen.getAllByTitle('Delete')
    // Exactly one Edit and one Delete — for the single custom provider.
    expect(editButtons).toHaveLength(1)
    expect(deleteButtons).toHaveLength(1)
  })

  it('builtin provider shows "Managed by ops" note', async () => {
    renderSettings()
    await waitFor(() => {
      expect(screen.getByText(/Managed by ops/i)).toBeInTheDocument()
    })
  })

  it('shows probe status pill for custom provider (stale)', async () => {
    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('stale')).toBeInTheDocument()
    })
  })

  it('shows empty state when no custom providers exist', async () => {
    mockListLlmProviders.mockResolvedValue({ builtin: [], custom: [] })
    renderSettings()
    await waitFor(() => {
      expect(screen.getByText(/No custom providers yet/i)).toBeInTheDocument()
    })
  })

  it('shows error card when listLlmProviders rejects', async () => {
    mockListLlmProviders.mockRejectedValue(new Error('Network failure'))
    renderSettings()
    await waitFor(() => {
      expect(screen.getByText(/Network failure/i)).toBeInTheDocument()
    })
  })
})

// ─── Add custom provider modal ────────────────────────────────────────────

describe('ProvidersPanel — Add modal', () => {
  it('opens modal when "Add Custom Provider" button is clicked', async () => {
    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Add Custom Provider/i }))

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })
  })

  it('submits valid form and asserts POST body and list refresh', async () => {
    const newProvider = makeCustomProvider({ id: 'new-uuid', name: 'acme-llm', display_name: 'Acme LLM' })
    mockCreateLlmProvider.mockResolvedValue(newProvider)
    // After creation, listLlmProviders returns the new provider too
    mockListLlmProviders
      .mockResolvedValueOnce(DEFAULT_PROVIDER_LIST)
      .mockResolvedValueOnce({
        builtin: DEFAULT_PROVIDER_LIST.builtin,
        custom: [...DEFAULT_PROVIDER_LIST.custom, newProvider],
      })

    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Add Custom Provider/i }))
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    // Fill form fields directly
    fireEvent.change(screen.getByPlaceholderText('my-provider'), {
      target: { value: 'acme-llm' },
    })
    fireEvent.change(screen.getByPlaceholderText('My Provider'), {
      target: { value: 'Acme LLM' },
    })
    fireEvent.change(screen.getByPlaceholderText('gpt-4o'), {
      target: { value: 'gpt-4o-mini' },
    })

    // Open adapter select and pick anthropic_compat (avoids api_base field appearing)
    const [adapterSelect] = screen.getAllByRole('combobox')
    fireEvent.click(adapterSelect)
    await waitFor(() => screen.getByText('anthropic_compat'))
    fireEvent.click(screen.getByText('anthropic_compat'))

    // Open auth_type select and pick none
    const selects = screen.getAllByRole('combobox')
    const authSelect = selects[selects.length - 1]
    fireEvent.click(authSelect)
    await waitFor(() => screen.getAllByText('none'))
    const noneItems = screen.getAllByText('none')
    fireEvent.click(noneItems[noneItems.length - 1])

    // Submit
    fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }))

    await waitFor(() => {
      expect(mockCreateLlmProvider).toHaveBeenCalled()
    })

    const createCall = mockCreateLlmProvider.mock.calls[0][0]
    expect(createCall.name).toBe('acme-llm')
    expect(createCall.display_name).toBe('Acme LLM')
    expect(createCall.adapter).toBe('anthropic_compat')
    expect(createCall.model_id).toBe('gpt-4o-mini')
    // auth_type is none — api_key must not appear in the body
    expect('api_key' in createCall).toBe(false)

    // List was refreshed after creation
    expect(mockListLlmProviders).toHaveBeenCalledTimes(2)
  })

  it('shows inline error "A provider with this name already exists" on 409', async () => {
    const conflictError = new ApiError('Provider name already exists', 409, {
      detail: "Provider name 'acme-llm' already exists",
    })
    mockCreateLlmProvider.mockRejectedValue(conflictError)

    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Add Custom Provider/i }))
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    // Fill minimal valid form
    fireEvent.change(screen.getByPlaceholderText('my-provider'), {
      target: { value: 'acme-llm' },
    })
    fireEvent.change(screen.getByPlaceholderText('My Provider'), {
      target: { value: 'Acme' },
    })
    const modelInput = screen.getByPlaceholderText('gpt-4o')
    fireEvent.change(modelInput, { target: { value: 'gpt-4o' } })

    // Select adapter
    const adapterSelect = screen.getAllByRole('combobox')[0]
    fireEvent.click(adapterSelect)
    await waitFor(() => {
      fireEvent.click(screen.getByText('anthropic_compat'))
    })

    // Select auth_type
    const authSelect = screen.getAllByRole('combobox')[1]
    fireEvent.click(authSelect)
    await waitFor(() => {
      // There may be multiple 'none' due to re-rendering; click the last one
      const noneOptions = screen.getAllByText('none')
      fireEvent.click(noneOptions[noneOptions.length - 1])
    })

    fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }))

    await waitFor(() => {
      expect(screen.getByText(/A provider with this name already exists/i)).toBeInTheDocument()
    })
  })
})

// ─── Delete confirm dialog ────────────────────────────────────────────────

describe('ProvidersPanel — Delete', () => {
  it('opens delete confirm when Delete button is clicked', async () => {
    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTitle('Delete'))

    await waitFor(() => {
      expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    })
    // Dialog should have a destructive Delete button (in addition to Cancel)
    const deleteButtons = screen.getAllByRole('button', { name: /^Delete$/i })
    expect(deleteButtons.length).toBeGreaterThanOrEqual(1)
  })

  it('calls deleteLlmProvider and refreshes list on confirm', async () => {
    mockDeleteLlmProvider.mockResolvedValue(undefined)
    mockListLlmProviders
      .mockResolvedValueOnce(DEFAULT_PROVIDER_LIST)
      .mockResolvedValueOnce({ builtin: DEFAULT_PROVIDER_LIST.builtin, custom: [] })

    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTitle('Delete'))
    await waitFor(() => {
      expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /^Delete$/i }))

    await waitFor(() => {
      expect(mockDeleteLlmProvider).toHaveBeenCalledWith('custom-uuid-1234')
    })
    expect(mockListLlmProviders).toHaveBeenCalledTimes(2)
  })
})

// ─── DefaultsPanel ────────────────────────────────────────────────────────

describe('DefaultsPanel — render', () => {
  it('shows allow_unavailable_models switch after switching to Experiment Defaults tab', async () => {
    const user = userEvent.setup()
    renderSettings()

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /Experiment Defaults/i })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('tab', { name: /Experiment Defaults/i }))

    await waitFor(() => {
      expect(screen.getByRole('switch')).toBeInTheDocument()
    })
  })

  it('Save button is disabled when form is not dirty', async () => {
    const user = userEvent.setup()
    renderSettings()

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /Experiment Defaults/i })).toBeInTheDocument()
    })
    await user.click(screen.getByRole('tab', { name: /Experiment Defaults/i }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save/i })).toBeDisabled()
    })
  })

  it('Save button enables when allow_unavailable_models is toggled', async () => {
    const user = userEvent.setup()
    renderSettings()

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /Experiment Defaults/i })).toBeInTheDocument()
    })
    await user.click(screen.getByRole('tab', { name: /Experiment Defaults/i }))

    await waitFor(() => {
      expect(screen.getByRole('switch')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('switch'))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save/i })).not.toBeDisabled()
    })
  })
})

describe('DefaultsPanel — PATCH sends only changed fields', () => {
  it('only sends allow_unavailable_models in patch body when only that field changed', async () => {
    const user = userEvent.setup()
    mockPatchSettingsDefaults.mockResolvedValue({
      ...DEFAULT_SETTINGS,
      allow_unavailable_models: true,
    })

    renderSettings()

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /Experiment Defaults/i })).toBeInTheDocument()
    })
    await user.click(screen.getByRole('tab', { name: /Experiment Defaults/i }))

    await waitFor(() => {
      expect(screen.getByRole('switch')).toBeInTheDocument()
    })

    // Toggle allow_unavailable_models
    await user.click(screen.getByRole('switch'))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Save/i })).not.toBeDisabled()
    })

    await user.click(screen.getByRole('button', { name: /Save/i }))

    await waitFor(() => {
      expect(mockPatchSettingsDefaults).toHaveBeenCalled()
    })

    const patchArg = mockPatchSettingsDefaults.mock.calls[0][0]
    // Only the changed field should be in the patch
    expect(patchArg).toEqual({ allow_unavailable_models: true })
    // Other fields should NOT be included
    expect('evidence_assessor' in patchArg).toBe(false)
    expect('evidence_judge_model' in patchArg).toBe(false)
  })
})

// ─── ToolExtensionsPanel ──────────────────────────────────────────────────

describe('ToolExtensionsPanel', () => {
  it('shows all three extensions after switching to Extensions tab', async () => {
    const user = userEvent.setup()
    renderSettings()

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /Tool Extensions/i })).toBeInTheDocument()
    })
    await user.click(screen.getByRole('tab', { name: /Tool Extensions/i }))

    await waitFor(() => {
      expect(screen.getByText('Tree-sitter')).toBeInTheDocument()
      // Each extension shows both label and key — use getAllByText to handle duplicates
      expect(screen.getAllByText('LSP').length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText('DevDocs')).toBeInTheDocument()
    })
  })

  it('shows "available" indicator for available extensions', async () => {
    const user = userEvent.setup()
    renderSettings()

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /Tool Extensions/i })).toBeInTheDocument()
    })
    await user.click(screen.getByRole('tab', { name: /Tool Extensions/i }))

    await waitFor(() => {
      // Tree-sitter and DevDocs are available
      const availableChips = screen.getAllByText('available')
      expect(availableChips.length).toBeGreaterThanOrEqual(2)
    })
  })

  it('shows "unavailable" indicator for unavailable extensions', async () => {
    const user = userEvent.setup()
    renderSettings()

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /Tool Extensions/i })).toBeInTheDocument()
    })
    await user.click(screen.getByRole('tab', { name: /Tool Extensions/i }))

    await waitFor(() => {
      expect(screen.getByText('unavailable')).toBeInTheDocument()
    })
  })

  it('shows "Configured via Helm" helper text', async () => {
    const user = userEvent.setup()
    renderSettings()

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /Tool Extensions/i })).toBeInTheDocument()
    })
    await user.click(screen.getByRole('tab', { name: /Tool Extensions/i }))

    await waitFor(() => {
      expect(screen.getByText(/Configured via Helm/i)).toBeInTheDocument()
    })
  })
})

// ─── Probe button ─────────────────────────────────────────────────────────

describe('ProvidersPanel — probe', () => {
  it('calls probeLlmProvider and updates card when Probe button is clicked', async () => {
    const probedProvider = makeCustomProvider({
      last_probe_status: 'fresh',
      last_probe_at: new Date().toISOString(),
    })
    mockProbeLlmProvider.mockResolvedValue(probedProvider)

    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })

    // 'stale' is shown before probe
    expect(screen.getByText('stale')).toBeInTheDocument()

    fireEvent.click(screen.getByTitle('Probe now'))

    await waitFor(() => {
      expect(mockProbeLlmProvider).toHaveBeenCalledWith('custom-uuid-1234')
    })
    // After probe, the status pill should update from 'stale' to 'fresh' on the custom card.
    // The builtin provider also shows 'fresh', so use getAllByText.
    await waitFor(() => {
      const freshPills = screen.getAllByText('fresh')
      expect(freshPills.length).toBeGreaterThanOrEqual(1)
      // 'stale' should no longer be shown
      expect(screen.queryByText('stale')).not.toBeInTheDocument()
    })
  })
})

// ─── Slug validation ──────────────────────────────────────────────────────

describe('ProvidersPanel — slug validation', () => {
  it('rejects a 33-character slug client-side before submit', async () => {
    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Add Custom Provider/i }))
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    // Type a 33-character slug (exceeds max 32)
    const slugInput = screen.getByPlaceholderText('my-provider')
    fireEvent.change(slugInput, { target: { value: 'a'.repeat(33) } })

    // Fill required fields so only slug validation fails
    fireEvent.change(screen.getByPlaceholderText('My Provider'), {
      target: { value: 'Too Long Provider' },
    })
    fireEvent.change(screen.getByPlaceholderText('gpt-4o'), {
      target: { value: 'gpt-4o' },
    })

    const adapterSelect = screen.getAllByRole('combobox')[0]
    fireEvent.click(adapterSelect)
    await waitFor(() => screen.getByText('anthropic_compat'))
    fireEvent.click(screen.getByText('anthropic_compat'))

    const authSelect = screen.getAllByRole('combobox')[screen.getAllByRole('combobox').length - 1]
    fireEvent.click(authSelect)
    await waitFor(() => screen.getAllByText('none'))
    const noneItems = screen.getAllByText('none')
    fireEvent.click(noneItems[noneItems.length - 1])

    fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }))

    // Client-side validation must reject before any API call
    await waitFor(() => {
      expect(screen.getByText('Maximum 32 characters')).toBeInTheDocument()
    })
    expect(mockCreateLlmProvider).not.toHaveBeenCalled()
  })
})

// ─── Stale api_base / region cleanup ─────────────────────────────────────

// Helper to open a Radix Select and pick an option by its visible text.
// After a value is selected, the trigger also shows that text, so getAllByText
// is used and the last occurrence (the dropdown item) is clicked.
async function pickSelectOption(trigger: HTMLElement, optionText: string) {
  fireEvent.click(trigger)
  await waitFor(() => {
    expect(screen.getAllByText(optionText).length).toBeGreaterThanOrEqual(1)
  })
  const options = screen.getAllByText(optionText)
  fireEvent.click(options[options.length - 1])
}

describe('ProvidersPanel — stale field cleanup', () => {
  it('does not send api_base in POST body when adapter is switched from openai_compat to bedrock', async () => {
    const newProvider = makeCustomProvider({ id: 'new-uuid-2', name: 'bedrock-llm', display_name: 'Bedrock LLM', adapter: 'bedrock', auth_type: 'aws' })
    mockCreateLlmProvider.mockResolvedValue(newProvider)
    mockListLlmProviders
      .mockResolvedValueOnce(DEFAULT_PROVIDER_LIST)
      .mockResolvedValueOnce({
        builtin: DEFAULT_PROVIDER_LIST.builtin,
        custom: [...DEFAULT_PROVIDER_LIST.custom, newProvider],
      })

    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Add Custom Provider/i }))
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText('my-provider'), {
      target: { value: 'bedrock-llm' },
    })
    fireEvent.change(screen.getByPlaceholderText('My Provider'), {
      target: { value: 'Bedrock LLM' },
    })
    fireEvent.change(screen.getByPlaceholderText('gpt-4o'), {
      target: { value: 'claude-3-sonnet' },
    })

    // First pick openai_compat (reveals the api_base field)
    await pickSelectOption(screen.getAllByRole('combobox')[0], 'openai_compat')

    // Fill in an api_base value
    await waitFor(() => screen.getByPlaceholderText('https://api.example.com/v1'))
    fireEvent.change(screen.getByPlaceholderText('https://api.example.com/v1'), {
      target: { value: 'https://my-openai.example.com/v1' },
    })

    // Switch adapter to bedrock — api_base field must disappear and state cleared
    await pickSelectOption(screen.getAllByRole('combobox')[0], 'bedrock')
    await waitFor(() => {
      expect(screen.queryByPlaceholderText('https://api.example.com/v1')).not.toBeInTheDocument()
    })

    // Select auth_type aws
    const combos = screen.getAllByRole('combobox')
    await pickSelectOption(combos[combos.length - 1], 'aws')

    fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }))

    await waitFor(() => {
      expect(mockCreateLlmProvider).toHaveBeenCalled()
    })

    const createCall = mockCreateLlmProvider.mock.calls[0][0]
    expect(createCall.adapter).toBe('bedrock')
    expect('api_base' in createCall).toBe(false)
  })

  it('does not send region in POST body when auth_type is switched from aws to api_key', async () => {
    const newProvider = makeCustomProvider({ id: 'new-uuid-3', name: 'openai-2', display_name: 'OpenAI 2', adapter: 'openai_compat', auth_type: 'api_key' })
    mockCreateLlmProvider.mockResolvedValue(newProvider)
    mockListLlmProviders
      .mockResolvedValueOnce(DEFAULT_PROVIDER_LIST)
      .mockResolvedValueOnce({
        builtin: DEFAULT_PROVIDER_LIST.builtin,
        custom: [...DEFAULT_PROVIDER_LIST.custom, newProvider],
      })

    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Add Custom Provider/i }))
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText('my-provider'), {
      target: { value: 'openai-2' },
    })
    fireEvent.change(screen.getByPlaceholderText('My Provider'), {
      target: { value: 'OpenAI 2' },
    })
    fireEvent.change(screen.getByPlaceholderText('gpt-4o'), {
      target: { value: 'gpt-4o' },
    })

    // Pick bedrock adapter (pairs naturally with aws auth_type)
    await pickSelectOption(screen.getAllByRole('combobox')[0], 'bedrock')

    // Pick auth_type aws (reveals region field)
    let combos = screen.getAllByRole('combobox')
    await pickSelectOption(combos[combos.length - 1], 'aws')

    // Type a region value
    await waitFor(() => screen.getByPlaceholderText('us-east-1'))
    fireEvent.change(screen.getByPlaceholderText('us-east-1'), {
      target: { value: 'eu-west-1' },
    })

    // Switch to openai_compat adapter
    await pickSelectOption(screen.getAllByRole('combobox')[0], 'openai_compat')

    // Switch auth_type to api_key — region field must disappear and state cleared
    combos = screen.getAllByRole('combobox')
    await pickSelectOption(combos[combos.length - 1], 'api_key')
    await waitFor(() => {
      expect(screen.queryByPlaceholderText('us-east-1')).not.toBeInTheDocument()
    })

    // Fill the api_base required for openai_compat
    await waitFor(() => screen.getByPlaceholderText('https://api.example.com/v1'))
    fireEvent.change(screen.getByPlaceholderText('https://api.example.com/v1'), {
      target: { value: 'https://api.openai.com/v1' },
    })

    // Fill api_key
    const passwordInputs = document.querySelectorAll('input[type="password"]')
    if (passwordInputs.length > 0) {
      fireEvent.change(passwordInputs[0], { target: { value: 'sk-test-key' } })
    }

    fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }))

    await waitFor(() => {
      expect(mockCreateLlmProvider).toHaveBeenCalled()
    })

    const createCall = mockCreateLlmProvider.mock.calls[0][0]
    expect(createCall.auth_type).toBe('api_key')
    expect('region' in createCall).toBe(false)
  })
})

// ─── Edit modal ───────────────────────────────────────────────────────────

describe('ProvidersPanel — Edit modal', () => {
  it('calls patchLlmProvider without api_key key when api_key is blank and only display_name changes', async () => {
    const editedProvider = makeCustomProvider({ display_name: 'My LLM Renamed' })
    mockPatchLlmProvider.mockResolvedValue(editedProvider)
    mockListLlmProviders
      .mockResolvedValueOnce(DEFAULT_PROVIDER_LIST)
      .mockResolvedValueOnce({
        builtin: DEFAULT_PROVIDER_LIST.builtin,
        custom: [editedProvider],
      })

    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTitle('Edit'))
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    // Change only display_name; leave api_key blank
    const displayNameInput = screen.getByPlaceholderText('My Provider')
    fireEvent.change(displayNameInput, { target: { value: 'My LLM Renamed' } })

    fireEvent.click(screen.getByRole('button', { name: /Save Changes/i }))

    await waitFor(() => {
      expect(mockPatchLlmProvider).toHaveBeenCalled()
    })

    const patchCall = mockPatchLlmProvider.mock.calls[0][1]
    expect(patchCall.display_name).toBe('My LLM Renamed')
    // api_key must not be present in the patch when the field was left blank
    expect('api_key' in patchCall).toBe(false)
  })
})

// ─── POST body assertion gap ──────────────────────────────────────────────

describe('ProvidersPanel — POST body field absence', () => {
  it('does not include api_key in POST body when auth_type is none', async () => {
    const newProvider = makeCustomProvider({ id: 'no-key-uuid', name: 'no-key-prov', display_name: 'No Key Prov', auth_type: 'none' })
    mockCreateLlmProvider.mockResolvedValue(newProvider)
    mockListLlmProviders
      .mockResolvedValueOnce(DEFAULT_PROVIDER_LIST)
      .mockResolvedValueOnce({
        builtin: DEFAULT_PROVIDER_LIST.builtin,
        custom: [...DEFAULT_PROVIDER_LIST.custom, newProvider],
      })

    renderSettings()
    await waitFor(() => {
      expect(screen.getByText('My LLM')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Add Custom Provider/i }))
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText('my-provider'), {
      target: { value: 'no-key-prov' },
    })
    fireEvent.change(screen.getByPlaceholderText('My Provider'), {
      target: { value: 'No Key Prov' },
    })
    fireEvent.change(screen.getByPlaceholderText('gpt-4o'), {
      target: { value: 'gpt-4o-mini' },
    })

    const adapterSelect = screen.getAllByRole('combobox')[0]
    fireEvent.click(adapterSelect)
    await waitFor(() => screen.getByText('anthropic_compat'))
    fireEvent.click(screen.getByText('anthropic_compat'))

    const authSelect = screen.getAllByRole('combobox')[screen.getAllByRole('combobox').length - 1]
    fireEvent.click(authSelect)
    await waitFor(() => screen.getAllByText('none'))
    const noneItems = screen.getAllByText('none')
    fireEvent.click(noneItems[noneItems.length - 1])

    fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }))

    await waitFor(() => {
      expect(mockCreateLlmProvider).toHaveBeenCalled()
    })

    const createCall = mockCreateLlmProvider.mock.calls[0][0]
    expect(createCall.auth_type).toBe('none')
    expect('api_key' in createCall).toBe(false)
  })
})
