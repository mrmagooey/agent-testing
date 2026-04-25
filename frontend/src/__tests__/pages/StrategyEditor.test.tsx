import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import StrategyEditor from '../../pages/StrategyEditor'
import type { UserStrategy, StrategySummary } from '../../api/client'

vi.mock('../../api/client', () => ({
  getStrategy: vi.fn(),
  createStrategy: vi.fn(),
  validateStrategy: vi.fn(),
  listToolExtensions: vi.fn(),
  listStrategiesFull: vi.fn(),
}))

import { getStrategy, createStrategy, validateStrategy, listToolExtensions, listStrategiesFull } from '../../api/client'
const mockGetStrategy = vi.mocked(getStrategy)
const mockCreateStrategy = vi.mocked(createStrategy)
const mockValidateStrategy = vi.mocked(validateStrategy)
const mockListToolExtensions = vi.mocked(listToolExtensions)
const mockListStrategiesFull = vi.mocked(listStrategiesFull)

function makeParentStrategy(overrides: Partial<UserStrategy> = {}): UserStrategy {
  return {
    id: 'builtin.single_agent',
    name: 'Single Agent',
    orchestration_shape: 'single_agent',
    is_builtin: true,
    parent_strategy_id: null,
    created_at: '2024-01-01T00:00:00Z',
    default: {
      system_prompt: 'You are a security researcher.',
      user_prompt_template: 'Analyze {repo_summary} for {finding_output_format}.',
      profile_modifier: '',
      model_id: 'claude-sonnet-4-5',
      tools: ['read_file'],
      verification: 'none',
      max_turns: 10,
      tool_extensions: [],
      subagents: [],
      max_subagent_depth: 3,
      max_subagent_invocations: 100,
      max_subagent_batch_size: 32,
      dispatch_fallback: 'reprompt',
      output_type_name: null,
    },
    overrides: [],
    ...overrides,
  }
}

