import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useExperiment } from '../../hooks/useExperiment'

// ─── Mock the API client ─────────────────────────────────────────────────────

vi.mock('../../api/client', () => ({
  getExperiment: vi.fn(),
}))

import { getExperiment, type Experiment } from '../../api/client'
const mockGetExperiment = vi.mocked(getExperiment)

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeExperiment(overrides: Partial<Experiment> = {}): Experiment {
  return {
    experiment_id: 'e1',
    status: 'running',
    dataset: 'ds1',
    created_at: '2026-01-01T00:00:00Z',
    total_runs: 10,
    completed_runs: 3,
    running_runs: 2,
    pending_runs: 5,
    failed_runs: 0,
    total_cost_usd: 0.5,
    ...overrides,
  }
}

// ─── Setup ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('useExperiment', () => {
  it('starts with loading=true and experiment=null', () => {
    // Never resolve — we just check initial state
    mockGetExperiment.mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useExperiment('e1'))

    expect(result.current.loading).toBe(true)
    expect(result.current.experiment).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('populates experiment data after a successful fetch', async () => {
    const experiment = makeExperiment({ status: 'completed' })
    mockGetExperiment.mockResolvedValue(experiment)

    const { result } = renderHook(() => useExperiment('e1'))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.experiment).toEqual(experiment)
    expect(result.current.error).toBeNull()
  })

  it('sets error when getExperiment rejects', async () => {
    mockGetExperiment.mockRejectedValue(new Error('Network failure'))

    const { result } = renderHook(() => useExperiment('e1'))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.error).toBe('Network failure')
    expect(result.current.experiment).toBeNull()
  })

  it('calls getExperiment with the provided experimentId', async () => {
    mockGetExperiment.mockResolvedValue(makeExperiment({ status: 'completed' }))

    renderHook(() => useExperiment('my-experiment-123'))

    await waitFor(() => {
      expect(mockGetExperiment).toHaveBeenCalledWith('my-experiment-123')
    })
  })

  it('does not fetch when experimentId is undefined', async () => {
    const { result } = renderHook(() => useExperiment(undefined))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(mockGetExperiment).not.toHaveBeenCalled()
  })
})
