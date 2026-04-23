import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Feedback from '../../pages/Feedback'
import type { Experiment, Dataset, FPPattern, TrendResponse } from '../../api/client'

vi.mock('../../api/client', () => ({
  listExperiments: vi.fn(),
  listDatasets: vi.fn(),
  getTrends: vi.fn(),
  compareExperiments: vi.fn(),
  getFPPatterns: vi.fn(),
}))

// Mock TrendGrid — recharts heavy
vi.mock('../../components/TrendGrid', () => ({
  default: () => <div data-testid="trend-grid" />,
}))

import {
  listExperiments,
  listDatasets,
  getTrends,
  compareExperiments,
  getFPPatterns,
} from '../../api/client'

const mockListExperiments = vi.mocked(listExperiments)
const mockListDatasets = vi.mocked(listDatasets)
const mockGetTrends = vi.mocked(getTrends)
const mockCompareExperiments = vi.mocked(compareExperiments)
const mockGetFPPatterns = vi.mocked(getFPPatterns)

function makeExperiment(overrides: Partial<Experiment> = {}): Experiment {
  return {
    experiment_id: 'exp-completed-1',
    status: 'completed',
    dataset: 'ds-test',
    created_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    total_runs: 2,
    completed_runs: 2,
    running_runs: 0,
    pending_runs: 0,
    failed_runs: 0,
    total_cost_usd: 0.5,
    ...overrides,
  }
}

function makeDataset(overrides: Partial<Dataset> = {}): Dataset {
  return {
    name: 'ds-test',
    source: 'manual',
    label_count: 5,
    file_count: 10,
    size_bytes: 1024,
    created_at: new Date().toISOString(),
    languages: ['python'],
    ...overrides,
  }
}

function makeFPPattern(overrides: Partial<FPPattern> = {}): FPPattern {
  return {
    model: 'gpt-4o',
    vuln_class: 'sqli',
    pattern: 'Missing input validation',
    count: 3,
    suggested_action: 'Add stricter prompting for DB queries',
    ...overrides,
  }
}

function makeTrendResponse(): TrendResponse {
  return {
    dataset: 'ds-test',
    experiments: [{ experiment_id: 'exp-1', completed_at: new Date().toISOString() }],
    series: [],
  }
}

function renderFeedback() {
  return render(
    <MemoryRouter>
      <Feedback />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  // Clear localStorage keys used by Feedback
  localStorage.clear()
  mockListExperiments.mockResolvedValue([])
  mockListDatasets.mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('Feedback — loading and initial render', () => {
  it('shows loading spinner while initial data fetches', () => {
    mockListExperiments.mockReturnValue(new Promise(() => {}))
    mockListDatasets.mockReturnValue(new Promise(() => {}))
    renderFeedback()
    // PageLoadingSpinner renders — heading not yet visible
    expect(screen.queryByRole('heading', { name: 'Feedback' })).not.toBeInTheDocument()
  })

  it('renders Feedback heading after data loads', async () => {
    renderFeedback()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Feedback' })).toBeInTheDocument()
    })
  })
})

