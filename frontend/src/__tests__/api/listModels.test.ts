import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  listModels,
  listAvailableModelIds,
  parseUnavailableModelsError,
  ApiError,
  type ModelProviderGroup,
} from '../../api/client'

// ─── Helpers ────────────────────────────────────────────────────────────────

function makeFetchResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response
}

// Phase 2 grouped fixture
const GROUPED_RESPONSE: ModelProviderGroup[] = [
  {
    provider: 'openai',
    probe_status: 'fresh',
    fetched_at: '2026-04-23T14:05:23Z',
    last_error: null,
    models: [
      { id: 'gpt-4o', display_name: 'GPT-4o', status: 'available', context_length: 128000, region: null },
      { id: 'gpt-4o-mini', display_name: 'GPT-4o Mini', status: 'key_missing', context_length: 128000, region: null },
    ],
  },
  {
    provider: 'bedrock',
    probe_status: 'disabled',
    fetched_at: null,
    last_error: null,
    models: [
      { id: 'bedrock-claude-3-5-sonnet', display_name: 'Claude 3.5 Sonnet (Bedrock)', status: 'key_missing', region: 'us-east-1' },
    ],
  },
  {
    provider: 'anthropic',
    probe_status: 'fresh',
    fetched_at: '2026-04-23T14:00:00Z',
    last_error: null,
    models: [
      { id: 'claude-3-5-sonnet-20241022', display_name: 'Claude 3.5 Sonnet', status: 'available', context_length: 200000, region: null },
      { id: 'claude-3-opus-20240229', display_name: 'Claude 3 Opus', status: 'not_listed', context_length: 200000, region: null },
    ],
  },
]

// ─── Setup ──────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.restoreAllMocks()
})

// ─── listModels ───────────────────────────────────────────────────────────────

describe('listModels()', () => {
  it('returns the Phase 2 grouped shape as-is', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeFetchResponse(GROUPED_RESPONSE)))

    const result = await listModels()

    expect(result).toEqual(GROUPED_RESPONSE)
    expect(result).toHaveLength(3)
    expect(result[0].provider).toBe('openai')
    expect(result[0].probe_status).toBe('fresh')
    expect(result[0].models).toHaveLength(2)
    expect(result[0].models[0].id).toBe('gpt-4o')
    expect(result[0].models[0].status).toBe('available')
  })

  it('calls GET /api/models', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeFetchResponse(GROUPED_RESPONSE))
    vi.stubGlobal('fetch', fetchMock)

    await listModels()

    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe('/api/models')
  })
})

// ─── listAvailableModelIds ────────────────────────────────────────────────────

describe('listAvailableModelIds()', () => {
  it('returns only ids of models with status === "available", preserving order across groups', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeFetchResponse(GROUPED_RESPONSE)))

    const ids = await listAvailableModelIds()

    // gpt-4o-mini is key_missing, bedrock-claude-3-5-sonnet is key_missing,
    // claude-3-opus is not_listed — only the two available ones come through
    expect(ids).toEqual(['gpt-4o', 'claude-3-5-sonnet-20241022'])
  })

  it('returns empty array when all models are unavailable', async () => {
    const allUnavailable: ModelProviderGroup[] = [
      {
        provider: 'openai',
        probe_status: 'failed',
        fetched_at: null,
        last_error: 'timeout',
        models: [
          { id: 'gpt-4o', display_name: 'GPT-4o', status: 'probe_failed' },
        ],
      },
    ]
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeFetchResponse(allUnavailable)))

    const ids = await listAvailableModelIds()

    expect(ids).toEqual([])
  })

  it('returns empty array when there are no groups', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeFetchResponse([])))

    const ids = await listAvailableModelIds()

    expect(ids).toEqual([])
  })
})

// ─── parseUnavailableModelsError ─────────────────────────────────────────────

describe('parseUnavailableModelsError()', () => {
  it('returns the structured payload from an ApiError with the Phase 2 error body', () => {
    const detail = {
      error: 'unavailable_models',
      models: [
        { id: 'gpt-4o', status: 'key_missing', reason: 'API key not configured' },
        { id: 'bedrock-claude-3-5-sonnet', status: 'key_missing', reason: 'AWS credentials missing' },
      ],
    }
    const err = new ApiError('API error 400', 400, { detail })

    const result = parseUnavailableModelsError(err)

    expect(result).not.toBeNull()
    expect(result!.error).toBe('unavailable_models')
    expect(result!.models).toHaveLength(2)
    expect(result!.models[0].id).toBe('gpt-4o')
    expect(result!.models[0].status).toBe('key_missing')
    expect(result!.models[0].reason).toBe('API key not configured')
  })

  it('returns null for a plain Error (not ApiError)', () => {
    const err = new Error('something went wrong')

    expect(parseUnavailableModelsError(err)).toBeNull()
  })

  it('returns null for non-error values', () => {
    expect(parseUnavailableModelsError(null)).toBeNull()
    expect(parseUnavailableModelsError(undefined)).toBeNull()
    expect(parseUnavailableModelsError('string error')).toBeNull()
    expect(parseUnavailableModelsError(42)).toBeNull()
  })

  it('returns null when ApiError body has a different error type', () => {
    const detail = { error: 'validation_error', message: 'something else' }
    const err = new ApiError('API error 400', 400, { detail })

    expect(parseUnavailableModelsError(err)).toBeNull()
  })

  it('returns null when ApiError body has no detail', () => {
    const err = new ApiError('API error 500', 500, { message: 'internal error' })

    expect(parseUnavailableModelsError(err)).toBeNull()
  })

  it('integrates with a failed submitExperiment call (simulated fetch)', async () => {
    const detail = {
      error: 'unavailable_models',
      models: [{ id: 'gpt-4o', status: 'key_missing' }],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        json: vi.fn().mockResolvedValue({ detail }),
      } as unknown as Response),
    )

    let caught: unknown
    try {
      const { submitExperiment } = await import('../../api/client')
      await submitExperiment({ dataset: 'ds', models: ['gpt-4o'], strategies: [], profiles: [], tool_variants: [], verification: [], repetitions: 1 })
    } catch (e) {
      caught = e
    }

    expect(caught).toBeInstanceOf(ApiError)
    const parsed = parseUnavailableModelsError(caught)
    expect(parsed).not.toBeNull()
    expect(parsed!.error).toBe('unavailable_models')
    expect(parsed!.models[0].id).toBe('gpt-4o')
  })
})
