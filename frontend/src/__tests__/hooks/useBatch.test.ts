import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useBatch } from '../../hooks/useBatch'

// ─── Mock the API client ─────────────────────────────────────────────────────

vi.mock('../../api/client', () => ({
  getBatch: vi.fn(),
}))

import { getBatch } from '../../api/client'
const mockGetBatch = vi.mocked(getBatch)

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeBatch(overrides: Record<string, unknown> = {}) {
  return {
    batch_id: 'b1',
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

describe('useBatch', () => {
  it('starts with loading=true and batch=null', () => {
    // Never resolve — we just check initial state
    mockGetBatch.mockReturnValue(new Promise(() => {}))

    const { result } = renderHook(() => useBatch('b1'))

    expect(result.current.loading).toBe(true)
    expect(result.current.batch).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('populates batch data after a successful fetch', async () => {
    const batch = makeBatch({ status: 'completed' })
    mockGetBatch.mockResolvedValue(batch)

    const { result } = renderHook(() => useBatch('b1'))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.batch).toEqual(batch)
    expect(result.current.error).toBeNull()
  })

  it('sets error when getBatch rejects', async () => {
    mockGetBatch.mockRejectedValue(new Error('Network failure'))

    const { result } = renderHook(() => useBatch('b1'))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.error).toBe('Network failure')
    expect(result.current.batch).toBeNull()
  })

  it('calls getBatch with the provided batchId', async () => {
    mockGetBatch.mockResolvedValue(makeBatch({ status: 'completed' }))

    renderHook(() => useBatch('my-batch-123'))

    await waitFor(() => {
      expect(mockGetBatch).toHaveBeenCalledWith('my-batch-123')
    })
  })

  it('does not fetch when batchId is undefined', async () => {
    const { result } = renderHook(() => useBatch(undefined))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(mockGetBatch).not.toHaveBeenCalled()
  })
})