function renderNewEditor() {
  return render(
    <MemoryRouter initialEntries={['/strategies/new']}>
      <Routes>
        <Route path="/strategies/new" element={<StrategyEditor />} />
        <Route path="/strategies/:id" element={<div>Strategy Viewer</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function renderForkEditor(id = 'builtin.single_agent') {
  return render(
    <MemoryRouter initialEntries={[`/strategies/${id}/fork`]}>
      <Routes>
        <Route path="/strategies/:id/fork" element={<StrategyEditor />} />
        <Route path="/strategies/:id" element={<div>Strategy Viewer</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function makeSampleRegistryStrategies(): StrategySummary[] {
  return [
    {
      id: 'builtin.single_agent',
      name: 'Single Agent',
      orchestration_shape: 'single_agent',
      is_builtin: true,
      parent_strategy_id: null,
    },
    {
      id: 'builtin.verifier',
      name: 'Verifier',
      orchestration_shape: 'single_agent',
      is_builtin: true,
      parent_strategy_id: null,
    },
  ]
}

describe('StrategyEditor — new strategy', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListToolExtensions.mockResolvedValue([
      { key: 'tree_sitter', label: 'Tree-sitter', available: true },
      { key: 'lsp', label: 'LSP', available: true },
    ])
    mockListStrategiesFull.mockResolvedValue(makeSampleRegistryStrategies())
    mockValidateStrategy.mockResolvedValue({ valid: true, errors: [] })
    mockCreateStrategy.mockResolvedValue({
      ...makeParentStrategy(),
      id: 'user.my-strategy-abc123',
      name: 'My Strategy',
      is_builtin: false,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the New Strategy heading', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Strategy' })).toBeVisible()
    })
  })

  it('renders name input and shape selector', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('name-input')).toBeVisible()
    })

    expect(screen.getByTestId('shape-select')).toBeVisible()
  })

  it('placeholder linter flags missing required placeholders', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('name-input')).toBeVisible()
    })

    // Find the user prompt template textarea and type something without required placeholders
    const textareas = screen.getAllByRole('textbox')
    const promptTemplateArea = textareas.find((ta) =>
      ta.getAttribute('placeholder')?.includes('{repo_summary}'),
    )
    expect(promptTemplateArea).toBeDefined()

    fireEvent.change(promptTemplateArea!, { target: { value: 'Hello world' } })

    await waitFor(() => {
      expect(screen.getByText(/missing: \{repo_summary\}/)).toBeVisible()
    })
    expect(screen.getByText(/missing: \{finding_output_format\}/)).toBeVisible()
  })

  it('placeholder linter shows green when both required placeholders present', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('name-input')).toBeVisible()
    })

    const textareas = screen.getAllByRole('textbox')
    const promptTemplateArea = textareas.find((ta) =>
      ta.getAttribute('placeholder')?.includes('{repo_summary}'),
    )
    expect(promptTemplateArea).toBeDefined()

    fireEvent.change(promptTemplateArea!, {
      target: { value: 'Review {repo_summary} and output in {finding_output_format}.' },
    })

    await waitFor(() => {
      expect(screen.queryByText(/missing:/)).toBeNull()
    })
    expect(screen.getByText('{repo_summary}')).toBeVisible()
    expect(screen.getByText('{finding_output_format}')).toBeVisible()
  })

  it('save button calls createStrategy with correct payload', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('name-input')).toBeVisible()
    })

    fireEvent.change(screen.getByTestId('name-input'), { target: { value: 'Test Strategy' } })

    fireEvent.click(screen.getByTestId('save-btn'))

    await waitFor(() => {
      expect(mockCreateStrategy).toHaveBeenCalled()
    })

    const callArg = mockCreateStrategy.mock.calls[0][0]
    expect(callArg.name).toBe('Test Strategy')
    expect(callArg.orchestration_shape).toBe('single_agent')
  })

  it('shows server errors when createStrategy fails', async () => {
    mockCreateStrategy.mockRejectedValue(new Error('Server validation error'))

    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('name-input')).toBeVisible()
    })

    fireEvent.change(screen.getByTestId('name-input'), { target: { value: 'Bad Strategy' } })
    fireEvent.click(screen.getByTestId('save-btn'))

    await waitFor(() => {
      expect(screen.getByText(/Server validation error/)).toBeVisible()
    })
  })

  it('overrides section is hidden for single_agent shape', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('shape-select')).toBeVisible()
    })

    // Default is single_agent — no overrides section
    expect(screen.queryByTestId('add-rule-btn')).toBeNull()
  })

  it('overrides section appears when switching to per_file shape', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('shape-select')).toBeVisible()
    })

    fireEvent.change(screen.getByTestId('shape-select'), { target: { value: 'per_file' } })

    await waitFor(() => {
      expect(screen.getByTestId('add-rule-btn')).toBeVisible()
    })
  })

  it('can add and reorder override rules for per_file shape', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('shape-select')).toBeVisible()
    })

    fireEvent.change(screen.getByTestId('shape-select'), { target: { value: 'per_file' } })

    await waitFor(() => {
      expect(screen.getByTestId('add-rule-btn')).toBeVisible()
    })

    // Add two rules
    fireEvent.click(screen.getByTestId('add-rule-btn'))
    fireEvent.click(screen.getByTestId('add-rule-btn'))

    await waitFor(() => {
      expect(screen.getAllByTestId('override-rule')).toHaveLength(2)
    })

    // Set keys
    const inputs = screen.getAllByTestId('rule-key-input')
    fireEvent.change(inputs[0], { target: { value: 'src/auth/**' } })
    fireEvent.change(inputs[1], { target: { value: '*.py' } })

    // Move second rule up (swap them)
    const moveUpBtns = screen.getAllByTestId('move-up-btn')
    // Second rule's up button should be clickable
    fireEvent.click(moveUpBtns[1])

    await waitFor(() => {
      const updatedInputs = screen.getAllByTestId('rule-key-input')
      expect(updatedInputs[0]).toHaveValue('*.py')
      expect(updatedInputs[1]).toHaveValue('src/auth/**')
    })
  })

  it('glob preview appears for rule keys', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('shape-select')).toBeVisible()
    })

    fireEvent.change(screen.getByTestId('shape-select'), { target: { value: 'per_file' } })

    await waitFor(() => {
      expect(screen.getByTestId('add-rule-btn')).toBeVisible()
    })

    fireEvent.click(screen.getByTestId('add-rule-btn'))

    const keyInput = screen.getByTestId('rule-key-input')
    fireEvent.change(keyInput, { target: { value: '*.py' } })

    await waitFor(() => {
      expect(screen.getByText(/Matches \d+ sample file/)).toBeVisible()
    })
  })

  it('removing a rule decrements the rule count', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('shape-select')).toBeVisible()
    })

    fireEvent.change(screen.getByTestId('shape-select'), { target: { value: 'per_file' } })

    await waitFor(() => {
      expect(screen.getByTestId('add-rule-btn')).toBeVisible()
    })

    fireEvent.click(screen.getByTestId('add-rule-btn'))
    fireEvent.click(screen.getByTestId('add-rule-btn'))

    await waitFor(() => {
      expect(screen.getAllByTestId('override-rule')).toHaveLength(2)
    })

    fireEvent.click(screen.getAllByTestId('remove-rule-btn')[0])

    await waitFor(() => {
      expect(screen.getAllByTestId('override-rule')).toHaveLength(1)
    })
  })
})

