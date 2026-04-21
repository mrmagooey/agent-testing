import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useEstimate } from '../../hooks/useEstimate'

// ─── Mock the API client ─────────────────────────────────────────────────────

vi.mock('../../api/client', () => ({
  estimateExperiment: vi.fn(),
}))

import { estimateExperiment } from '../../api/client'
import type { CostEstimate, ExperimentConfig } from '../../api/client'
const mockEstimate = vi.mocked(estimateExperiment)

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeEstimate(overrides: Partial<CostEstimate> = {}): CostEstimate {
  return {
    total_runs: 20,
    estimated_cost_usd: 4.5,
    by_model: { 'gpt-4o': 2.5 },
    ...overrides,
  }
}

function makeConfig(overrides: Partial<ExperimentConfig> = {}): Partial<ExperimentConfig> {
  return {
    models: ['gpt-4o'],
    strategies: ['single_agent'],
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

// ─── Tests (real timers — DEBOUNCE_MS=300ms is tolerable in CI) ──────────────

describe('useEstimate', () => {
  it('starts with loading=false and estimate=null when no models/strategies', () => {
    const { result } = renderHook(() => useEstimate({}))
    expect(result.current.loading).toBe(false)
    expect(result.current.estimate).toBeNull()
  })

  it('does not fetch when config has no models or strategies', async () => {
    renderHook(() => useEstimate({}))
    await new Promise((r) => setTimeout(r, 400))
    expect(mockEstimate).not.toHaveBeenCalled()
  }, 10000)

  it('fetches when only models is provided (strategies empty)', async () => {
    // Guard: !hasModels && !hasStrategies — only skips when BOTH absent.
    // models=['gpt-4o'] + strategies=[] → hasModels=true → will fetch.
    mockEstimate.mockResolvedValue(makeEstimate())
    const { result } = renderHook(() => useEstimate({ models: ['gpt-4o'], strategies: [] }))
    await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 2000 })
    expect(mockEstimate).toHaveBeenCalledOnce()
  }, 10000)

  it('fetches when only strategies is provided (models empty)', async () => {
    // models=[] + strategies=['single_agent'] → hasStrategies=true → will fetch.
    mockEstimate.mockResolvedValue(makeEstimate())
    const { result } = renderHook(() => useEstimate({ models: [], strategies: ['single_agent'] }))
    await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 2000 })
    expect(mockEstimate).toHaveBeenCalledOnce()
  }, 10000)

  it('sets loading=true while debounce is pending', () => {
    mockEstimate.mockReturnValue(new Promise(() => {}))
    const { result } = renderHook(() => useEstimate(makeConfig()))
    // After triggering setLoading(true) but before debounce fires
    expect(result.current.loading).toBe(true)
  })

  it('fetches estimate after debounce fires', async () => {
    const estimate = makeEstimate()
    mockEstimate.mockResolvedValue(estimate)

    const { result } = renderHook(() => useEstimate(makeConfig()))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    }, { timeout: 2000 })

    expect(mockEstimate).toHaveBeenCalledOnce()
    expect(result.current.estimate).toEqual(estimate)
  }, 10000)

  it('sets estimate to null when API throws', async () => {
    mockEstimate.mockRejectedValue(new Error('API down'))

    const { result } = renderHook(() => useEstimate(makeConfig()))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    }, { timeout: 2000 })

    expect(result.current.estimate).toBeNull()
  }, 10000)

  it('loading is false after successful fetch', async () => {
    mockEstimate.mockResolvedValue(makeEstimate())

    const { result } = renderHook(() => useEstimate(makeConfig()))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    }, { timeout: 2000 })
  }, 10000)

  it('loading is false after failed fetch', async () => {
    mockEstimate.mockRejectedValue(new Error('Network error'))

    const { result } = renderHook(() => useEstimate(makeConfig()))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    }, { timeout: 2000 })
  }, 10000)

  it('re-fetches when config changes', async () => {
    mockEstimate.mockResolvedValue(makeEstimate())

    const { result, rerender } = renderHook(
      ({ config }: { config: Partial<ExperimentConfig> }) => useEstimate(config),
      { initialProps: { config: makeConfig({ models: ['gpt-4o'] }) } }
    )

    await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 2000 })

    // Change config
    rerender({ config: makeConfig({ models: ['claude-3-5-sonnet'] }) })
    await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 2000 })

    expect(mockEstimate).toHaveBeenCalledTimes(2)
  }, 15000)

  it('does not re-fetch when config reference changes but content is the same', async () => {
    mockEstimate.mockResolvedValue(makeEstimate())

    const config = makeConfig()
    const { result, rerender } = renderHook(
      ({ c }: { c: Partial<ExperimentConfig> }) => useEstimate(c),
      { initialProps: { c: config } }
    )

    await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 2000 })

    // Same content, new object reference
    rerender({ c: { ...config } })
    await new Promise((r) => setTimeout(r, 400))
    await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 2000 })

    // Should not refetch because JSON.stringify is the same
    expect(mockEstimate).toHaveBeenCalledTimes(1)
  }, 15000)
})
