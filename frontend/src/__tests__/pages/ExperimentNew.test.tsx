import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ExperimentNew, { generatePowerSet } from '../../pages/ExperimentNew'

// ─── Mock helpers ──────────────────────────────────────────────────────────────

function makeResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response
}

const SAMPLE_STRATEGIES = [
  {
    id: 'builtin.single_agent',
    name: 'Single Agent',
    orchestration_shape: 'single_agent',
    is_builtin: true,
    parent_strategy_id: null,
  },
  {
    id: 'builtin.per_vuln_class',
    name: 'Per Vuln Class',
    orchestration_shape: 'per_vuln_class',
    is_builtin: true,
    parent_strategy_id: null,
  },
]

function mockFetch(strategies = SAMPLE_STRATEGIES) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.endsWith('/api/strategies')) {
      // Returns StrategySummary[] for the new listStrategiesFull()
      return makeResponse(strategies)
    }
    if (url.endsWith('/api/datasets')) {
      return makeResponse([{ name: 'cve-2024-python', label_count: 4, languages: [] }])
    }
    if (url.endsWith('/api/experiments/estimate') && init?.method === 'POST') {
      return makeResponse({ total_runs: 2, estimated_cost_usd: 1.0, by_model: {} })
    }
    return makeResponse({}, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('ExperimentNew page rendering', () => {
  beforeEach(() => {
    mockFetch()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the New Experiment heading', async () => {
    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })
  })

  it('renders strategy cards for available strategies', async () => {
    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })

    // Strategy names should appear
    expect(screen.getByText('Single Agent')).toBeVisible()
    expect(screen.getByText('Per Vuln Class')).toBeVisible()
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

  it('shows builtin badge on builtin strategies', async () => {
    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })

    // Both builtin strategies should show "builtin" badge
    const builtinBadges = screen.getAllByText('builtin')
    expect(builtinBadges.length).toBeGreaterThanOrEqual(2)
  })

  it('selecting a strategy card toggles its selected state', async () => {
    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })

    const cards = screen.getAllByTestId('strategy-card')
    expect(cards[0]).toHaveAttribute('data-selected', 'false')

    // Click to select
    fireEvent.click(cards[0])
    await waitFor(() => {
      expect(cards[0]).toHaveAttribute('data-selected', 'true')
    })

    // Click to deselect
    fireEvent.click(cards[0])
    await waitFor(() => {
      expect(cards[0]).toHaveAttribute('data-selected', 'false')
    })
  })
})

describe('ExperimentNew — dropped unavailable models notice', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('does not render dropped-models notice on initial load', async () => {
    mockFetch()

    render(
      <MemoryRouter initialEntries={['/experiments/new']}>
        <ExperimentNew />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Experiment' })).toBeVisible()
    })

    // No drop notice initially
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
      if (url.endsWith('/api/strategies')) {
        return makeResponse(SAMPLE_STRATEGIES)
      }
      if (url.endsWith('/api/datasets')) {
        return makeResponse([{ name: 'cve-2024-python', label_count: 4, languages: [] }])
      }
      if (url.endsWith('/api/experiments/estimate') && init?.method === 'POST') {
        return makeResponse({ total_runs: 2, estimated_cost_usd: 1.0, by_model: {} })
      }
      if (url.endsWith('/api/experiments') && init?.method === 'POST') {
        submissionCount++
        const body = JSON.parse(init.body as string) as Record<string, unknown>
        if (body.allow_unavailable_models) {
          return makeResponse({ experiment_id: 'exp-override-123', status: 'pending' }, 201)
        }
        return makeResponse(
          {
            detail: {
              error: 'unavailable_models',
              models: [{ id: 'claude-sonnet-4-5', status: 'key_missing' }],
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

    // Select dataset
    const datasetSelect = document.querySelector('select')!
    fireEvent.change(datasetSelect, { target: { value: 'cve-2024-python' } })

    // Select a strategy card
    const cards = screen.getAllByTestId('strategy-card')
    fireEvent.click(cards[0])

    // Submit
    const submitBtn = screen.getByRole('button', { name: 'Submit Experiment' })
    fireEvent.click(submitBtn)

    await waitFor(() => {
      expect(screen.getByTestId('unavailable-models-error')).toBeVisible()
    })

    expect(screen.getByTestId('unavailable-models-error').textContent).toMatch(/claude-sonnet-4-5/)
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

    const datasetSelect = document.querySelector('select')!
    fireEvent.change(datasetSelect, { target: { value: 'cve-2024-python' } })
    const cards = screen.getAllByTestId('strategy-card')
    fireEvent.click(cards[0])

    fireEvent.click(screen.getByRole('button', { name: 'Submit Experiment' }))

    await waitFor(() => {
      expect(screen.getByTestId('unavailable-models-error')).toBeVisible()
    })

    fireEvent.click(screen.getByTestId('submit-with-override-btn'))

    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(([url, init]) => {
        const u = typeof url === 'string' ? url : url.toString()
        return u.endsWith('/api/experiments') && (init as RequestInit)?.method === 'POST'
      })
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

    const datasetSelect = document.querySelector('select')!
    fireEvent.change(datasetSelect, { target: { value: 'cve-2024-python' } })
    const cards = screen.getAllByTestId('strategy-card')
    fireEvent.click(cards[0])

    const allowCheckbox = screen.getByTestId('allow-unavailable-checkbox')
    fireEvent.click(allowCheckbox)
    expect(allowCheckbox).toBeChecked()

    fireEvent.click(screen.getByRole('button', { name: 'Submit Experiment' }))

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
    const withBoth = result.filter((s) => s.includes('a') && s.includes('b'))
    withBoth.forEach((subset) => {
      const aIdx = subset.indexOf('a')
      const bIdx = subset.indexOf('b')
      expect(aIdx).toBeLessThan(bIdx)
    })
  })
})