describe('StrategyEditor — fork mode', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListToolExtensions.mockResolvedValue([])
    mockListStrategiesFull.mockResolvedValue([])
    mockValidateStrategy.mockResolvedValue({ valid: true, errors: [] })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('pre-populates form from parent strategy', async () => {
    const parent = makeParentStrategy()
    mockGetStrategy.mockResolvedValue(parent)
    mockCreateStrategy.mockResolvedValue({
      ...parent,
      id: 'user.fork-abc123',
      name: 'Fork of Single Agent',
      is_builtin: false,
    })

    renderForkEditor('builtin.single_agent')

    await waitFor(() => {
      expect(screen.getByTestId('name-input')).toHaveValue('Fork of Single Agent')
    })

    expect(screen.getByTestId('shape-select')).toHaveValue('single_agent')
  })

  it('shows parent strategy ID in the form', async () => {
    const parent = makeParentStrategy()
    mockGetStrategy.mockResolvedValue(parent)
    renderForkEditor('builtin.single_agent')

    await waitFor(() => {
      // The "Parent:" label with the ID is rendered somewhere on the page
      const parentText = screen.getAllByText(/builtin\.single_agent/)
      expect(parentText.length).toBeGreaterThanOrEqual(1)
    })
  })

  it('save includes parent_strategy_id in payload', async () => {
    const parent = makeParentStrategy()
    mockGetStrategy.mockResolvedValue(parent)
    mockCreateStrategy.mockResolvedValue({
      ...parent,
      id: 'user.fork-abc123',
      name: 'Fork of Single Agent',
      is_builtin: false,
    })

    renderForkEditor('builtin.single_agent')

    await waitFor(() => {
      expect(screen.getByTestId('name-input')).toBeVisible()
    })

    fireEvent.click(screen.getByTestId('save-btn'))

    await waitFor(() => {
      expect(mockCreateStrategy).toHaveBeenCalled()
    })

    const callArg = mockCreateStrategy.mock.calls[0][0]
    expect(callArg.parent_strategy_id).toBe('builtin.single_agent')
  })

  it('shows error when parent strategy fails to load', async () => {
    mockGetStrategy.mockRejectedValue(new Error('Strategy not found'))
    renderForkEditor('nonexistent')

    await waitFor(() => {
      expect(screen.getByText(/Strategy not found/)).toBeVisible()
    })
  })
})

// ─── Phase 6 tests ────────────────────────────────────────────────────────────

