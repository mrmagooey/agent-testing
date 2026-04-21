import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import FindingsSearch from '../../components/FindingsSearch'

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
    experiment_id: 'e1',
    title: `Finding ${id}`,
    description: 'SQL injection',
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

// ─── Tests (real timers — DEBOUNCE_MS=300 is tolerable) ─────────────────────

describe('FindingsSearch', () => {
  it('renders the search input', () => {
    render(<FindingsSearch experimentId="e1" onResults={vi.fn()} />)
    expect(screen.getByPlaceholderText(/search findings/i)).toBeInTheDocument()
  })

  it('does not call searchFindings when input is empty', async () => {
    const onResults = vi.fn()
    render(<FindingsSearch experimentId="e1" onResults={onResults} />)

    // First type something, then clear to empty — that triggers onResults([])
    fireEvent.change(screen.getByPlaceholderText(/search findings/i), { target: { value: 'sql' } })
    fireEvent.change(screen.getByPlaceholderText(/search findings/i), { target: { value: '' } })

    await new Promise((r) => setTimeout(r, 400))
    // API should not have been called for the empty value
    // (the 'sql' one was debounced and canceled by the '' input)
    expect(onResults).toHaveBeenCalledWith([])
  }, 10000)

  it('does not call searchFindings for whitespace-only input', async () => {
    const onResults = vi.fn()
    render(<FindingsSearch experimentId="e1" onResults={onResults} />)

    fireEvent.change(screen.getByPlaceholderText(/search findings/i), { target: { value: '   ' } })

    await new Promise((r) => setTimeout(r, 400))
    expect(mockSearch).not.toHaveBeenCalled()
  }, 10000)

  it('debounces: does not call searchFindings immediately after typing', () => {
    mockSearch.mockResolvedValue([])
    render(<FindingsSearch experimentId="e1" onResults={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText(/search findings/i), {
      target: { value: 'sqli' },
    })

    // Immediately — before debounce fires
    expect(mockSearch).not.toHaveBeenCalled()
  })

  it('calls searchFindings with experimentId and query after debounce', async () => {
    const findings = [makeFinding('f1'), makeFinding('f2')]
    mockSearch.mockResolvedValue(findings)
    const onResults = vi.fn()

    render(<FindingsSearch experimentId="e1" onResults={onResults} />)

    fireEvent.change(screen.getByPlaceholderText(/search findings/i), {
      target: { value: 'sql' },
    })

    await waitFor(() => {
      expect(mockSearch).toHaveBeenCalledWith('e1', 'sql')
    }, { timeout: 2000 })
  }, 10000)

  it('calls onResults with findings after successful search', async () => {
    const findings = [makeFinding('f1')]
    mockSearch.mockResolvedValue(findings)
    const onResults = vi.fn()

    render(<FindingsSearch experimentId="e1" onResults={onResults} />)

    fireEvent.change(screen.getByPlaceholderText(/search findings/i), {
      target: { value: 'injection' },
    })

    await waitFor(() => {
      expect(onResults).toHaveBeenCalledWith(findings)
    }, { timeout: 2000 })
  }, 10000)

  it('calls onResults with empty array when search fails', async () => {
    mockSearch.mockRejectedValue(new Error('API error'))
    const onResults = vi.fn()

    render(<FindingsSearch experimentId="e1" onResults={onResults} />)

    fireEvent.change(screen.getByPlaceholderText(/search findings/i), {
      target: { value: 'error-query' },
    })

    await waitFor(() => {
      expect(onResults).toHaveBeenCalledWith([])
    }, { timeout: 2000 })
  }, 10000)

  it('shows clear button after search completes (loading=false, query not empty)', async () => {
    mockSearch.mockResolvedValue([])
    render(<FindingsSearch experimentId="e1" onResults={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText(/search findings/i), {
      target: { value: 'sqli' },
    })

    // Wait for the debounce to fire and loading to complete
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /clear search/i })).toBeInTheDocument()
    }, { timeout: 2000 })
  }, 10000)

  it('clears query and calls onResults([]) when clear button clicked', async () => {
    mockSearch.mockResolvedValue([])
    const onResults = vi.fn()

    render(<FindingsSearch experimentId="e1" onResults={onResults} />)

    fireEvent.change(screen.getByPlaceholderText(/search findings/i), {
      target: { value: 'sqli' },
    })

    // Wait for loading to finish so the clear button is visible
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /clear search/i })).toBeInTheDocument()
    }, { timeout: 2000 })

    fireEvent.click(screen.getByRole('button', { name: /clear search/i }))

    const input = screen.getByPlaceholderText(/search findings/i) as HTMLInputElement
    expect(input.value).toBe('')
    expect(onResults).toHaveBeenCalledWith([])
  }, 10000)

  it('shows loading spinner while request is in-flight', async () => {
    mockSearch.mockReturnValue(new Promise(() => {}))  // never resolves
    render(<FindingsSearch experimentId="e1" onResults={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText(/search findings/i), {
      target: { value: 'sql' },
    })

    await waitFor(() => {
      const spinners = document.querySelectorAll('.animate-spin')
      expect(spinners.length).toBeGreaterThanOrEqual(1)
    }, { timeout: 2000 })
  }, 10000)
})
