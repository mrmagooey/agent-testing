import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  listExperiments,
  getExperiment,
  submitExperiment,
  searchFindings,
  getFileContent,
  estimateExperiment,
  downloadReports,
  cancelExperiment,
  listAvailableModelIds,
  listStrategies,
  listProfiles,
  listDatasets,
  getTrends,
  type Experiment,
  type ExperimentConfig,
  type TrendResponse,
} from '../../api/client'

// ─── Helpers ────────────────────────────────────────────────────────────────

function makeFetchResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response
}

// ─── Setup ──────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.restoreAllMocks()
})

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('apiFetch helper behaviour', () => {
  it('returns parsed JSON on a successful 200 response', async () => {
    const payload = { experiment_id: 'e1', status: 'completed' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeFetchResponse(payload, 200)))

    const result = await getExperiment('e1')
    expect(result).toEqual(payload)
  })

  it('throws an error on a 404 response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: vi.fn().mockResolvedValue({ detail: 'Experiment not found' }),
      } as unknown as Response),
    )

    await expect(getExperiment('missing')).rejects.toThrow('Experiment not found')
  })

  it('handles 204 No Content by returning undefined without calling .json()', async () => {
    const jsonFn = vi.fn()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 204,
        json: jsonFn,
      } as unknown as Response),
    )

    const result = await cancelExperiment('e1')
    expect(result).toBeUndefined()
    expect(jsonFn).not.toHaveBeenCalled()
  })

  it('uses the fallback error message when response body has no detail or message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: vi.fn().mockResolvedValue({}),
      } as unknown as Response),
    )

    await expect(getExperiment('e1')).rejects.toThrow('API error 500')
  })
})

describe('listExperiments', () => {
  it('calls GET /api/experiments and returns the array', async () => {
    const experiments: Partial<Experiment>[] = [{ experiment_id: 'e1' }, { experiment_id: 'e2' }]
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(experiments))
    vi.stubGlobal('fetch', fetchMock)

    const result = await listExperiments()

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/experiments')
    expect(init?.method).toBeUndefined() // default GET has no explicit method
    expect(result).toEqual(experiments)
  })
})

describe('getExperiment', () => {
  it('calls GET /api/experiments/:id with the correct path', async () => {
    const experiment: Partial<Experiment> = { experiment_id: 'abc-123', status: 'running' }
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(experiment))
    vi.stubGlobal('fetch', fetchMock)

    await getExperiment('abc-123')

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe('/api/experiments/abc-123')
  })
})

describe('submitExperiment', () => {
  it('sends POST /api/experiments with the serialised config body', async () => {
    const config: ExperimentConfig = {
      dataset: 'ds1',
      models: ['gpt-4'],
      strategies: ['basic'],
      profiles: ['default'],
      tool_variants: ['none'],
      verification: ['none'],
      repetitions: 1,
    }
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse({ experiment_id: 'new' }))
    vi.stubGlobal('fetch', fetchMock)

    await submitExperiment(config)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/experiments')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(config)
  })
})

describe('searchFindings', () => {
  it('encodes the query parameter in the URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await searchFindings('e1', 'SQL injection & XSS')

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('/api/experiments/e1/findings/search?q=')
    expect(url).toContain(encodeURIComponent('SQL injection & XSS'))
  })
})

describe('getFileContent', () => {
  it('encodes both the dataset name and file path in the URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeFetchResponse({ content: 'print("hello")', language: 'python' }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await getFileContent('my dataset', 'src/main.py')

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain(encodeURIComponent('my dataset'))
    expect(url).toContain(encodeURIComponent('src/main.py'))
  })
})

describe('estimateExperiment', () => {
  it('sends POST /api/experiments/estimate with partial config', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeFetchResponse({ total_runs: 20, estimated_cost_usd: 1.5, by_model: {} }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await estimateExperiment({ models: ['claude-3-5-sonnet'], repetitions: 2 })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/experiments/estimate')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toMatchObject({ models: ['claude-3-5-sonnet'], repetitions: 2 })
  })
})

