import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Findings from '../../pages/Findings'
import type { GlobalFinding, GlobalFindingsResponse, FindingFacets } from '../../api/client'

vi.mock('../../api/client', () => ({
  searchFindingsGlobal: vi.fn(),
}))

// Mock heavy child components that don't add coverage value here
vi.mock('../../components/FindingRow', () => ({
  default: vi.fn(({ finding }: { finding: GlobalFinding }) => (
    <tr data-testid="finding-row">
      <td>{finding.title}</td>
    </tr>
  )),
}))

vi.mock('../../components/FindingsFilterBar', () => ({
  default: vi.fn(({ onClearAll }: { onClearAll: () => void }) => (
    <div data-testid="filter-bar">
      <button onClick={onClearAll}>Clear all</button>
    </div>
  )),
}))

vi.mock('../../components/Pagination', () => ({
  default: vi.fn(() => <div data-testid="pagination" />),
}))

vi.mock('../../components/EmptyState', () => ({
  default: vi.fn(({ title, subtitle }: { title: string; subtitle?: string }) => (
    <div data-testid="empty-state">
      <p>{title}</p>
      {subtitle && <p>{subtitle}</p>}
    </div>
  )),
}))

import { searchFindingsGlobal } from '../../api/client'
const mockSearchFindingsGlobal = vi.mocked(searchFindingsGlobal)

const EMPTY_FACETS: FindingFacets = {
  vuln_class: {},
  severity: {},
  match_status: {},
  model_id: {},
  strategy: {},
  dataset_name: {},
}

function makeFinding(overrides: Partial<GlobalFinding> = {}): GlobalFinding {
  return {
    finding_id: 'f-001',
    run_id: 'run-1',
    experiment_id: 'exp-1',
    title: 'SQL Injection',
    description: 'Unsanitized input reaches DB query',
    vuln_class: 'sqli',
    severity: 'high',
    match_status: 'tp',
    model_id: 'gpt-4o',
    strategy: 'zero_shot',
    dataset_name: 'cve-2024-python',
    experiment_name: 'Experiment 1',
    file_path: 'src/db.py',
    line_start: 42,
    line_end: 45,
    created_at: '2024-01-15T10:00:00Z',
    ...overrides,
  }
}

function makeResponse(
  items: GlobalFinding[],
  total?: number,
  facets?: FindingFacets,
): GlobalFindingsResponse {
  return {
    total: total ?? items.length,
    facets: facets ?? EMPTY_FACETS,
    items,
  }
}

function renderFindings(initialPath = '/findings') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/findings" element={<Findings />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Findings — initial render', () => {
  it('renders the "Findings" heading', async () => {
    mockSearchFindingsGlobal.mockResolvedValue(makeResponse([]))

    renderFindings()

    expect(screen.getByRole('heading', { name: 'Findings' })).toBeInTheDocument()
  })

  it('renders the search input', () => {
    mockSearchFindingsGlobal.mockResolvedValue(makeResponse([]))

    renderFindings()

    expect(screen.getByRole('searchbox', { name: 'Search findings' })).toBeInTheDocument()
  })

  it('renders the filter sidebar', async () => {
    mockSearchFindingsGlobal.mockResolvedValue(makeResponse([]))

    renderFindings()

    expect(screen.getByTestId('filter-bar')).toBeInTheDocument()
  })
})

describe('Findings — data-load success', () => {
  it('renders finding rows after load', async () => {
    mockSearchFindingsGlobal.mockResolvedValue(
      makeResponse([
        makeFinding({ finding_id: 'f-001', title: 'SQL Injection' }),
        makeFinding({ finding_id: 'f-002', title: 'Path Traversal' }),
      ]),
    )

    renderFindings()

    await waitFor(() => {
      expect(screen.getAllByTestId('finding-row')).toHaveLength(2)
    })
    expect(screen.getByText('SQL Injection')).toBeInTheDocument()
    expect(screen.getByText('Path Traversal')).toBeInTheDocument()
  })

  it('renders total count label', async () => {
    mockSearchFindingsGlobal.mockResolvedValue(makeResponse([makeFinding()], 1))

    renderFindings()

    await waitFor(() => {
      expect(screen.getByText(/1 finding/)).toBeInTheDocument()
    })
  })

  it('renders pagination when results exist', async () => {
    mockSearchFindingsGlobal.mockResolvedValue(makeResponse([makeFinding()], 1))

    renderFindings()

    await waitFor(() => {
      expect(screen.getByTestId('pagination')).toBeInTheDocument()
    })
  })
})

describe('Findings — empty state (no findings indexed)', () => {
  it('shows "No findings indexed yet" when total is 0 and no filters active', async () => {
    mockSearchFindingsGlobal.mockResolvedValue(makeResponse([], 0))

    renderFindings()

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
      expect(screen.getByText('No findings indexed yet')).toBeInTheDocument()
    })
  })
})

describe('Findings — empty state (filters active, no results)', () => {
  it('shows "No results for current filters" when query is active but total is 0', async () => {
    mockSearchFindingsGlobal.mockResolvedValue(makeResponse([], 0))

    // Render with a ?q= param so "isIndexEmpty" is false
    renderFindings('/findings?q=xss')

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
      expect(screen.getByText('No results for current filters')).toBeInTheDocument()
    })
  })
})

describe('Findings — error state', () => {
  it('shows error EmptyState when searchFindingsGlobal rejects', async () => {
    mockSearchFindingsGlobal.mockRejectedValue(new Error('Server error'))

    renderFindings()

    await waitFor(() => {
      expect(screen.getByTestId('empty-state')).toBeInTheDocument()
      expect(screen.getByText('Failed to load findings')).toBeInTheDocument()
      expect(screen.getByText('Server error')).toBeInTheDocument()
    })
  })
})

describe('Findings — search interaction', () => {
  it('calls searchFindingsGlobal on initial load', async () => {
    mockSearchFindingsGlobal.mockResolvedValue(makeResponse([]))

    renderFindings()

    await waitFor(() => {
      expect(mockSearchFindingsGlobal).toHaveBeenCalled()
    })
  })

  it('clear-all resets search input via filter bar', async () => {
    mockSearchFindingsGlobal.mockResolvedValue(makeResponse([]))

    renderFindings('/findings?q=sqli')

    await waitFor(() => {
      expect(mockSearchFindingsGlobal).toHaveBeenCalled()
    })

    // Clicking "Clear all" from the mocked FilterBar should call setSearchParams
    fireEvent.click(screen.getByText('Clear all'))
    // After clear, another search call should fire
    await waitFor(() => {
      expect(mockSearchFindingsGlobal).toHaveBeenCalledTimes(2)
    })
  })
})

describe('Findings — URL-param-driven behavior', () => {
  it('passes q param to searchFindingsGlobal when present in URL', async () => {
    mockSearchFindingsGlobal.mockResolvedValue(makeResponse([]))

    renderFindings('/findings?q=buffer+overflow')

    await waitFor(() => {
      expect(mockSearchFindingsGlobal).toHaveBeenCalledWith(
        expect.objectContaining({ q: 'buffer overflow' }),
      )
    })
  })

  it('passes sort param to searchFindingsGlobal when present in URL', async () => {
    mockSearchFindingsGlobal.mockResolvedValue(makeResponse([]))

    renderFindings('/findings?sort=severity+desc')

    await waitFor(() => {
      expect(mockSearchFindingsGlobal).toHaveBeenCalledWith(
        expect.objectContaining({ sort: 'severity desc' }),
      )
    })
  })
})
