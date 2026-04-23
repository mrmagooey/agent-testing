import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import CVEDiscovery from '../../pages/CVEDiscovery'
import type { CVECandidate } from '../../api/client'

vi.mock('../../api/client', () => ({
  discoverCVEs: vi.fn(),
  resolveCVE: vi.fn(),
  importCVE: vi.fn(),
}))

vi.mock('../../components/CVECandidateTable', () => ({
  default: vi.fn(
    ({
      candidates,
      onImport,
    }: {
      candidates: CVECandidate[]
      onImport: (ids: string[]) => void
    }) => (
      <div data-testid="cve-candidate-table">
        {candidates.map((c) => (
          <div key={c.cve_id} data-testid="cve-row">
            <span>{c.cve_id}</span>
            <button onClick={() => onImport([c.cve_id])}>Import {c.cve_id}</button>
          </div>
        ))}
      </div>
    ),
  ),
}))

import { discoverCVEs, resolveCVE, importCVE } from '../../api/client'
const mockDiscoverCVEs = vi.mocked(discoverCVEs)
const mockResolveCVE = vi.mocked(resolveCVE)
const mockImportCVE = vi.mocked(importCVE)

function makeCandidate(overrides: Partial<CVECandidate> = {}): CVECandidate {
  return {
    cve_id: 'CVE-2024-12345',
    score: 0.85,
    vuln_class: 'sqli',
    severity: 'high',
    language: 'python',
    repo: 'github.com/example/repo',
    files_changed: 3,
    lines_changed: 42,
    importable: true,
    description: 'SQL injection vulnerability in login handler',
    ...overrides,
  }
}

