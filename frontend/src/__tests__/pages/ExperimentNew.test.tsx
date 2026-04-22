import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ExperimentNew, { generatePowerSet } from '../../pages/ExperimentNew'

// ─── Regression: React error #31 on /experiments/new ─────────────────────────
//
// The coordinator's /models, /strategies, /profiles endpoints return
// `list[dict]` (e.g. `{"name": "default", "description": "..."}`).
// The frontend previously typed these as `string[]` and dropped the items
// straight into JSX (`<span>{p}</span>` inside the Profile radio list),
// which triggers React error #31: "Objects are not valid as a React child."
//
// The e2e Playwright mocks happen to return real `string[]` for these
// endpoints, so the bug only reproduces against the live coordinator.
// This test simulates the real coordinator's object-shape responses and
// asserts the page renders without throwing.

function makeResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response
}

const GROUPED_MODELS = [
  {
    provider: 'openai',
    probe_status: 'fresh',
    models: [{ id: 'gpt-4o', display_name: 'GPT-4o', status: 'available' }],
  },
  {
    provider: 'anthropic',
    probe_status: 'fresh',
    models: [{ id: 'claude-3-5-sonnet-20241022', display_name: 'Claude 3.5 Sonnet', status: 'available' }],
  },
]

const GROUPED_MODELS_WITH_UNAVAILABLE = [
  {
    provider: 'openai',
    probe_status: 'fresh',
    models: [
      { id: 'gpt-4o', display_name: 'GPT-4o', status: 'available' },
      { id: 'gpt-4o-mini', display_name: 'GPT-4o Mini', status: 'key_missing' },
    ],
  },
  {
    provider: 'anthropic',
    probe_status: 'fresh',
    models: [{ id: 'claude-3-5-sonnet-20241022', display_name: 'Claude 3.5 Sonnet', status: 'available' }],
  },
]

function mockRealCoordinatorFetch(modelFixture = GROUPED_MODELS) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    // Phase 2 grouped-by-provider shape from /api/models
    if (url.endsWith('/api/models')) {
      return makeResponse(modelFixture)
    }
    if (url.endsWith('/api/strategies')) {
      return makeResponse([
        { name: 'zero_shot', description: 'zero_shot scan strategy' },
        { name: 'chain_of_thought', description: 'chain_of_thought scan strategy' },
      ])
    }
    if (url.endsWith('/api/profiles')) {
      return makeResponse([
        { name: 'default', description: 'Default review profile.' },
        { name: 'strict', description: 'Strict review profile.' },
      ])
    }
    if (url.endsWith('/api/datasets')) {
      // Coordinator omits `languages` — must not crash the <option> renderer.
      return makeResponse([{ name: 'cve-2024-python', label_count: 4 }])
    }
    if (url.endsWith('/api/tool-extensions')) {
      return makeResponse([
        { key: 'tree_sitter', label: 'Tree-sitter', available: true },
        { key: 'lsp', label: 'LSP', available: true },
        { key: 'devdocs', label: 'DevDocs', available: false },
      ])
    }
    if (url.endsWith('/api/experiments/estimate')) {
      return makeResponse({ total_runs: 2, estimated_cost_usd: 1.0, by_model: {} })
    }
    return makeResponse({}, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('ExperimentNew page rendering (React #31 regression)', () => {
  beforeEach(() => {
    mockRealCoordinatorFetch()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders /experiments/new without throwing when the coordinator returns object-shape config payloads', async () => {
    // Capture console.error so we can detect React's #31 warning even in
    // environments where it would otherwise be swallowed.
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    // Wait for the page to finish loading (Loading… disappears once Promise.all resolves).
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })

    // The profile radio list must show the profile names (not [object Object]).
    expect(screen.getByText('default')).toBeVisible()
    expect(screen.getByText('strict')).toBeVisible()

    // No React "Objects are not valid as a React child" error should have been logged.
    const reactObjectErrors = errorSpy.mock.calls.filter((call) => {
      const msg = call.map(String).join(' ')
      return (
        msg.includes('Objects are not valid as a React child') ||
        msg.includes('Minified React error #31')
      )
    })
    expect(reactObjectErrors).toEqual([])
  })

  it('renders ModelSearchPicker with search input instead of ChipGroup for models', async () => {
    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })

    // ModelSearchPicker renders a search input
    expect(screen.getByPlaceholderText('Search models…')).toBeVisible()
    // Available model display names are shown in the picker
    expect(screen.getByText('GPT-4o')).toBeVisible()
    expect(screen.getByText('Claude 3.5 Sonnet')).toBeVisible()
  })

  it('renders "Allow unavailable models" checkbox', async () => {
    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })

    expect(screen.getByTestId('allow-unavailable-checkbox')).toBeVisible()
    expect(screen.getByTestId('allow-unavailable-checkbox')).not.toBeChecked()
  })
})

