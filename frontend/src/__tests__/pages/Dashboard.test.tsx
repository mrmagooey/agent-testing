import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Dashboard from '../../pages/Dashboard'

// Mock recharts to avoid ResizeObserver issues in jsdom
vi.mock('recharts', () => ({
  LineChart: ({ children }: { children: React.ReactNode }) => <div data-testid="line-chart">{children}</div>,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

// Mock AccuracyHeatmap — heavyweight chart component
vi.mock('../../components/AccuracyHeatmap', () => ({
  default: () => <div data-testid="accuracy-heatmap" />,
}))

vi.mock('../../api/client', () => ({
  listExperiments: vi.fn(),
  runSmokeTest: vi.fn(),
}))

import { listExperiments, runSmokeTest } from '../../api/client'
const mockListExperiments = vi.mocked(listExperiments)
const mockRunSmokeTest = vi.mocked(runSmokeTest)

import type { Experiment } from '../../api/client'

function makeExperiment(overrides: Partial<Experiment> = {}): Experiment {
  return {
    experiment_id: 'exp-abc12345',
    status: 'completed',
    dataset: 'ds-test',
    created_at: new Date(Date.now() - 60_000).toISOString(),
    completed_at: new Date().toISOString(),
    total_runs: 4,
    completed_runs: 4,
    running_runs: 0,
    pending_runs: 0,
    failed_runs: 0,
    total_cost_usd: 1.23,
    ...overrides,
  }
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Dashboard — initial render and loading', () => {
  it('shows loading spinner while data is fetching', () => {
    mockListExperiments.mockReturnValue(new Promise(() => {})) // never resolves
    renderDashboard()
    // PageLoadingSpinner renders a spinner; we detect the loading state
    // by the absence of the Dashboard heading (not yet rendered)
    expect(screen.queryByRole('heading', { name: 'Dashboard' })).not.toBeInTheDocument()
  })

  it('renders Dashboard heading after data loads', async () => {
    mockListExperiments.mockResolvedValue([])
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    })
  })

  it('shows error message when listExperiments rejects', async () => {
    mockListExperiments.mockRejectedValue(new Error('Network failure'))
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText(/Network failure/)).toBeInTheDocument()
    })
  })
})

describe('Dashboard — empty state', () => {
  it('shows no-active-experiments empty state when list is empty', async () => {
    mockListExperiments.mockResolvedValue([])
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('No active experiments.')).toBeInTheDocument()
    })
  })

  it('shows no-completed-experiments empty state when list is empty', async () => {
    mockListExperiments.mockResolvedValue([])
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('No completed experiments yet.')).toBeInTheDocument()
    })
  })
})

describe('Dashboard — data loaded', () => {
  it('shows running experiment in active section', async () => {
    const exp = makeExperiment({ status: 'running', completed_runs: 2, total_runs: 4 })
    mockListExperiments.mockResolvedValue([exp])
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText(/running/i)).toBeInTheDocument()
    })
    // Dataset name displayed
    expect(screen.getByText('ds-test')).toBeInTheDocument()
  })

  it('shows completed experiment in recent section', async () => {
    const exp = makeExperiment({ status: 'completed' })
    mockListExperiments.mockResolvedValue([exp])
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    })
    // Cost is shown
    expect(screen.getByText('$1.23')).toBeInTheDocument()
  })

  it('renders smoke-test button', async () => {
    mockListExperiments.mockResolvedValue([])
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /run smoke test/i })).toBeInTheDocument()
    })
  })

  it('renders New Experiment and Compare navigation buttons', async () => {
    mockListExperiments.mockResolvedValue([])
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /new experiment/i })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /compare/i })).toBeInTheDocument()
    })
  })
})

describe('Dashboard — smoke test interactions', () => {
  it('shows success state and experiment link after successful smoke test', async () => {
    mockListExperiments.mockResolvedValue([])
    mockRunSmokeTest.mockResolvedValue({
      experiment_id: 'smoke-exp-1',
      message: 'Smoke test submitted.',
      total_runs: 1,
    })
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /run smoke test/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /run smoke test/i }))

    await waitFor(() => {
      expect(screen.getByText(/Smoke test submitted/)).toBeInTheDocument()
    })
    expect(screen.getByRole('link', { name: /view experiment/i })).toBeInTheDocument()
  })

  it('shows error message when smoke test fails', async () => {
    mockListExperiments.mockResolvedValue([])
    mockRunSmokeTest.mockRejectedValue(new Error('Coordinator unavailable'))
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /run smoke test/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /run smoke test/i }))

    await waitFor(() => {
      expect(screen.getByText(/Coordinator unavailable/)).toBeInTheDocument()
    })
  })
})

describe('Dashboard — a11y', () => {
  it('smoke-test button is a button role', async () => {
    mockListExperiments.mockResolvedValue([])
    renderDashboard()
    await waitFor(() => {
      const btn = screen.getByRole('button', { name: /run smoke test/i })
      expect(btn.tagName).toBe('BUTTON')
    })
  })
})