describe('Feedback — Trends section', () => {
  it('renders Trends section heading', async () => {
    renderFeedback()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Trends' })).toBeInTheDocument()
    })
  })

  it('renders dataset select with "Select dataset…" placeholder', async () => {
    renderFeedback()
    await waitFor(() => {
      expect(screen.getByText('Select dataset…')).toBeInTheDocument()
    })
  })

  it('populates dataset dropdown with available datasets', async () => {
    mockListDatasets.mockResolvedValue([makeDataset({ name: 'ds-alpha' })])
    renderFeedback()
    await waitFor(() => {
      const option = screen.getByRole('option', { name: 'ds-alpha' })
      expect(option).toBeInTheDocument()
    })
  })

  it('Load Trends button is disabled when no dataset is selected', async () => {
    renderFeedback()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /load trends/i })).toBeDisabled()
    })
  })

  it('calls getTrends when Load Trends is clicked with a dataset selected', async () => {
    mockListDatasets.mockResolvedValue([makeDataset({ name: 'ds-test' })])
    mockGetTrends.mockResolvedValue(makeTrendResponse())
    renderFeedback()

    await waitFor(() => {
      expect(screen.getByText('ds-test')).toBeInTheDocument()
    })

    // Select the dataset
    const allDatasetSelects = screen.getAllByRole('combobox')
    // First combobox in Trends section is the dataset select
    fireEvent.change(allDatasetSelects[0], { target: { value: 'ds-test' } })

    fireEvent.click(screen.getByRole('button', { name: /load trends/i }))

    await waitFor(() => {
      expect(mockGetTrends).toHaveBeenCalledWith(
        'ds-test',
        expect.objectContaining({ limit: expect.any(Number) }),
      )
    })
  })

  it('shows TrendGrid after trends load', async () => {
    mockListDatasets.mockResolvedValue([makeDataset({ name: 'ds-test' })])
    mockGetTrends.mockResolvedValue(makeTrendResponse())
    renderFeedback()

    await waitFor(() => {
      expect(screen.getByText('ds-test')).toBeInTheDocument()
    })

    const allDatasetSelects = screen.getAllByRole('combobox')
    fireEvent.change(allDatasetSelects[0], { target: { value: 'ds-test' } })
    fireEvent.click(screen.getByRole('button', { name: /load trends/i }))

    await waitFor(() => {
      expect(screen.getByTestId('trend-grid')).toBeInTheDocument()
    })
  })

  it('shows error message when getTrends fails', async () => {
    mockListDatasets.mockResolvedValue([makeDataset({ name: 'ds-test' })])
    mockGetTrends.mockRejectedValue(new Error('Trends unavailable'))
    renderFeedback()

    await waitFor(() => {
      expect(screen.getByText('ds-test')).toBeInTheDocument()
    })

    const allDatasetSelects = screen.getAllByRole('combobox')
    fireEvent.change(allDatasetSelects[0], { target: { value: 'ds-test' } })
    fireEvent.click(screen.getByRole('button', { name: /load trends/i }))

    await waitFor(() => {
      expect(screen.getByText(/Trends unavailable/)).toBeInTheDocument()
    })
  })
})

describe('Feedback — Experiment Comparison section', () => {
  it('renders Experiment Comparison heading', async () => {
    renderFeedback()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Experiment Comparison/ })).toBeInTheDocument()
    })
  })

  it('Compare button is disabled when no experiments are selected', async () => {
    renderFeedback()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Compare$/ })).toBeDisabled()
    })
  })

  it('shows only completed experiments in dropdowns', async () => {
    mockListExperiments.mockResolvedValue([
      makeExperiment({ experiment_id: 'exp-done', status: 'completed' }),
      makeExperiment({ experiment_id: 'exp-running', status: 'running' }),
    ])
    renderFeedback()

    await waitFor(() => {
      // "exp-done" should appear, "exp-running" should not
      expect(screen.queryByText(/exp-running/)).not.toBeInTheDocument()
    })
  })

  it('calls compareExperiments and shows Metric Deltas', async () => {
    const expA = makeExperiment({ experiment_id: 'exp-aaaaaaaaaaaa' })
    const expB = makeExperiment({ experiment_id: 'exp-bbbbbbbbbbbb' })
    mockListExperiments.mockResolvedValue([expA, expB])
    mockCompareExperiments.mockResolvedValue({
      metric_deltas: [
        {
          experiment_id: 'exp-bbbbbbbbbbbb',
          precision_delta: 0.05,
          recall_delta: 0.02,
          f1_delta: 0.03,
        },
      ],
      fp_patterns: [],
      stability: {},
    })
    renderFeedback()

    // Wait for the page to finish loading (experiments populated)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Compare$/ })).toBeInTheDocument()
    })

    // Select exp A and exp B from the experiment A/B dropdowns
    // comboboxes: [0]=trends dataset, [1]=trends "Last N", [2]=exp A, [3]=exp B, [4]=FP
    const experimentSelects = screen.getAllByRole('combobox')
    fireEvent.change(experimentSelects[2], { target: { value: 'exp-aaaaaaaaaaaa' } })
    fireEvent.change(experimentSelects[3], { target: { value: 'exp-bbbbbbbbbbbb' } })

    fireEvent.click(screen.getByRole('button', { name: /^Compare$/ }))

    await waitFor(() => {
      expect(mockCompareExperiments).toHaveBeenCalledWith('exp-aaaaaaaaaaaa', 'exp-bbbbbbbbbbbb')
    })
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /Metric Deltas/i })).toBeInTheDocument()
    })
  })

  it('shows compare error message on failure', async () => {
    mockListExperiments.mockResolvedValue([
      makeExperiment({ experiment_id: 'exp-aaaaaaaaaaaa' }),
      makeExperiment({ experiment_id: 'exp-bbbbbbbbbbbb' }),
    ])
    mockCompareExperiments.mockRejectedValue(new Error('Comparison failed'))
    renderFeedback()

    // Wait for the page to finish loading (experiments populated)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^Compare$/ })).toBeInTheDocument()
    })

    // comboboxes: [0]=trends dataset, [1]=trends "Last N", [2]=exp A, [3]=exp B, [4]=FP
    const experimentSelects = screen.getAllByRole('combobox')
    fireEvent.change(experimentSelects[2], { target: { value: 'exp-aaaaaaaaaaaa' } })
    fireEvent.change(experimentSelects[3], { target: { value: 'exp-bbbbbbbbbbbb' } })
    fireEvent.click(screen.getByRole('button', { name: /^Compare$/ }))

    await waitFor(() => {
      expect(screen.getByText(/Comparison failed/)).toBeInTheDocument()
    })
  })
})

