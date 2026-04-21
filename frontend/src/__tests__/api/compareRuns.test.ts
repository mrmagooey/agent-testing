import { describe, it, expect, vi, beforeEach } from 'vitest'
import { compareRuns, compareRunsCross, type RunComparison } from '../../api/client'

function makeFetchResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response
}

beforeEach(() => {
  vi.restoreAllMocks()
})

const MINIMAL_COMPARISON: RunComparison = {
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
  },
  run_b: {
    run_id: 'run-b',
    experiment_id: 'exp-2',
    experiment_name: 'Experiment 2',
    dataset: 'ds-beta',
    model: 'gpt-4o',
    strategy: 'zero_shot',
    tool_variant: 'without_tools',
    profile: 'default',
    verification: 'none',
    status: 'completed',
  },
  found_by_both: [],
  only_in_a: [],
  only_in_b: [],
  dataset_mismatch: true,
  warnings: ['Datasets differ: ds-alpha vs ds-beta'],
}

describe('compareRunsCross', () => {
  it('hits /api/compare-runs with correct query params', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(MINIMAL_COMPARISON))
    vi.stubGlobal('fetch', fetchMock)

    await compareRunsCross({
      aExperiment: 'exp-1',
      aRun: 'run-a',
      bExperiment: 'exp-2',
      bRun: 'run-b',
    })

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('/api/compare-runs')
    expect(url).toContain('a_experiment=exp-1')
    expect(url).toContain('a_run=run-a')
    expect(url).toContain('b_experiment=exp-2')
    expect(url).toContain('b_run=run-b')
  })

  it('returns the comparison response including dataset_mismatch and warnings', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(MINIMAL_COMPARISON))
    vi.stubGlobal('fetch', fetchMock)

    const result = await compareRunsCross({
      aExperiment: 'exp-1',
      aRun: 'run-a',
      bExperiment: 'exp-2',
      bRun: 'run-b',
    })

    expect(result.dataset_mismatch).toBe(true)
    expect(result.warnings).toHaveLength(1)
    expect(result.run_a.experiment_id).toBe('exp-1')
    expect(result.run_b.experiment_id).toBe('exp-2')
  })

  it('URL-encodes special characters in experiment and run IDs', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(MINIMAL_COMPARISON))
    vi.stubGlobal('fetch', fetchMock)

    await compareRunsCross({
      aExperiment: 'exp a+1',
      aRun: 'run/a',
      bExperiment: 'exp b+2',
      bRun: 'run/b',
    })

    const [url] = fetchMock.mock.calls[0] as [string]
    // URLSearchParams encodes spaces as + and slashes as %2F — the URL must not
    // contain raw spaces or unencoded slashes in the query portion.
    expect(url).not.toContain(' ')
    // Slashes in values are percent-encoded by URLSearchParams
    expect(url).toContain('run%2Fa')
  })
})

describe('compareRuns (legacy shim)', () => {
  it('delegates to compareRunsCross with same experiment on both sides', async () => {
    const sameExpComparison: RunComparison = {
      ...MINIMAL_COMPARISON,
      run_a: { ...MINIMAL_COMPARISON.run_a, experiment_id: 'exp-same' },
      run_b: { ...MINIMAL_COMPARISON.run_b, experiment_id: 'exp-same' },
      dataset_mismatch: false,
      warnings: [],
    }
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(sameExpComparison))
    vi.stubGlobal('fetch', fetchMock)

    await compareRuns('exp-same', 'run-a', 'run-b')

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('/api/compare-runs')
    expect(url).toContain('a_experiment=exp-same')
    expect(url).toContain('b_experiment=exp-same')
    expect(url).toContain('a_run=run-a')
    expect(url).toContain('b_run=run-b')
  })
})
