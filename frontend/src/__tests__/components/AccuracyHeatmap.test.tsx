import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import AccuracyHeatmap from '../../components/AccuracyHeatmap'
import type { AccuracyMatrix } from '../../api/client'

vi.mock('../../api/client', () => ({
  getAccuracyMatrix: vi.fn(),
}))

import { getAccuracyMatrix } from '../../api/client'
const mockGetAccuracyMatrix = vi.mocked(getAccuracyMatrix)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('AccuracyHeatmap', () => {
  it('shows empty state when no completed runs with evaluation data', async () => {
    const empty: AccuracyMatrix = { models: [], strategies: [], cells: [] }
    mockGetAccuracyMatrix.mockResolvedValue(empty)

    render(<AccuracyHeatmap />)

    await waitFor(() => {
      expect(screen.getByText(/No completed runs with evaluation data yet/)).toBeInTheDocument()
    })
  })

  it('renders heatmap table with model rows and strategy columns', async () => {
    const matrix: AccuracyMatrix = {
      models: ['gpt-4o', 'claude-opus-4'],
      strategies: ['single_agent', 'per_file'],
      cells: [
        { model: 'gpt-4o', strategy: 'single_agent', accuracy: 0.75, run_count: 3 },
        { model: 'gpt-4o', strategy: 'per_file', accuracy: 0.60, run_count: 2 },
        { model: 'claude-opus-4', strategy: 'single_agent', accuracy: 0.90, run_count: 4 },
        { model: 'claude-opus-4', strategy: 'per_file', accuracy: 0.85, run_count: 2 },
      ],
    }
    mockGetAccuracyMatrix.mockResolvedValue(matrix)

    render(<AccuracyHeatmap />)

    await waitFor(() => {
      expect(screen.getByTestId('accuracy-heatmap')).toBeInTheDocument()
    })

    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('claude-opus-4')).toBeInTheDocument()
    expect(screen.getByText('single_agent')).toBeInTheDocument()
    expect(screen.getByText('per_file')).toBeInTheDocument()
    expect(screen.getByText('0.750')).toBeInTheDocument()
    expect(screen.getByText('0.900')).toBeInTheDocument()
  })

  it('shows loading skeleton while fetching', () => {
    mockGetAccuracyMatrix.mockReturnValue(new Promise(() => {}))

    const { container } = render(<AccuracyHeatmap />)

    expect(container.querySelector('.animate-pulse')).toBeInTheDocument()
  })

  it('shows error message on fetch failure', async () => {
    mockGetAccuracyMatrix.mockRejectedValue(new Error('Network error'))

    render(<AccuracyHeatmap />)

    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeInTheDocument()
    })
  })

  it('renders empty cells as dashes for missing model/strategy combinations', async () => {
    const matrix: AccuracyMatrix = {
      models: ['gpt-4o', 'claude-opus-4'],
      strategies: ['single_agent', 'per_file'],
      cells: [
        { model: 'gpt-4o', strategy: 'single_agent', accuracy: 0.75, run_count: 3 },
      ],
    }
    mockGetAccuracyMatrix.mockResolvedValue(matrix)

    render(<AccuracyHeatmap />)

    await waitFor(() => {
      expect(screen.getByTestId('accuracy-heatmap')).toBeInTheDocument()
    })

    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(3)
  })
})
