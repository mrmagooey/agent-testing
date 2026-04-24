import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import StrategiesList from '../../pages/StrategiesList'

// Mock the API client
vi.mock('../../api/client', () => ({
  listStrategiesFull: vi.fn(),
}))

import { listStrategiesFull } from '../../api/client'
const mockListStrategiesFull = vi.mocked(listStrategiesFull)

const SAMPLE_STRATEGIES = [
  {
    id: 'builtin.single_agent',
    name: 'Single Agent',
    orchestration_shape: 'single_agent',
    is_builtin: true,
    parent_strategy_id: null,
  },
  {
    id: 'builtin.per_file',
    name: 'Per File',
    orchestration_shape: 'per_file',
    is_builtin: true,
    parent_strategy_id: null,
  },
  {
    id: 'user.sqli-hunter-a3f9',
    name: 'SQLi Hunter',
    orchestration_shape: 'per_vuln_class',
    is_builtin: false,
    parent_strategy_id: 'builtin.per_vuln_class',
  },
]

function renderList() {
  return render(
    <MemoryRouter initialEntries={['/strategies']}>
      <StrategiesList />
    </MemoryRouter>,
  )
}

describe('StrategiesList', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the Strategies heading', async () => {
    mockListStrategiesFull.mockResolvedValue(SAMPLE_STRATEGIES)
    renderList()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Strategies' })).toBeVisible()
    })
  })

  it('renders a row for each strategy', async () => {
    mockListStrategiesFull.mockResolvedValue(SAMPLE_STRATEGIES)
    renderList()

    await waitFor(() => {
      expect(screen.getAllByTestId('strategy-row')).toHaveLength(3)
    })

    // Strategy names appear in cells (may also appear in filter dropdown as shape labels)
    expect(screen.getByText('SQLi Hunter')).toBeVisible()
    // Verify the row count is correct
    const rows = screen.getAllByTestId('strategy-row')
    expect(rows).toHaveLength(3)
  })

  it('shows builtin badge for builtin strategies', async () => {
    mockListStrategiesFull.mockResolvedValue(SAMPLE_STRATEGIES)
    renderList()

    await waitFor(() => {
      expect(screen.getAllByTestId('strategy-row')).toHaveLength(3)
    })

    // 2 builtin strategies → 2 "builtin" badges
    const builtinBadges = screen.getAllByText('builtin')
    expect(builtinBadges.length).toBeGreaterThanOrEqual(2)
  })

  it('shows user badge for user strategies', async () => {
    mockListStrategiesFull.mockResolvedValue(SAMPLE_STRATEGIES)
    renderList()

    await waitFor(() => {
      expect(screen.getAllByTestId('strategy-row')).toHaveLength(3)
    })

    // 1 user strategy → at least 1 "user" badge
    const userBadges = screen.getAllByText('user')
    expect(userBadges.length).toBeGreaterThanOrEqual(1)
  })

  it('filter by shape narrows the displayed rows', async () => {
    mockListStrategiesFull.mockResolvedValue(SAMPLE_STRATEGIES)
    renderList()

    await waitFor(() => {
      expect(screen.getAllByTestId('strategy-row')).toHaveLength(3)
    })

    // Filter to per_vuln_class (only user.sqli-hunter has it)
    const shapeFilter = screen.getByTestId('shape-filter')
    fireEvent.change(shapeFilter, { target: { value: 'per_vuln_class' } })

    await waitFor(() => {
      expect(screen.getAllByTestId('strategy-row')).toHaveLength(1)
    })
    expect(screen.getByText('SQLi Hunter')).toBeVisible()
  })

  it('builtin toggle filters to only builtin strategies', async () => {
    mockListStrategiesFull.mockResolvedValue(SAMPLE_STRATEGIES)
    renderList()

    await waitFor(() => {
      expect(screen.getAllByTestId('strategy-row')).toHaveLength(3)
    })

    fireEvent.click(screen.getByTestId('filter-builtin'))

    await waitFor(() => {
      expect(screen.getAllByTestId('strategy-row')).toHaveLength(2)
    })
    expect(screen.queryByText('SQLi Hunter')).toBeNull()
  })

  it('user toggle filters to only user strategies', async () => {
    mockListStrategiesFull.mockResolvedValue(SAMPLE_STRATEGIES)
    renderList()

    await waitFor(() => {
      expect(screen.getAllByTestId('strategy-row')).toHaveLength(3)
    })

    fireEvent.click(screen.getByTestId('filter-user'))

    await waitFor(() => {
      expect(screen.getAllByTestId('strategy-row')).toHaveLength(1)
    })
    expect(screen.getByText('SQLi Hunter')).toBeVisible()
  })

  it('shows empty state when no strategies match filter', async () => {
    mockListStrategiesFull.mockResolvedValue(SAMPLE_STRATEGIES)
    renderList()

    await waitFor(() => {
      expect(screen.getAllByTestId('strategy-row')).toHaveLength(3)
    })

    // Filter to diff_review (none of our samples have it)
    const shapeFilter = screen.getByTestId('shape-filter')
    fireEvent.change(shapeFilter, { target: { value: 'diff_review' } })

    await waitFor(() => {
      expect(screen.queryAllByTestId('strategy-row')).toHaveLength(0)
    })
    expect(screen.getByText(/No strategies match/)).toBeVisible()
  })

  it('shows View button for each strategy row', async () => {
    mockListStrategiesFull.mockResolvedValue(SAMPLE_STRATEGIES)
    renderList()

    await waitFor(() => {
      expect(screen.getAllByTestId('view-btn')).toHaveLength(3)
    })
  })

  it('shows error when API fails', async () => {
    mockListStrategiesFull.mockRejectedValue(new Error('Network error'))
    renderList()

    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeVisible()
    })
  })

  it('shows loading state initially', () => {
    // Never-resolving promise to keep loading state
    mockListStrategiesFull.mockReturnValue(new Promise(() => {}))
    renderList()

    expect(screen.getByText(/Loading/)).toBeVisible()
  })
})