function renderCVEDiscovery() {
  return render(
    <MemoryRouter initialEntries={['/datasets/discover']}>
      <CVEDiscovery />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('CVEDiscovery — initial render', () => {
  it('renders the "CVE Discovery" heading', () => {
    renderCVEDiscovery()

    expect(screen.getByRole('heading', { name: 'CVE Discovery' })).toBeInTheDocument()
  })

  it('renders Search and Resolve CVE tabs', () => {
    renderCVEDiscovery()

    expect(screen.getByRole('button', { name: 'Search' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resolve CVE' })).toBeInTheDocument()
  })

  it('starts on the Search tab by default', () => {
    renderCVEDiscovery()

    // Search CVEs button is on the Search tab form
    expect(screen.getByRole('button', { name: 'Search CVEs' })).toBeInTheDocument()
  })

  it('renders language filter chips', () => {
    renderCVEDiscovery()

    expect(screen.getByText('python')).toBeInTheDocument()
    expect(screen.getByText('javascript')).toBeInTheDocument()
    expect(screen.getByText('go')).toBeInTheDocument()
  })

  it('renders severity filter chips', () => {
    renderCVEDiscovery()

    expect(screen.getByText('critical')).toBeInTheDocument()
    expect(screen.getByText('high')).toBeInTheDocument()
    expect(screen.getByText('medium')).toBeInTheDocument()
    expect(screen.getByText('low')).toBeInTheDocument()
  })
})

describe('CVEDiscovery — Search tab: data-load success', () => {
  it('shows candidate table after successful search', async () => {
    mockDiscoverCVEs.mockResolvedValue([
      makeCandidate({ cve_id: 'CVE-2024-001' }),
      makeCandidate({ cve_id: 'CVE-2024-002', language: 'java' }),
    ])

    renderCVEDiscovery()

    fireEvent.click(screen.getByRole('button', { name: 'Search CVEs' }))

    await waitFor(() => {
      expect(screen.getByTestId('cve-candidate-table')).toBeInTheDocument()
    })
    expect(screen.getByText('CVE-2024-001')).toBeInTheDocument()
    expect(screen.getByText('CVE-2024-002')).toBeInTheDocument()
  })

  it('passes selected filters to discoverCVEs', async () => {
    mockDiscoverCVEs.mockResolvedValue([])

    renderCVEDiscovery()

    // Toggle python language chip
    fireEvent.click(screen.getByText('python'))
    // Toggle high severity chip
    fireEvent.click(screen.getByText('high'))

    fireEvent.click(screen.getByRole('button', { name: 'Search CVEs' }))

    await waitFor(() => {
      expect(mockDiscoverCVEs).toHaveBeenCalledWith(
        expect.objectContaining({
          languages: ['python'],
          severities: ['high'],
        }),
      )
    })
  })
})

describe('CVEDiscovery — Search tab: empty state', () => {
  it('does not show candidate table when search returns empty array', async () => {
    mockDiscoverCVEs.mockResolvedValue([])

    renderCVEDiscovery()

    fireEvent.click(screen.getByRole('button', { name: 'Search CVEs' }))

    await waitFor(() => {
      expect(mockDiscoverCVEs).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('cve-candidate-table')).not.toBeInTheDocument()
  })
})

describe('CVEDiscovery — Search tab: error state', () => {
  it('shows error message when discoverCVEs rejects', async () => {
    mockDiscoverCVEs.mockRejectedValue(new Error('API unreachable'))

    renderCVEDiscovery()

    fireEvent.click(screen.getByRole('button', { name: 'Search CVEs' }))

    await waitFor(() => {
      expect(screen.getByText('API unreachable')).toBeInTheDocument()
    })
  })
})

describe('CVEDiscovery — Search tab: import interaction', () => {
  it('calls importCVE when import is triggered from the table', async () => {
    mockDiscoverCVEs.mockResolvedValue([makeCandidate({ cve_id: 'CVE-2024-999' })])
    mockImportCVE.mockResolvedValue({
      name: 'cve-2024-999',
      source: 'cve',
      label_count: 0,
      file_count: 0,
      size_bytes: 0,
      created_at: '2024-01-01T00:00:00Z',
      languages: [],
    })

    renderCVEDiscovery()

    fireEvent.click(screen.getByRole('button', { name: 'Search CVEs' }))

    await waitFor(() => {
      expect(screen.getByText('CVE-2024-999')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Import CVE-2024-999' }))

    await waitFor(() => {
      expect(mockImportCVE).toHaveBeenCalledWith('CVE-2024-999')
    })
  })
})

describe('CVEDiscovery — tab navigation', () => {
  it('switches to Resolve CVE tab when that tab is clicked', () => {
    renderCVEDiscovery()

    fireEvent.click(screen.getByRole('button', { name: 'Resolve CVE' }))

    expect(screen.getByText('Resolve CVE by ID')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('CVE-2024-12345')).toBeInTheDocument()
  })

  it('switches back to Search tab from Resolve tab', () => {
    renderCVEDiscovery()

    fireEvent.click(screen.getByRole('button', { name: 'Resolve CVE' }))
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))

    expect(screen.getByRole('button', { name: 'Search CVEs' })).toBeInTheDocument()
  })
})

describe('CVEDiscovery — Resolve tab: success', () => {
  it('shows resolved CVE details after successful resolve', async () => {
    mockResolveCVE.mockResolvedValue(
      makeCandidate({
        cve_id: 'CVE-2024-12345',
        vuln_class: 'rce',
        severity: 'critical',
        language: 'java',
        importable: true,
      }),
    )

    renderCVEDiscovery()

    fireEvent.click(screen.getByRole('button', { name: 'Resolve CVE' }))

    const input = screen.getByPlaceholderText('CVE-2024-12345')
    fireEvent.change(input, { target: { value: 'CVE-2024-12345' } })

    fireEvent.click(screen.getByRole('button', { name: 'Resolve' }))

    await waitFor(() => {
      expect(mockResolveCVE).toHaveBeenCalledWith('CVE-2024-12345')
    })
    await waitFor(() => {
      // CVE ID appears in the details list
      expect(screen.getAllByText('CVE-2024-12345').length).toBeGreaterThan(0)
    })
    // Import button appears for importable CVE
    expect(screen.getByRole('button', { name: 'Import' })).toBeInTheDocument()
  })

  it('shows success message after importing resolved CVE', async () => {
    mockResolveCVE.mockResolvedValue(makeCandidate({ cve_id: 'CVE-2024-12345', importable: true }))
    mockImportCVE.mockResolvedValue({
      name: 'cve-2024-12345',
      source: 'cve',
      label_count: 0,
      file_count: 0,
      size_bytes: 0,
      created_at: '2024-01-01T00:00:00Z',
      languages: [],
    })

    renderCVEDiscovery()

    fireEvent.click(screen.getByRole('button', { name: 'Resolve CVE' }))
    const input = screen.getByPlaceholderText('CVE-2024-12345')
    fireEvent.change(input, { target: { value: 'CVE-2024-12345' } })
    fireEvent.click(screen.getByRole('button', { name: 'Resolve' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Import' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Import' }))

    await waitFor(() => {
      expect(screen.getByText('Imported successfully.')).toBeInTheDocument()
    })
  })
})

describe('CVEDiscovery — Resolve tab: error state', () => {
  it('shows error when resolveCVE rejects', async () => {
    mockResolveCVE.mockRejectedValue(new Error('CVE not found'))

    renderCVEDiscovery()

    fireEvent.click(screen.getByRole('button', { name: 'Resolve CVE' }))

    const input = screen.getByPlaceholderText('CVE-2024-12345')
    fireEvent.change(input, { target: { value: 'CVE-9999-00000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Resolve' }))

    await waitFor(() => {
      expect(screen.getByText('CVE not found')).toBeInTheDocument()
    })
  })
})

describe('CVEDiscovery — Resolve tab: Resolve button disabled state', () => {
  it('Resolve button is disabled when input is empty', () => {
    renderCVEDiscovery()

    fireEvent.click(screen.getByRole('button', { name: 'Resolve CVE' }))

    expect(screen.getByRole('button', { name: 'Resolve' })).toBeDisabled()
  })

  it('Resolve button is enabled when input has text', () => {
    renderCVEDiscovery()

    fireEvent.click(screen.getByRole('button', { name: 'Resolve CVE' }))

    const input = screen.getByPlaceholderText('CVE-2024-12345')
    fireEvent.change(input, { target: { value: 'CVE-2024-12345' } })

    expect(screen.getByRole('button', { name: 'Resolve' })).not.toBeDisabled()
  })
})
