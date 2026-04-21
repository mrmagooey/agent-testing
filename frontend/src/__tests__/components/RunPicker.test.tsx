import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import RunPicker from '../../components/RunPicker'

vi.mock('../../api/client', () => ({
  listExperiments: vi.fn(),
  listRuns: vi.fn(),
}))

import { listExperiments, listRuns } from '../../api/client'
import type { Experiment, Run } from '../../api/client'
const mockListExperiments = vi.mocked(listExperiments)
const mockListRuns = vi.mocked(listRuns)

function makeExperiment(id: string): Partial<Experiment> {
  return {
    experiment_id: id,
    status: 'completed',
    dataset: `dataset-${id}`,
    created_at: '2026-04-20T00:00:00Z',
    total_runs: 4,
    completed_runs: 4,
    running_runs: 0,
    pending_runs: 0,
    failed_runs: 0,
    total_cost_usd: 1.0,
  }
}

function makeRun(id: string, experimentId: string): Partial<Run> {
  return {
    run_id: id,
    experiment_id: experimentId,
    model: 'gpt-4o',
    strategy: 'zero_shot',
    tool_variant: 'with_tools',
    profile: 'default',
    verification: 'none',
    status: 'completed',
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('RunPicker', () => {
  it('renders experiment and run selects', async () => {
    mockListExperiments.mockResolvedValue([makeExperiment('exp-1')] as Experiment[])

    render(
      <RunPicker
        label="Run A"
        selectedExperiment=""
        selectedRun=""
        onExperimentChange={() => {}}
        onRunChange={() => {}}
      />,
    )

    expect(screen.getByText('Run A')).toBeInTheDocument()
    expect(screen.getByLabelText('Experiment')).toBeInTheDocument()
    expect(screen.getByLabelText('Run')).toBeInTheDocument()
  })

  it('loads experiments on mount and populates experiment select', async () => {
    mockListExperiments.mockResolvedValue([
      makeExperiment('exp-1'),
      makeExperiment('exp-2'),
    ] as Experiment[])

    render(
      <RunPicker
        label="Run A"
        selectedExperiment=""
        selectedRun=""
        onExperimentChange={() => {}}
        onRunChange={() => {}}
      />,
    )

    await waitFor(() => {
      expect(screen.getByRole('option', { name: /exp-1/ })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: /exp-2/ })).toBeInTheDocument()
    })
  })

  it('run select is disabled when no experiment is selected', async () => {
    mockListExperiments.mockResolvedValue([makeExperiment('exp-1')] as Experiment[])

    render(
      <RunPicker
        label="Run A"
        selectedExperiment=""
        selectedRun=""
        onExperimentChange={() => {}}
        onRunChange={() => {}}
      />,
    )

    await waitFor(() => {
      expect(screen.getByRole('option', { name: /exp-1/ })).toBeInTheDocument()
    })

    expect(screen.getByLabelText('Run')).toBeDisabled()
  })

  it('loads runs when an experiment is selected', async () => {
    mockListExperiments.mockResolvedValue([makeExperiment('exp-1')] as Experiment[])
    mockListRuns.mockResolvedValue([
      makeRun('run-a', 'exp-1'),
      makeRun('run-b', 'exp-1'),
    ] as Run[])

    render(
      <RunPicker
        label="Run A"
        selectedExperiment="exp-1"
        selectedRun=""
        onExperimentChange={() => {}}
        onRunChange={() => {}}
      />,
    )

    await waitFor(() => {
      expect(mockListRuns).toHaveBeenCalledWith('exp-1')
    })

    await waitFor(() => {
      const opts = screen.getAllByRole('option', { name: /gpt-4o/ })
      expect(opts.length).toBeGreaterThan(0)
    })
  })

  it('calls onExperimentChange and resets run when experiment select changes', async () => {
    const user = userEvent.setup()
    const onExperimentChange = vi.fn()
    const onRunChange = vi.fn()

    mockListExperiments.mockResolvedValue([
      makeExperiment('exp-1'),
      makeExperiment('exp-2'),
    ] as Experiment[])
    mockListRuns.mockResolvedValue([])

    render(
      <RunPicker
        label="Run A"
        selectedExperiment=""
        selectedRun=""
        onExperimentChange={onExperimentChange}
        onRunChange={onRunChange}
      />,
    )

    await waitFor(() => {
      expect(screen.getByRole('option', { name: /exp-1/ })).toBeInTheDocument()
    })

    await user.selectOptions(screen.getByLabelText('Experiment'), 'exp-1')

    expect(onExperimentChange).toHaveBeenCalledWith('exp-1')
    expect(onRunChange).toHaveBeenCalledWith('')
  })

  it('calls onRunChange when a run is selected', async () => {
    const user = userEvent.setup()
    const onRunChange = vi.fn()

    mockListExperiments.mockResolvedValue([makeExperiment('exp-1')] as Experiment[])
    mockListRuns.mockResolvedValue([makeRun('run-x', 'exp-1')] as Run[])

    render(
      <RunPicker
        label="Run A"
        selectedExperiment="exp-1"
        selectedRun=""
        onExperimentChange={() => {}}
        onRunChange={onRunChange}
      />,
    )

    await waitFor(() => {
      const opts = screen.getAllByRole('option', { name: /gpt-4o/ })
      expect(opts.length).toBeGreaterThan(0)
    })

    await user.selectOptions(screen.getByLabelText('Run'), 'run-x')

    expect(onRunChange).toHaveBeenCalledWith('run-x')
  })

  it('renders disabled when disabled prop is true', async () => {
    mockListExperiments.mockResolvedValue([makeExperiment('exp-1')] as Experiment[])

    render(
      <RunPicker
        label="Run A"
        selectedExperiment="exp-1"
        selectedRun=""
        onExperimentChange={() => {}}
        onRunChange={() => {}}
        disabled
      />,
    )

    expect(screen.getByLabelText('Experiment')).toBeDisabled()
    expect(screen.getByLabelText('Run')).toBeDisabled()
  })
})
