import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ExperimentDetail from '../../pages/ExperimentDetail'
import type { Experiment, Run, Finding } from '../../api/client'

vi.mock('../../api/client', () => ({
  getExperiment: vi.fn(),
  getExperimentResults: vi.fn(),
  cancelExperiment: vi.fn(),
}))

// Mock heavy sub-components
vi.mock('../../components/AccuracyHeatmap', () => ({
  default: () => <div data-testid="accuracy-heatmap" />,
}))
vi.mock('../../components/DimensionChart', () => ({
  default: ({ title }: { title: string }) => <div data-testid="dimension-chart">{title}</div>,
}))
vi.mock('../../components/FindingsExplorer', () => ({
  default: () => <div data-testid="findings-explorer" />,
}))
vi.mock('../../components/DownloadButton', () => ({
  default: ({ label }: { label?: string }) => <button>{label ?? 'Download'}</button>,
}))
vi.mock('../../components/ExportMenu', () => ({
  default: ({ experimentId }: { experimentId: string }) => (
    <button data-testid="export-menu">{`Export ${experimentId}`}</button>
  ),
}))

import { getExperiment, getExperimentResults, cancelExperiment } from '../../api/client'
const mockGetExperiment = vi.mocked(getExperiment)
const mockGetExperimentResults = vi.mocked(getExperimentResults)
const mockCancelExperiment = vi.mocked(cancelExperiment)

function makeExperiment(overrides: Partial<Experiment> = {}): Experiment {
  return {
    experiment_id: 'exp-detail-1',
    status: 'completed',
    dataset: 'ds-alpha',
    created_at: new Date(Date.now() - 300_000).toISOString(),
    completed_at: new Date().toISOString(),
    total_runs: 3,
    completed_runs: 3,
    running_runs: 0,
    pending_runs: 0,
    failed_runs: 0,
    total_cost_usd: 0.75,
    ...overrides,
  }
}

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    run_id: 'run-1',
    experiment_id: 'exp-detail-1',
    model: 'gpt-4o',
    strategy: 'zero_shot',
    tool_variant: 'none',
    profile: 'default',
    verification: 'none',
    status: 'completed',
    precision: 0.8,
    recall: 0.7,
    f1: 0.75,
    cost_usd: 0.25,
    ...overrides,
  }
}

function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    finding_id: 'f-1',
    run_id: 'run-1',
    experiment_id: 'exp-detail-1',
    title: 'SQL Injection',
    description: 'User input not sanitized.',
    vuln_class: 'sqli',
    severity: 'high',
    match_status: 'tp',
    ...overrides,
  }
}

function renderPage(experimentId = 'exp-detail-1') {
  return render(
    <MemoryRouter initialEntries={[`/experiments/${experimentId}`]}>
      <Routes>
        <Route path="/experiments/:id" element={<ExperimentDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetExperimentResults.mockResolvedValue({ runs: [], findings: [] })
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('ExperimentDetail — loading state', () => {
  it('shows loading text while experiment is fetching', () => {
    mockGetExperiment.mockReturnValue(new Promise(() => {}))
    renderPage()
    expect(screen.getByText(/Loading experiment/i)).toBeInTheDocument()
  })
})

describe('ExperimentDetail — error state', () => {
  it('renders error message when getExperiment rejects', async () => {
    mockGetExperiment.mockRejectedValue(new Error('Not found'))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/Not found/)).toBeInTheDocument()
    })
  })
})

describe('ExperimentDetail — completed experiment', () => {
  it('renders experiment ID as heading', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment())
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /exp-detail-1/ })).toBeInTheDocument()
    })
  })

  it('shows dataset name', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment())
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/ds-alpha/)).toBeInTheDocument()
    })
  })

  it('renders completed status badge', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'completed' }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('completed')).toBeInTheDocument()
    })
  })

  it('does NOT show Cancel button for completed experiment', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'completed' }))
    renderPage()
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /cancel/i })).not.toBeInTheDocument()
    })
  })

  it('loads and shows results when experiment is terminal', async () => {
    const runs = [makeRun()]
    const findings = [makeFinding()]
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'completed' }))
    mockGetExperimentResults.mockResolvedValue({ runs, findings })

    renderPage()

    await waitFor(() => {
      expect(mockGetExperimentResults).toHaveBeenCalledWith('exp-detail-1')
    })
  })

  it('renders Experiment Matrix section heading when results load', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'completed' }))
    mockGetExperimentResults.mockResolvedValue({ runs: [makeRun()], findings: [] })

    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Experiment Matrix/i })).toBeInTheDocument()
    })
  })

  it('shows Cost Analysis section', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'completed' }))
    mockGetExperimentResults.mockResolvedValue({ runs: [makeRun()], findings: [] })

    renderPage()

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Cost Analysis/i })).toBeInTheDocument()
    })
  })
})

describe('ExperimentDetail — running experiment', () => {
  it('shows Cancel button for running experiment', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'running', completed_runs: 1, total_runs: 4 }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument()
    })
  })

  it('does NOT call getExperimentResults for non-terminal experiment', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'running', completed_runs: 1, total_runs: 4 }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /exp-detail-1/ })).toBeInTheDocument()
    })
    expect(mockGetExperimentResults).not.toHaveBeenCalled()
  })
})

describe('ExperimentDetail — cancel modal', () => {
  it('shows cancel confirmation modal when Cancel is clicked', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'running', completed_runs: 1, total_runs: 4 }))
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

    expect(screen.getByText(/Stop all pending runs/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /keep running/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /stop experiment/i })).toBeInTheDocument()
  })

  it('closes modal when Keep Running is clicked', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'running', completed_runs: 1, total_runs: 4 }))
    renderPage()
    await waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    })

    fireEvent.click(screen.getByRole('button', { name: /keep running/i }))

    expect(screen.queryByText(/Stop all pending runs/i)).not.toBeInTheDocument()
  })

  it('calls cancelExperiment when Stop experiment is confirmed', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'running', completed_runs: 1, total_runs: 4 }))
    mockCancelExperiment.mockResolvedValue(undefined)
    renderPage()
    await waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    })

    fireEvent.click(screen.getByRole('button', { name: /stop experiment/i }))

    await waitFor(() => {
      expect(mockCancelExperiment).toHaveBeenCalledWith('exp-detail-1')
    })
  })
})

describe('ExperimentDetail — filter: no runs match', () => {
  it('shows no-match message and clear-filters button when filter yields 0 runs', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'completed' }))
    mockGetExperimentResults.mockResolvedValue({
      runs: [makeRun({ model: 'gpt-4o' })],
      findings: [],
    })

    render(
      <MemoryRouter initialEntries={['/experiments/exp-detail-1?model=nonexistent']}>
        <Routes>
          <Route path="/experiments/:id" element={<ExperimentDetail />} />
        </Routes>
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText(/No runs match these filters/i)).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /clear filters/i })).toBeInTheDocument()
  })
})
