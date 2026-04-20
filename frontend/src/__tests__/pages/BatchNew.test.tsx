import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import BatchNew, { generatePowerSet } from '../../pages/BatchNew'

// ─── Regression: React error #31 on /batches/new ─────────────────────────────
//
// The coordinator's /models, /strategies, /profiles endpoints return
// `list[dict]` (e.g. `{"name": "default", "description": "..."}`).
// The frontend previously typed these as `string[]` and dropped the items
// straight into JSX (`<span>{p}</span>` inside the Profile radio list),
// which triggers React error #31: "Objects are not valid as a React child."
//
// The e2e Playwright mocks happen to return real `string[]` for these
// endpoints, so the bug only reproduces against the live coordinator.
// This test simulates the real coordinator's object-shape responses and
// asserts the page renders without throwing.

function makeResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response
}

function mockRealCoordinatorFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    // Object-shape responses matching coordinator.py list_models/strategies/profiles
    if (url.endsWith('/api/models')) {
      return makeResponse([
        { id: 'gpt-4o', provider: 'openai' },
        { id: 'claude-3-5-sonnet-20241022', provider: 'anthropic' },
      ])
    }
    if (url.endsWith('/api/strategies')) {
      return makeResponse([
        { name: 'zero_shot', description: 'zero_shot scan strategy' },
        { name: 'chain_of_thought', description: 'chain_of_thought scan strategy' },
      ])
    }
    if (url.endsWith('/api/profiles')) {
      return makeResponse([
        { name: 'default', description: 'Default review profile.' },
        { name: 'strict', description: 'Strict review profile.' },
      ])
    }
    if (url.endsWith('/api/datasets')) {
      // Coordinator omits `languages` — must not crash the <option> renderer.
      return makeResponse([{ name: 'cve-2024-python', label_count: 4 }])
    }
    if (url.endsWith('/api/tool-extensions')) {
      return makeResponse([
        { key: 'tree_sitter', label: 'Tree-sitter', available: true },
        { key: 'lsp', label: 'LSP', available: true },
        { key: 'devdocs', label: 'DevDocs', available: false },
      ])
    }
    return makeResponse({}, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('BatchNew page rendering (React #31 regression)', () => {
  beforeEach(() => {
    mockRealCoordinatorFetch()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders /batches/new without throwing when the coordinator returns object-shape config payloads', async () => {
    // Capture console.error so we can detect React's #31 warning even in
    // environments where it would otherwise be swallowed.
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <MemoryRouter initialEntries={['/batches/new']}>
        <BatchNew />
      </MemoryRouter>,
    )

    // Wait for the page to finish loading (Loading… disappears once Promise.all resolves).
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'New Batch' })).toBeVisible()
    })

    // The profile radio list must show the profile names (not [object Object]).
    expect(screen.getByText('default')).toBeVisible()
    expect(screen.getByText('strict')).toBeVisible()

    // No React "Objects are not valid as a React child" error should have been logged.
    const reactObjectErrors = errorSpy.mock.calls.filter((call) => {
      const msg = call.map(String).join(' ')
      return (
        msg.includes('Objects are not valid as a React child') ||
        msg.includes('Minified React error #31')
      )
    })
    expect(reactObjectErrors).toEqual([])
  })
})

describe('generatePowerSet', () => {
  it('generates empty set when given empty array', () => {
    const result = generatePowerSet([])
    expect(result).toEqual([[]])
  })

  it('generates power set for single element', () => {
    const result = generatePowerSet(['a'])
    expect(result).toEqual([[], ['a']])
  })

  it('generates power set for two elements', () => {
    const result = generatePowerSet(['a', 'b'])
    expect(result).toHaveLength(4)
    expect(result).toContainEqual([])
    expect(result).toContainEqual(['a'])
    expect(result).toContainEqual(['b'])
    expect(result).toContainEqual(['a', 'b'])
  })

  it('generates power set for three elements (2^3 = 8 subsets)', () => {
    const result = generatePowerSet(['lsp', 'tree_sitter', 'devdocs'])
    expect(result).toHaveLength(8)
    expect(result).toContainEqual([])
    expect(result).toContainEqual(['lsp'])
    expect(result).toContainEqual(['tree_sitter'])
    expect(result).toContainEqual(['devdocs'])
    expect(result).toContainEqual(['lsp', 'tree_sitter'])
    expect(result).toContainEqual(['lsp', 'devdocs'])
    expect(result).toContainEqual(['tree_sitter', 'devdocs'])
    expect(result).toContainEqual(['lsp', 'tree_sitter', 'devdocs'])
  })

  it('generates correct power set length for n elements', () => {
    const testCases = [
      { n: 1, expected: 2 },
      { n: 2, expected: 4 },
      { n: 3, expected: 8 },
      { n: 4, expected: 16 },
      { n: 5, expected: 32 },
    ]

    testCases.forEach(({ n, expected }) => {
      const items = Array.from({ length: n }, (_, i) => `item${i}`)
      const result = generatePowerSet(items)
      expect(result).toHaveLength(expected)
    })
  })

  it('preserves order of elements within subsets', () => {
    const result = generatePowerSet(['a', 'b', 'c'])
    // Check that 'a' always comes before 'b' if both are present
    const withBoth = result.filter((s) => s.includes('a') && s.includes('b'))
    withBoth.forEach((subset) => {
      const aIdx = subset.indexOf('a')
      const bIdx = subset.indexOf('b')
      expect(aIdx).toBeLessThan(bIdx)
    })
  })
})