describe('getTrends', () => {
  const mockResponse: TrendResponse = {
    dataset: 'test-ds',
    experiments: [{ experiment_id: 'b1', completed_at: '2026-01-01T10:00:00' }],
    series: [],
  }

  it('serializes dataset param correctly', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(mockResponse))
    vi.stubGlobal('fetch', fetchMock)

    await getTrends('test-ds')

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('/api/trends')
    expect(url).toContain('dataset=test-ds')
  })

  it('serializes optional limit param', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(mockResponse))
    vi.stubGlobal('fetch', fetchMock)

    await getTrends('test-ds', { limit: 20 })

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('limit=20')
  })

  it('serializes tool_ext param when provided', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(mockResponse))
    vi.stubGlobal('fetch', fetchMock)

    await getTrends('test-ds', { tool_ext: 'tree_sitter' })

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('tool_ext=tree_sitter')
  })

  it('does not include tool_ext when empty string', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(mockResponse))
    vi.stubGlobal('fetch', fetchMock)

    await getTrends('test-ds', { tool_ext: '' })

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).not.toContain('tool_ext')
  })

  it('includes since and until when provided', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(mockResponse))
    vi.stubGlobal('fetch', fetchMock)

    await getTrends('test-ds', { since: '2026-01-01', until: '2026-12-31' })

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('since=2026-01-01')
    expect(url).toContain('until=2026-12-31')
  })

  it('throws on 400 error from server (dataset required)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        json: vi.fn().mockResolvedValue({ detail: 'dataset query parameter is required' }),
      } as unknown as Response),
    )

    await expect(getTrends('')).rejects.toThrow('dataset query parameter is required')
  })
})

describe('downloadReports', () => {
  it('returns the correct download URL string without making a fetch call', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const url = downloadReports('experiment-xyz')

    expect(url).toBe('/api/experiments/experiment-xyz/results/download')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

// Regression: the coordinator's /models, /strategies, /profiles endpoints
// return `list[dict]` (objects with `id`/`name`/etc.), not the plain `list[str]`
// that the frontend historically assumed. Rendering those objects as React
// children triggers React error #31 on /experiments/new. The client must
// normalize them to plain string IDs so the UI never sees a raw object.
describe('config endpoint normalization (regression for React error #31)', () => {
  it('listAvailableModelIds returns only available model ids from grouped response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeFetchResponse([
        {
          provider: 'openai',
          probe_status: 'fresh',
          models: [
            { id: 'gpt-4o', display_name: 'GPT-4o', status: 'available' },
            { id: 'gpt-4o-mini', display_name: 'GPT-4o Mini', status: 'key_missing' },
          ],
        },
        {
          provider: 'anthropic',
          probe_status: 'fresh',
          models: [
            { id: 'claude-3-5-sonnet-20241022', display_name: 'Claude 3.5 Sonnet', status: 'available' },
          ],
        },
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const models = await listAvailableModelIds()

    expect(models).toEqual(['gpt-4o', 'claude-3-5-sonnet-20241022'])
    for (const m of models) expect(typeof m).toBe('string')
  })

  it('listStrategies flattens {name, description} objects to the name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeFetchResponse([
        { name: 'zero_shot', description: 'zero_shot scan strategy' },
        { name: 'chain_of_thought', description: '…' },
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const strategies = await listStrategies()

    expect(strategies).toEqual(['zero_shot', 'chain_of_thought'])
    for (const s of strategies) expect(typeof s).toBe('string')
  })

  it('listProfiles flattens {name, description} objects to the name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeFetchResponse([
        { name: 'default', description: 'default profile' },
        { name: 'strict', description: 'strict profile' },
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const profiles = await listProfiles()

    expect(profiles).toEqual(['default', 'strict'])
    for (const p of profiles) expect(typeof p).toBe('string')
  })

  it('listProfiles drops entries without a usable string identifier', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeFetchResponse([
        { name: 'default', description: 'ok' },
        { description: 'no name' },
        null,
        42,
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    expect(await listProfiles()).toEqual(['default'])
  })

  it('listDatasets defaults missing languages to an empty array so .join is safe', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeFetchResponse([{ name: 'cve-2024-python', label_count: 4 }]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const [ds] = await listDatasets()

    expect(ds.name).toBe('cve-2024-python')
    expect(ds.label_count).toBe(4)
    expect(Array.isArray(ds.languages)).toBe(true)
    // Must not throw: prior bug was `undefined.join('/')` in the <option> label.
    expect(ds.languages.join('/')).toBe('')
  })
})
