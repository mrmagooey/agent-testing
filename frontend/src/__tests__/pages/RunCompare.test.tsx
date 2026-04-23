import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import RunCompare from '../../pages/RunCompare'
import type { RunComparison } from '../../api/client'

vi.mock('../../api/client', () => ({
  compareRunsCross: vi.fn(),
  listExperiments: vi.fn(),
  listRuns: vi.fn(),
}))

import { compareRunsCross, listExperiments, listRuns } from '../../api/client'
const mockCompareRunsCross = vi.mocked(compareRunsCross)
const mockListExperiments = vi.mocked(listExperiments)
const mockListRuns = vi.mocked(listRuns)

function makeComparison(overrides: Partial<RunComparison> = {}): RunComparison {
  return {
    run_a: {
      run_id: 'run-a',
      experiment_id: 'exp-1',
      experiment_name: 'Experiment 1',
      dataset: 'ds-alpha',
      model: 'gpt-4o',
      strategy: 'zero_shot',
      tool_variant: 'with_tools',
      profile: 'default',
      verification: 'none',
      status: 'completed',
      precision: 0.85,
      recall: 0.75,
      f1: 0.80,
      cost_usd: 0.5,
    },
    run_b: {
      run_id: 'run-b',
      experiment_id: 'exp-2',
      experiment_name: 'Experiment 2',
      dataset: 'ds-beta',
      model: 'claude-3-5-sonnet',
      strategy: 'zero_shot',
      tool_variant: 'without_tools',
      profile: 'default',
      verification: 'none',
      status: 'completed',
      precision: 0.80,
      recall: 0.70,
      f1: 0.75,
      cost_usd: 0.3,
    },
    found_by_both: [
      {
        finding_id: 'f-both',
        run_id: 'run-a',
        experiment_id: 'exp-1',
        title: 'Shared Finding',
        description: 'Both runs found this.',
        vuln_class: 'sqli',
        severity: 'high',
        match_status: 'tp',
      },
    ],
    only_in_a: [],
    only_in_b: [],
    dataset_mismatch: false,
    warnings: [],
    ...overrides,
  }
}

function renderCompare(path: string, initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path={path} element={<RunCompare />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockListExperiments.mockResolvedValue([])
  mockListRuns.mockResolvedValue([])
})

describe('RunCompare — cross-experiment route (/compare)', () => {
  it('fetches comparison when all four params are present', async () => {
    mockCompareRunsCross.mockResolvedValue(makeComparison())

    renderCompare(
      '/compare',
      '/compare?a_experiment=exp-1&a_run=run-a&b_experiment=exp-2&b_run=run-b',
    )

    await waitFor(() => {
      expect(mockCompareRunsCross).toHaveBeenCalledWith({
        aExperiment: 'exp-1',
        aRun: 'run-a',
        bExperiment: 'exp-2',
        bRun: 'run-b',
      })
    })
  })

  it('shows run A and run B cards after loading', async () => {
    mockCompareRunsCross.mockResolvedValue(makeComparison())

    renderCompare(
      '/compare',
      '/compare?a_experiment=exp-1&a_run=run-a&b_experiment=exp-2&b_run=run-b',
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Run A' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Run B' })).toBeInTheDocument()
    })
  })

  it('does NOT show dataset mismatch banner when dataset_mismatch is false', async () => {
    mockCompareRunsCross.mockResolvedValue(makeComparison({ dataset_mismatch: false, warnings: [] }))

    renderCompare(
      '/compare',
      '/compare?a_experiment=exp-1&a_run=run-a&b_experiment=exp-2&b_run=run-b',
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Run A' })).toBeInTheDocument()
    })

    expect(screen.queryByTestId('dataset-mismatch-banner')).not.toBeInTheDocument()
  })

  it('renders dataset mismatch banner when dataset_mismatch is true', async () => {
    mockCompareRunsCross.mockResolvedValue(
      makeComparison({
        dataset_mismatch: true,
        warnings: ['Datasets differ: ds-alpha vs ds-beta — only findings with identical file paths will match via FindingIdentity'],
      }),
    )

    renderCompare(
      '/compare',
      '/compare?a_experiment=exp-1&a_run=run-a&b_experiment=exp-2&b_run=run-b',
    )

    await waitFor(() => {
      expect(screen.getByTestId('dataset-mismatch-banner')).toBeInTheDocument()
    })

    expect(screen.getByText(/Dataset mismatch/)).toBeInTheDocument()
    expect(screen.getByText(/Datasets differ/)).toBeInTheDocument()
  })

  it('shows picker card on /compare route', () => {
    vi.mocked(mockCompareRunsCross).mockResolvedValue(makeComparison())

    renderCompare('/compare', '/compare')

    // Two RunPicker instances render — one for Run A, one for Run B
    const experimentSelects = screen.getAllByLabelText('Experiment')
    expect(experimentSelects).toHaveLength(2)
  })

  it('shows prompt to select runs when params are missing', () => {
    renderCompare('/compare', '/compare')

    expect(screen.getByText(/Select two runs above/)).toBeInTheDocument()
  })

  it('uses cross-experiment breadcrumb when experiments differ', async () => {
    mockCompareRunsCross.mockResolvedValue(makeComparison())

    renderCompare(
      '/compare',
      '/compare?a_experiment=exp-1&a_run=run-a&b_experiment=exp-2&b_run=run-b',
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Run A' })).toBeInTheDocument()
    })

    expect(screen.getByText('Compare')).toBeInTheDocument()
  })
})

describe('RunCompare — legacy route (/experiments/:id/compare)', () => {
  it('fetches using both-sides-same-experiment on /experiments/:id/compare route', async () => {
    mockCompareRunsCross.mockResolvedValue(
      makeComparison({ dataset_mismatch: false, warnings: [] }),
    )

    renderCompare('/experiments/:id/compare', '/experiments/exp-legacy/compare?a=run-a&b=run-b')

    await waitFor(() => {
      expect(mockCompareRunsCross).toHaveBeenCalledWith({
        aExperiment: 'exp-legacy',
        aRun: 'run-a',
        bExperiment: 'exp-legacy',
        bRun: 'run-b',
      })
    })
  })

  it('shows the picker on the legacy route so users can switch runs', () => {
    mockCompareRunsCross.mockResolvedValue(makeComparison())

    renderCompare('/experiments/:id/compare', '/experiments/exp-legacy/compare?a=run-a&b=run-b')

    // Picker is always rendered on both routes (see RunCompare.tsx comment
    // "Pickers — always shown so users can select/change runs on any route").
    expect(screen.queryAllByLabelText('Experiment')).toHaveLength(2)
  })

  it('shows experiment ID in breadcrumb on legacy route', () => {
    mockCompareRunsCross.mockResolvedValue(makeComparison())

    renderCompare('/experiments/:id/compare', '/experiments/exp-legacy/compare?a=run-a&b=run-b')

    expect(screen.getByText('exp-legacy')).toBeInTheDocument()
  })
})
