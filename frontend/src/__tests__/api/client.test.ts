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
