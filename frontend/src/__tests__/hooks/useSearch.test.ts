import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useSearch } from '../../hooks/useSearch'

// ─── Mock the API client ─────────────────────────────────────────────────────

vi.mock('../../api/client', () => ({
  searchFindings: vi.fn(),
}))

import { searchFindings } from '../../api/client'
import type { Finding } from '../../api/client'
const mockSearch = vi.mocked(searchFindings)

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeFinding(id: string): Finding {
  return {
    finding_id: id,
    run_id: 'r1',
    batch_id: 'b1',
    title: `Finding ${id}`,
    description: 'A vulnerability',
    vuln_class: 'sqli',
    severity: 'high',
    match_status: 'tp',
  }
}

// ─── Setup ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ─── Tests (real timers, DEBOUNCE_MS=300 is tolerable) ───────────────────────

describe('useSearch', () => {
  it('starts with empty results and loading=false', () => {
    const { result } = renderHook(() => useSearch('b1'))
    expect(result.current.results).toEqual([])
    expect(result.current.loading).toBe(false)
  })

  it('exposes a search function', () => {
    const { result } = renderHook(() => useSearch('b1'))
    expect(typeof result.current.search).toBe('function')
  })

  it('does not call API for empty string', async () => {
    const { result } = renderHook(() => useSearch('b1'))

    act(() => {
      result.current.search('')
    })

    await new Promise((r) => setTimeout(r, 400))
    expect(mockSearch).not.toHaveBeenCalled()
    expect(result.current.results).toEqual([])
    expect(result.current.loading).toBe(false)
  }, 10000)

  it('does not call API for whitespace-only query', async () => {
    const { result } = renderHook(() => useSearch('b1'))

    act(() => {
      result.current.search('   ')
    })

    await new Promise((r) => setTimeout(r, 400))
    expect(mockSearch).not.toHaveBeenCalled()
  }, 10000)

  it('sets loading=true immediately when search is called with non-empty query', () => {
    mockSearch.mockReturnValue(new Promise(() => {}))
    const { result } = renderHook(() => useSearch('b1'))

    act(() => {
      result.current.search('sqli')
    })

    expect(result.current.loading).toBe(true)
  })

  it('debounces: does not call API immediately', () => {
    mockSearch.mockResolvedValue([])
    const { result } = renderHook(() => useSearch('b1'))

    act(() => {
      result.current.search('sqli')
    })

    // Timer hasn't fired yet
    expect(mockSearch).not.toHaveBeenCalled()
  })

  it('calls searchFindings with correct batchId and query after debounce', async () => {
    mockSearch.mockResolvedValue([])
    const { result } = renderHook(() => useSearch('my-batch'))

    act(() => {
      result.current.search('injection')
    })

    await waitFor(() => {
      expect(mockSearch).toHaveBeenCalledWith('my-batch', 'injection')
    }, { timeout: 2000 })
  }, 10000)

  it('populates results after successful search', async () => {
    const findings = [makeFinding('f1'), makeFinding('f2')]
    mockSearch.mockResolvedValue(findings)

    const { result } = renderHook(() => useSearch('b1'))

    act(() => {
      result.current.search('sql')
    })

    await waitFor(() => {
      expect(result.current.results).toEqual(findings)
    }, { timeout: 2000 })
  }, 10000)

  it('loading is false after successful search completes', async () => {
    mockSearch.mockResolvedValue([makeFinding('f1')])

    const { result } = renderHook(() => useSearch('b1'))

    act(() => {
      result.current.search('xss')
    })

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    }, { timeout: 2000 })
  }, 10000)

  it('resets results to empty array on API error', async () => {
    mockSearch.mockRejectedValue(new Error('Network error'))

    const { result } = renderHook(() => useSearch('b1'))

    act(() => {
      result.current.search('error')
    })

    await waitFor(() => {
      expect(result.current.results).toEqual([])
      expect(result.current.loading).toBe(false)
    }, { timeout: 2000 })
  }, 10000)

  it('loading is false after error', async () => {
    mockSearch.mockRejectedValue(new Error('API down'))

    const { result } = renderHook(() => useSearch('b1'))

    act(() => {
      result.current.search('test')
    })

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    }, { timeout: 2000 })
  }, 10000)

  it('cancels previous debounce when search is called rapidly', async () => {
    mockSearch.mockResolvedValue([])
    const { result } = renderHook(() => useSearch('b1'))

    // Call search multiple times rapidly (simulating user typing)
    act(() => {
      result.current.search('s')
      result.current.search('sq')
      result.current.search('sql')
    })

    await waitFor(() => {
      expect(mockSearch).toHaveBeenCalledTimes(1)
    }, { timeout: 2000 })

    // Should only be called with the last query
    expect(mockSearch).toHaveBeenCalledWith('b1', 'sql')
  }, 10000)

  it('loading returns to false when empty query clears the debounce', async () => {
    const { result } = renderHook(() => useSearch('b1'))

    act(() => {
      result.current.search('sql')   // loading = true
      result.current.search('')      // clears timer, loading = false
    })

    expect(result.current.loading).toBe(false)
  })
})