describe('Feedback — FP Pattern Browser', () => {
  it('renders FP Pattern Browser section heading', async () => {
    renderFeedback()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /FP Pattern Browser/i })).toBeInTheDocument()
    })
  })

  it('Load Patterns button is disabled when no experiment is selected', async () => {
    renderFeedback()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /load patterns/i })).toBeDisabled()
    })
  })

  it('calls getFPPatterns and renders patterns table', async () => {
    const exp = makeExperiment({ experiment_id: 'exp-fp-test' })
    mockListExperiments.mockResolvedValue([exp])
    mockGetFPPatterns.mockResolvedValue([
      makeFPPattern({ model: 'gpt-4o', vuln_class: 'sqli', pattern: 'Missing validation', count: 5 }),
    ])
    renderFeedback()

    // Wait for the page to finish loading (experiments populated)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /load patterns/i })).toBeInTheDocument()
    })

    // FP experiment select is the last combobox
    const allSelects = screen.getAllByRole('combobox')
    const fpSelect = allSelects[allSelects.length - 1]
    fireEvent.change(fpSelect, { target: { value: 'exp-fp-test' } })

    fireEvent.click(screen.getByRole('button', { name: /load patterns/i }))

    await waitFor(() => {
      expect(mockGetFPPatterns).toHaveBeenCalledWith('exp-fp-test')
    })
    await waitFor(() => {
      expect(screen.getByText('Missing validation')).toBeInTheDocument()
      // Count cell — use getAllByText since "5" also appears as a "Last N" option
      const fives = screen.getAllByText('5')
      expect(fives.length).toBeGreaterThanOrEqual(1)
      const countCell = fives.find((el) => el.tagName === 'TD')
      expect(countCell).toBeInTheDocument()
    })
  })

  it('shows no-patterns message when getFPPatterns returns empty', async () => {
    const exp = makeExperiment({ experiment_id: 'exp-fp-empty' })
    mockListExperiments.mockResolvedValue([exp])
    mockGetFPPatterns.mockResolvedValue([])
    renderFeedback()

    // Wait for the page to finish loading (experiments populated)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /load patterns/i })).toBeInTheDocument()
    })

    const allSelects = screen.getAllByRole('combobox')
    const fpSelect = allSelects[allSelects.length - 1]
    fireEvent.change(fpSelect, { target: { value: 'exp-fp-empty' } })

    fireEvent.click(screen.getByRole('button', { name: /load patterns/i }))

    await waitFor(() => {
      expect(screen.getByText(/No FP patterns found/i)).toBeInTheDocument()
    })
  })
})
