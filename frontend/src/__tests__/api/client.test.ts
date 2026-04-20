import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  listBatches,
  getBatch,
  submitBatch,
  searchFindings,
  getFileContent,
  estimateBatch,
  downloadReports,
  cancelBatch,
  listModels,
  listStrategies,
  listProfiles,
  listDatasets,
  type Batch,
  type BatchConfig,
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
    const payload = { batch_id: 'b1', status: 'completed' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeFetchResponse(payload, 200)))

    const result = await getBatch('b1')
    expect(result).toEqual(payload)
  })

  it('throws an error on a 404 response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: vi.fn().mockResolvedValue({ detail: 'Batch not found' }),
      } as unknown as Response),
    )

    await expect(getBatch('missing')).rejects.toThrow('Batch not found')
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

    const result = await cancelBatch('b1')
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

    await expect(getBatch('b1')).rejects.toThrow('API error 500')
  })
})

describe('listBatches', () => {
  it('calls GET /api/batches and returns the array', async () => {
    const batches: Partial<Batch>[] = [{ batch_id: 'b1' }, { batch_id: 'b2' }]
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(batches))
    vi.stubGlobal('fetch', fetchMock)

    const result = await listBatches()

    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/batches')
    expect(init?.method).toBeUndefined() // default GET has no explicit method
    expect(result).toEqual(batches)
  })
})

describe('getBatch', () => {
  it('calls GET /api/batches/:id with the correct path', async () => {
    const batch: Partial<Batch> = { batch_id: 'abc-123', status: 'running' }
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(batch))
    vi.stubGlobal('fetch', fetchMock)

    await getBatch('abc-123')

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe('/api/batches/abc-123')
  })
})

describe('submitBatch', () => {
  it('sends POST /api/batches with the serialised config body', async () => {
    const config: BatchConfig = {
      dataset: 'ds1',
      models: ['gpt-4'],
      strategies: ['basic'],
      profiles: ['default'],
      tool_variants: ['none'],
      verification: ['none'],
      repetitions: 1,
    }
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse({ batch_id: 'new' }))
    vi.stubGlobal('fetch', fetchMock)

    await submitBatch(config)

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/batches')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(config)
  })
})

describe('searchFindings', () => {
  it('encodes the query parameter in the URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    await searchFindings('b1', 'SQL injection & XSS')

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('/api/batches/b1/findings/search?q=')
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

describe('estimateBatch', () => {
  it('sends POST /api/batches/estimate with partial config', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeFetchResponse({ total_runs: 20, estimated_cost_usd: 1.5, by_model: {} }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await estimateBatch({ models: ['claude-3-5-sonnet'], repetitions: 2 })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/batches/estimate')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toMatchObject({ models: ['claude-3-5-sonnet'], repetitions: 2 })
  })
})

describe('downloadReports', () => {
  it('returns the correct download URL string without making a fetch call', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    const url = downloadReports('batch-xyz')

    expect(url).toBe('/api/batches/batch-xyz/results/download')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

// Regression: the coordinator's /models, /strategies, /profiles endpoints
// return `list[dict]` (objects with `id`/`name`/etc.), not the plain `list[str]`
// that the frontend historically assumed. Rendering those objects as React
// children triggers React error #31 on /batches/new. The client must
// normalize them to plain string IDs so the UI never sees a raw object.
describe('config endpoint normalization (regression for React error #31)', () => {
  it('listModels flattens object responses to their id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeFetchResponse([
        { id: 'gpt-4o', provider: 'openai', cost_per_1k_input: 0.0025 },
        { id: 'claude-3-5-sonnet-20241022', provider: 'anthropic' },
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const models = await listModels()

    expect(models).toEqual(['gpt-4o', 'claude-3-5-sonnet-20241022'])
    for (const m of models) expect(typeof m).toBe('string')
  })

  it('listModels still accepts legacy string[] responses', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      makeFetchResponse(['gpt-4o', 'gemini-1.5-pro']),
    )
    vi.stubGlobal('fetch', fetchMock)

    expect(await listModels()).toEqual(['gpt-4o', 'gemini-1.5-pro'])
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