describe('ExperimentNew — dropped unavailable models notice', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('does not render dropped-models notice when no initial selection', async () => {
    mockRealCoordinatorFetch(GROUPED_MODELS_WITH_UNAVAILABLE)

    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })

    // No selection pre-loaded → no drop notice
    expect(screen.queryByTestId('dropped-models-notice')).toBeNull()
  })
})

describe('ExperimentNew — unavailable_models submit error + override', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function mockWithUnavailableError() {
    let submissionCount = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url.endsWith('/api/models')) return makeResponse(GROUPED_MODELS)
      if (url.endsWith('/api/strategies')) return makeResponse([{ name: 'zero_shot', description: '' }])
      if (url.endsWith('/api/profiles')) return makeResponse([{ name: 'default', description: '' }])
      if (url.endsWith('/api/datasets')) return makeResponse([{ name: 'cve-2024-python', label_count: 4 }])
      if (url.endsWith('/api/tool-extensions')) return makeResponse([])
      if (url.endsWith('/api/experiments/estimate')) {
        return makeResponse({ total_runs: 2, estimated_cost_usd: 1.0, by_model: {} })
      }
      if (url.endsWith('/api/experiments') && init?.method === 'POST') {
        submissionCount++
        const body = JSON.parse(init.body as string) as Record<string, unknown>
        if (body.allow_unavailable_models) {
          // Success on override
          return makeResponse(
            { experiment_id: 'exp-override-123', status: 'pending' },
            201,
          )
        }
        // First submit: return unavailable_models error
        return makeResponse(
          {
            detail: {
              error: 'unavailable_models',
              models: [{ id: 'gpt-4o', status: 'key_missing' }],
            },
          },
          400,
        )
      }
      return makeResponse({}, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    return { fetchMock, getSubmissionCount: () => submissionCount }
  }

  it('renders targeted unavailable_models error after 400 response', async () => {
    mockWithUnavailableError()

    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })

    // Fill required fields - select dataset (use the <select> element directly)
    // getByRole('combobox') is ambiguous (matches both <select> and the cmdk input);
    // use document.querySelector('select') to target the dataset dropdown unambiguously.
    const datasetSelect = document.querySelector('select')!
    fireEvent.change(datasetSelect, { target: { value: 'cve-2024-python' } })

    // Select GPT-4o via picker - click the item in the command list
    const gpt4oItem = screen.getByText('GPT-4o')
    fireEvent.click(gpt4oItem)

    // Select strategy
    const zeroShotCheckbox = screen.getByRole('checkbox', { name: /zero_shot/i })
    fireEvent.click(zeroShotCheckbox)

    // Submit
    const submitBtn = screen.getByRole('button', { name: 'Submit Experiment' })
    fireEvent.click(submitBtn)

    // Wait for the unavailable models error to appear
    await waitFor(() => {
      expect(screen.getByTestId('unavailable-models-error')).toBeVisible()
    })

    // Should list the offending model in the error panel
    expect(screen.getByTestId('unavailable-models-error').textContent).toMatch(/gpt-4o/)
    // Should show the override button
    expect(screen.getByTestId('submit-with-override-btn')).toBeVisible()
  })

  it('"Submit with override" re-submits with allow_unavailable_models:true', async () => {
    const { fetchMock } = mockWithUnavailableError()

    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })

    // Fill form
    const datasetSelect = document.querySelector('select')!
    fireEvent.change(datasetSelect, { target: { value: 'cve-2024-python' } })
    fireEvent.click(screen.getByText('GPT-4o'))
    const zeroShotCheckbox = screen.getByRole('checkbox', { name: /zero_shot/i })
    fireEvent.click(zeroShotCheckbox)

    // Submit → get error
    fireEvent.click(screen.getByRole('button', { name: 'Submit Experiment' }))

    await waitFor(() => {
      expect(screen.getByTestId('unavailable-models-error')).toBeVisible()
    })

    // Click override button
    fireEvent.click(screen.getByTestId('submit-with-override-btn'))

    // Verify the last POST included allow_unavailable_models
    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(([url, init]) => {
        const u = typeof url === 'string' ? url : url.toString()
        return u.endsWith('/api/experiments') && (init as RequestInit)?.method === 'POST'
      })
      // There should be at least 2 POST calls (first fails, second succeeds)
      expect(postCalls.length).toBeGreaterThanOrEqual(2)
      const lastCall = postCalls[postCalls.length - 1]
      const body = JSON.parse((lastCall[1] as RequestInit).body as string) as Record<string, unknown>
      expect(body.allow_unavailable_models).toBe(true)
    })
  })

  it('"Allow unavailable models" checkbox state included in submit payload', async () => {
    const { fetchMock } = mockWithUnavailableError()

    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })

    // Fill form
    const datasetSelect2 = document.querySelector('select')!
    fireEvent.change(datasetSelect2, { target: { value: 'cve-2024-python' } })
    fireEvent.click(screen.getByText('GPT-4o'))
    const zeroShotCheckbox = screen.getByRole('checkbox', { name: /zero_shot/i })
    fireEvent.click(zeroShotCheckbox)

    // Check the "Allow unavailable models" checkbox BEFORE submitting
    const allowCheckbox = screen.getByTestId('allow-unavailable-checkbox')
    fireEvent.click(allowCheckbox)
    expect(allowCheckbox).toBeChecked()

    // Submit
    fireEvent.click(screen.getByRole('button', { name: 'Submit Experiment' }))

    // Verify the POST included allow_unavailable_models:true
    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(([url, init]) => {
        const u = typeof url === 'string' ? url : url.toString()
        return u.endsWith('/api/experiments') && (init as RequestInit)?.method === 'POST'
      })
      expect(postCalls.length).toBeGreaterThanOrEqual(1)
      const body = JSON.parse((postCalls[0][1] as RequestInit).body as string) as Record<string, unknown>
      expect(body.allow_unavailable_models).toBe(true)
    })
  })
})