describe('StrategyEditor — Phase 6 new fields', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListToolExtensions.mockResolvedValue([])
    mockListStrategiesFull.mockResolvedValue(makeSampleRegistryStrategies())
    mockValidateStrategy.mockResolvedValue({ valid: true, errors: [] })
    mockCreateStrategy.mockResolvedValue({
      ...makeParentStrategy(),
      id: 'user.test-abc123',
      name: 'Test Strategy',
      is_builtin: false,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders subagents list from registry', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('subagents-list')).toBeVisible()
    })

    // Both registry strategies should appear as checkboxes
    expect(screen.getByTestId('subagent-checkbox-builtin.single_agent')).toBeInTheDocument()
    expect(screen.getByTestId('subagent-checkbox-builtin.verifier')).toBeInTheDocument()
  })

  it('renders dispatch_fallback dropdown with default "reprompt"', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('dispatch-fallback-select')).toBeVisible()
    })

    const select = screen.getByTestId('dispatch-fallback-select') as HTMLSelectElement
    expect(select.value).toBe('reprompt')
  })

  it('renders output_type_name dropdown defaulting to empty (none)', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('output-type-name-select')).toBeVisible()
    })

    const select = screen.getByTestId('output-type-name-select') as HTMLSelectElement
    expect(select.value).toBe('')
  })

  it('renders subagent caps fields with default values', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('max-subagent-depth-input')).toBeVisible()
    })

    expect((screen.getByTestId('max-subagent-depth-input') as HTMLInputElement).value).toBe('3')
    expect((screen.getByTestId('max-subagent-invocations-input') as HTMLInputElement).value).toBe('100')
    expect((screen.getByTestId('max-subagent-batch-size-input') as HTMLInputElement).value).toBe('32')
  })

  it('submitting with subagents includes new fields in payload', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('name-input')).toBeVisible()
    })

    // Set name
    fireEvent.change(screen.getByTestId('name-input'), { target: { value: 'Subagent Strategy' } })

    // Select builtin.verifier as subagent
    await waitFor(() => {
      expect(screen.getByTestId('subagent-checkbox-builtin.verifier')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByTestId('subagent-checkbox-builtin.verifier'))

    // Set dispatch_fallback to 'none'
    fireEvent.change(screen.getByTestId('dispatch-fallback-select'), { target: { value: 'none' } })

    // Set output_type_name
    fireEvent.change(screen.getByTestId('output-type-name-select'), { target: { value: 'finding_list' } })

    fireEvent.click(screen.getByTestId('save-btn'))

    await waitFor(() => {
      expect(mockCreateStrategy).toHaveBeenCalled()
    })

    const callArg = mockCreateStrategy.mock.calls[0][0]
    expect(callArg.default.subagents).toEqual(['builtin.verifier'])
    expect(callArg.default.dispatch_fallback).toBe('none')
    expect(callArg.default.output_type_name).toBe('finding_list')
    expect(callArg.default.max_subagent_depth).toBeGreaterThan(0)
    expect(callArg.default.max_subagent_invocations).toBeGreaterThan(0)
    expect(callArg.default.max_subagent_batch_size).toBeGreaterThan(0)
  })

  it('selecting subagents shows caps-required hint message', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('subagents-list')).toBeVisible()
    })

    // No hint before selection
    expect(screen.queryByText(/caps fields below are required/)).toBeNull()

    // Select a subagent
    fireEvent.click(screen.getByTestId('subagent-checkbox-builtin.verifier'))

    await waitFor(() => {
      expect(screen.getByText(/caps fields below are required/)).toBeVisible()
    })
  })

  it('no caps validation error when no subagents selected even with zero caps', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('name-input')).toBeVisible()
    })

    fireEvent.change(screen.getByTestId('name-input'), { target: { value: 'No Subagents Strategy' } })

    // Don't select any subagents; set depth to 0 (no error expected)
    fireEvent.change(screen.getByTestId('max-subagent-depth-input'), { target: { value: '0' } })

    fireEvent.click(screen.getByTestId('save-btn'))

    await waitFor(() => {
      expect(mockCreateStrategy).toHaveBeenCalled()
    })

    // No caps error shown
    expect(screen.queryByText(/max_subagent_depth must be a positive integer/)).toBeNull()
  })

  it('dispatch_fallback options are reprompt, programmatic, none', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('dispatch-fallback-select')).toBeVisible()
    })

    const select = screen.getByTestId('dispatch-fallback-select')
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.value)
    expect(options).toContain('reprompt')
    expect(options).toContain('programmatic')
    expect(options).toContain('none')
  })

  it('output_type_name options include finding_list and verifier_verdict', async () => {
    renderNewEditor()

    await waitFor(() => {
      expect(screen.getByTestId('output-type-name-select')).toBeVisible()
    })

    const select = screen.getByTestId('output-type-name-select')
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.value)
    expect(options).toContain('')
    expect(options).toContain('finding_list')
    expect(options).toContain('verifier_verdict')
    expect(options).toContain('source_list')
    expect(options).toContain('taint_path_list')
    expect(options).toContain('sanitization_verdict')
    expect(options).toContain('classifier_judgement_list')
  })
})