describe('generatePowerSet', () => {
  it('generates empty set when given empty array', () => {
    const result = generatePowerSet([])
    expect(result).toEqual([[]])
  })

  it('generates power set for single element', () => {
    const result = generatePowerSet(['a'])
    expect(result).toEqual([[], ['a']])
  })

  it('generates power set for two elements', () => {
    const result = generatePowerSet(['a', 'b'])
    expect(result).toHaveLength(4)
    expect(result).toContainEqual([])
    expect(result).toContainEqual(['a'])
    expect(result).toContainEqual(['b'])
    expect(result).toContainEqual(['a', 'b'])
  })

  it('generates power set for three elements (2^3 = 8 subsets)', () => {
    const result = generatePowerSet(['lsp', 'tree_sitter', 'devdocs'])
    expect(result).toHaveLength(8)
    expect(result).toContainEqual([])
    expect(result).toContainEqual(['lsp'])
    expect(result).toContainEqual(['tree_sitter'])
    expect(result).toContainEqual(['devdocs'])
    expect(result).toContainEqual(['lsp', 'tree_sitter'])
    expect(result).toContainEqual(['lsp', 'devdocs'])
    expect(result).toContainEqual(['tree_sitter', 'devdocs'])
    expect(result).toContainEqual(['lsp', 'tree_sitter', 'devdocs'])
  })

  it('generates correct power set length for n elements', () => {
    const testCases = [
      { n: 1, expected: 2 },
      { n: 2, expected: 4 },
      { n: 3, expected: 8 },
      { n: 4, expected: 16 },
      { n: 5, expected: 32 },
    ]

    testCases.forEach(({ n, expected }) => {
      const items = Array.from({ length: n }, (_, i) => `item${i}`)
      const result = generatePowerSet(items)
      expect(result).toHaveLength(expected)
    })
  })

  it('preserves order of elements within subsets', () => {
    const result = generatePowerSet(['a', 'b', 'c'])
    // Check that 'a' always comes before 'b' if both are present
    const withBoth = result.filter((s) => s.includes('a') && s.includes('b'))
    withBoth.forEach((subset) => {
      const aIdx = subset.indexOf('a')
      const bIdx = subset.indexOf('b')
      expect(aIdx).toBeLessThan(bIdx)
    })
  })
})
