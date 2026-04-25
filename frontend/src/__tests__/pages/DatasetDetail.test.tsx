import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import DatasetDetail from '../../pages/DatasetDetail'
import type { Label, InjectionTemplate, DatasetRow } from '../../api/client'

vi.mock('../../api/client', () => ({
  getFileTree: vi.fn(),
  getLabels: vi.fn(),
  getFileContent: vi.fn(),
  listTemplates: vi.fn(),
  previewInjection: vi.fn(),
  injectVuln: vi.fn(),
  getDataset: vi.fn(),
  rematerializeDataset: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    body: unknown
    constructor(message: string, status: number, body: unknown) {
      super(message)
      this.name = 'ApiError'
      this.status = status
      this.body = body
    }
  },
}))

// Mock heavy sub-components
vi.mock('../../components/FileTree', () => ({
  default: ({ onSelect }: { onSelect: (path: string) => void }) => (
    <div data-testid="file-tree">
      <button onClick={() => onSelect('src/main.py')}>src/main.py</button>
    </div>
  ),
}))
vi.mock('../../components/CodeViewer', () => ({
  default: ({ content }: { content: string }) => <pre data-testid="code-viewer">{content}</pre>,
}))
vi.mock('../../components/DiffViewer', () => ({
  default: () => <div data-testid="diff-viewer" />,
}))

import {
  getFileTree,
  getLabels,
  getFileContent,
  listTemplates,
  previewInjection,
  injectVuln,
  getDataset,
  rematerializeDataset,
} from '../../api/client'

const mockGetFileTree = vi.mocked(getFileTree)
const mockGetLabels = vi.mocked(getLabels)
const mockGetFileContent = vi.mocked(getFileContent)
const mockListTemplates = vi.mocked(listTemplates)
const mockPreviewInjection = vi.mocked(previewInjection)
const mockInjectVuln = vi.mocked(injectVuln)
const mockGetDataset = vi.mocked(getDataset)
const mockRematerializeDataset = vi.mocked(rematerializeDataset)

function makeLabel(overrides: Partial<Label> = {}): Label {
  return {
    label_id: 'lbl-1',
    dataset: 'ds-test',
    file_path: 'src/main.py',
    line_start: 10,
    line_end: 20,
    vuln_class: 'sqli',
    severity: 'high',
    description: 'SQL injection vulnerability',
    source: 'manual',
    ...overrides,
  }
}

function makeTemplate(overrides: Partial<InjectionTemplate> = {}): InjectionTemplate {
  return {
    template_id: 'tmpl-1',
    language: 'python',
    cwe: 'CWE-89',
    severity: 'high',
    description: 'SQL injection template',
    vuln_class: 'sqli',
    anchor_pattern: 'cursor.execute',
    ...overrides,
  }
}

function makeDatasetRow(overrides: Partial<DatasetRow> = {}): DatasetRow {
  return {
    name: 'ds-test',
    kind: 'git',
    origin_url: 'https://github.com/example/repo',
    origin_commit: 'abc123def456789012',
    origin_ref: 'main',
    cve_id: null,
    base_dataset: null,
    recipe_json: null,
    metadata: {},
    created_at: '2026-01-15T00:00:00Z',
    materialized_at: '2026-01-16T00:00:00Z',
    ...overrides,
  }
}

function renderPage(datasetName = 'ds-test') {
  return render(
    <MemoryRouter initialEntries={[`/datasets/${datasetName}`]}>
      <Routes>
        <Route path="/datasets/:name" element={<DatasetDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetFileTree.mockResolvedValue({ 'src': { 'main.py': null } })
  mockGetLabels.mockResolvedValue([])
  mockGetFileContent.mockResolvedValue({ content: 'print("hello")', language: 'python' })
  mockListTemplates.mockResolvedValue([])
  mockGetDataset.mockResolvedValue(makeDatasetRow())
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('DatasetDetail — loading and error', () => {
  it('shows loading text while fetching', () => {
    mockGetFileTree.mockReturnValue(new Promise(() => {}))
    mockGetLabels.mockReturnValue(new Promise(() => {}))
    mockGetDataset.mockReturnValue(new Promise(() => {}))
    renderPage()
    expect(screen.getByText(/Loading dataset/i)).toBeInTheDocument()
  })

  it('shows error message when fetch fails', async () => {
    mockGetFileTree.mockRejectedValue(new Error('Dataset not found'))
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/Dataset not found/)).toBeInTheDocument()
    })
  })
})

describe('DatasetDetail — data rendered', () => {
  it('renders dataset name as heading', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'ds-test' })).toBeInTheDocument()
    })
  })

  it('renders Files section with file tree', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /files/i })).toBeInTheDocument()
      expect(screen.getByTestId('file-tree')).toBeInTheDocument()
    })
  })

  it('renders Labels section', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/Labels \(0\)/)).toBeInTheDocument()
    })
  })

  it('shows "No labels yet." when labels list is empty', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('No labels yet.')).toBeInTheDocument()
    })
  })

  it('renders label rows when labels are present', async () => {
    mockGetLabels.mockResolvedValue([
      makeLabel({ label_id: 'lbl-1', vuln_class: 'sqli' }),
      makeLabel({ label_id: 'lbl-2', vuln_class: 'xss', file_path: 'src/view.py' }),
    ])
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('sqli')).toBeInTheDocument()
      expect(screen.getByText('xss')).toBeInTheDocument()
    })
  })

  it('renders Inject Vulnerability button', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /inject vulnerability/i })).toBeInTheDocument()
    })
  })
})

describe('DatasetDetail — file selection', () => {
  it('shows "Select a file to view" before any file is selected', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/Select a file to view/)).toBeInTheDocument()
    })
  })

  it('loads and shows file content after selecting a file', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('file-tree')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('src/main.py'))

    await waitFor(() => {
      expect(mockGetFileContent).toHaveBeenCalledWith('ds-test', 'src/main.py')
    })
    await waitFor(() => {
      expect(screen.getByTestId('code-viewer')).toBeInTheDocument()
    })
  })
})

describe('DatasetDetail — injection workflow', () => {
  it('opens injection modal at step 1 when Inject Vulnerability is clicked', async () => {
    mockListTemplates.mockResolvedValue([makeTemplate()])
    renderPage()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /inject vulnerability/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /inject vulnerability/i }))

    await waitFor(() => {
      expect(screen.getByText(/Inject Vulnerability — Step 1\/5/i)).toBeInTheDocument()
    })
  })

  it('shows templates in step 1', async () => {
    mockListTemplates.mockResolvedValue([makeTemplate({ vuln_class: 'sqli' })])
    renderPage()
    await waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: /inject vulnerability/i }))
    })
    await waitFor(() => {
      expect(screen.getByText('sqli')).toBeInTheDocument()
    })
  })

  it('advances to step 2 after selecting a template', async () => {
    mockListTemplates.mockResolvedValue([makeTemplate({ vuln_class: 'sqli' })])
    renderPage()
    await waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: /inject vulnerability/i }))
    })
    await waitFor(() => {
      expect(screen.getByText('sqli')).toBeInTheDocument()
    })

    // Click the template button
    fireEvent.click(screen.getByText('sqli'))

    await waitFor(() => {
      expect(screen.getByText(/Step 2\/5/i)).toBeInTheDocument()
    })
  })

  it('closes modal when × button is clicked', async () => {
    mockListTemplates.mockResolvedValue([makeTemplate()])
    renderPage()
    await waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: /inject vulnerability/i }))
    })
    await waitFor(() => {
      expect(screen.getByText(/Step 1\/5/i)).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('×'))

    expect(screen.queryByText(/Step 1\/5/i)).not.toBeInTheDocument()
  })

  it('calls previewInjection and advances to step 4', async () => {
    const tmpl = makeTemplate({ description: 'No placeholders here', vuln_class: 'sqli' })
    mockListTemplates.mockResolvedValue([tmpl])
    mockPreviewInjection.mockResolvedValue({
      before: 'old code',
      after: 'new code',
      language: 'python',
      label: makeLabel(),
      warnings: [],
    })

    renderPage()
    await waitFor(() => {
      fireEvent.click(screen.getByRole('button', { name: /inject vulnerability/i }))
    })

    // Step 1 → select template
    await waitFor(() => expect(screen.getByText('sqli')).toBeInTheDocument())
    fireEvent.click(screen.getByText('sqli'))

    // Step 2 → select file from tree
    await waitFor(() => expect(screen.getByText(/Step 2\/5/i)).toBeInTheDocument())
    // Two file-trees render: one in the main page sidebar, one in the modal.
    // The modal's tree is the last one; click its button to advance.
    const fileTrees = screen.getAllByTestId('file-tree')
    fireEvent.click(fileTrees[fileTrees.length - 1].querySelector('button')!)

    // Step 3 → no substitutions, click Preview
    await waitFor(() => expect(screen.getByText(/Step 3\/5/i)).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /preview injection/i }))

    await waitFor(() => {
      expect(mockPreviewInjection).toHaveBeenCalled()
      expect(screen.getByText(/Step 4\/5/i)).toBeInTheDocument()
    })
  })
})

// ─── Origin card — git kind ───────────────────────────────────────────────────

describe('DatasetDetail — Origin card (git kind)', () => {
  it('renders "Git origin" section with URL, commit, and ref', async () => {
    mockGetDataset.mockResolvedValue(
      makeDatasetRow({
        kind: 'git',
        origin_url: 'https://github.com/example/repo',
        origin_commit: 'abc123def456789012',
        origin_ref: 'main',
      }),
    )
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('Git origin')).toBeInTheDocument()
    })
    expect(screen.getByRole('link', { name: 'https://github.com/example/repo' })).toBeInTheDocument()
    // Truncated commit (first 12 chars)
    expect(screen.getByText(/abc123def456/)).toBeInTheDocument()
    expect(screen.getByText('main')).toBeInTheDocument()
  })

  it('renders CVE chip linking to cve-discovery when cve_id is present', async () => {
    mockGetDataset.mockResolvedValue(
      makeDatasetRow({ cve_id: 'CVE-2024-1234' }),
    )
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('CVE-2024-1234')).toBeInTheDocument()
    })
    const cveLink = screen.getByRole('link', { name: 'CVE-2024-1234' })
    expect(cveLink.getAttribute('href')).toContain('CVE-2024-1234')
  })

  it('renders created_at and materialized_at formatted dates', async () => {
    mockGetDataset.mockResolvedValue(
      makeDatasetRow({
        created_at: '2026-01-15T00:00:00Z',
        materialized_at: '2026-01-16T00:00:00Z',
      }),
    )
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/Jan 15, 2026/)).toBeInTheDocument()
      expect(screen.getByText(/Jan 16, 2026/)).toBeInTheDocument()
    })
  })
})

// ─── Origin card — derived kind ──────────────────────────────────────────────

describe('DatasetDetail — Origin card (derived kind)', () => {
  it('renders "Derived from" section with base_dataset link', async () => {
    mockGetDataset.mockResolvedValue(
      makeDatasetRow({
        kind: 'derived',
        base_dataset: 'cve-2023-base',
        recipe_json: null,
        origin_url: null,
        origin_commit: null,
        origin_ref: null,
      }),
    )
    renderPage()
    await waitFor(() => {
      expect(screen.getByText('Derived from')).toBeInTheDocument()
    })
    const link = screen.getByRole('link', { name: 'cve-2023-base' })
    expect(link.getAttribute('href')).toContain('/datasets/cve-2023-base')
  })

  it('renders recipe summary with templates_version and application count', async () => {
    const recipe = {
      templates_version: 'v2.1',
      applications: [
        { template_id: 'tmpl-sqli', target_file: 'src/db.py', seed: 42 },
        { template_id: 'tmpl-xss', target_file: 'src/view.py', seed: 99 },
      ],
    }
    mockGetDataset.mockResolvedValue(
      makeDatasetRow({
        kind: 'derived',
        base_dataset: 'base-ds',
        recipe_json: JSON.stringify(recipe),
        origin_url: null,
        origin_commit: null,
        origin_ref: null,
      }),
    )
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/templates version/i)).toBeInTheDocument()
      expect(screen.getByText('v2.1')).toBeInTheDocument()
      // "Applications: 2" paragraph
      expect(screen.getByText(/^Applications:/)).toBeInTheDocument()
    })
  })
})

// ─── Materialization banner ──────────────────────────────────────────────────

describe('DatasetDetail — Materialization', () => {
  it('shows materialization banner when materialized_at is null', async () => {
    mockGetDataset.mockResolvedValue(
      makeDatasetRow({ materialized_at: null }),
    )
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/not currently materialized/i)).toBeInTheDocument()
      expect(screen.getByTestId('materialize-btn')).toBeInTheDocument()
    })
  })

  it('does NOT show materialization banner when materialized_at is set', async () => {
    mockGetDataset.mockResolvedValue(
      makeDatasetRow({ materialized_at: '2026-01-16T00:00:00Z' }),
    )
    renderPage()
    await waitFor(() => {
      expect(screen.queryByText(/not currently materialized/i)).not.toBeInTheDocument()
    })
  })

  it('POSTs to rematerialize and updates state on success', async () => {
    mockGetDataset.mockResolvedValue(
      makeDatasetRow({ materialized_at: null }),
    )
    mockRematerializeDataset.mockResolvedValue({ materialized_at: '2026-04-24T12:00:00Z' })

    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('materialize-btn')).toBeInTheDocument()
    })

    await act(async () => {
      fireEvent.click(screen.getByTestId('materialize-btn'))
    })

    await waitFor(() => {
      expect(mockRematerializeDataset).toHaveBeenCalledWith('ds-test')
    })

    // Banner should be gone after success
    await waitFor(() => {
      expect(screen.queryByText(/not currently materialized/i)).not.toBeInTheDocument()
    })
  })

  it('shows error banner when rematerialization fails', async () => {
    // Use a plain Error so that the ApiError check falls through to the generic handler
    mockGetDataset.mockResolvedValue(
      makeDatasetRow({ materialized_at: null }),
    )
    mockRematerializeDataset.mockRejectedValue(new Error('Server error'))

    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('materialize-btn')).toBeInTheDocument()
    })

    await act(async () => {
      fireEvent.click(screen.getByTestId('materialize-btn'))
    })

    await waitFor(() => {
      expect(screen.getByTestId('materialize-error')).toBeInTheDocument()
      expect(screen.getByTestId('materialize-error')).toHaveTextContent(/materialization failed/i)
    })
  })
})

// ─── Labels filter ────────────────────────────────────────────────────────────

describe('DatasetDetail — Labels filter', () => {
  it('renders filter controls for cwe, severity, and source', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('filter-cwe')).toBeInTheDocument()
      expect(screen.getByTestId('filter-severity')).toBeInTheDocument()
      expect(screen.getByTestId('filter-source')).toBeInTheDocument()
    })
  })

  it('re-fetches labels with cwe filter when CWE input changes', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('filter-cwe')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByTestId('filter-cwe'), { target: { value: 'CWE-89' } })

    await waitFor(() => {
      // getLabels should have been called with the cwe filter
      const calls = mockGetLabels.mock.calls
      const filteredCall = calls.find((c) => c[1]?.cwe === 'CWE-89')
      expect(filteredCall).toBeDefined()
    })
  })

  it('re-fetches labels with severity filter when severity select changes', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('filter-severity')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByTestId('filter-severity'), { target: { value: 'high' } })

    await waitFor(() => {
      const calls = mockGetLabels.mock.calls
      const filteredCall = calls.find((c) => c[1]?.severity === 'high')
      expect(filteredCall).toBeDefined()
    })
  })

  it('shows clear filters button when any filter is active', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('filter-cwe')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByTestId('filter-source'), { target: { value: 'manual' } })

    await waitFor(() => {
      expect(screen.getByTestId('filter-clear')).toBeInTheDocument()
    })
  })

  it('clears all filters when "Clear filters" is clicked', async () => {
    renderPage()
    await waitFor(() => {
      expect(screen.getByTestId('filter-cwe')).toBeInTheDocument()
    })

    fireEvent.change(screen.getByTestId('filter-cwe'), { target: { value: 'CWE-89' } })
    await waitFor(() => expect(screen.getByTestId('filter-clear')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('filter-clear'))

    await waitFor(() => {
      expect((screen.getByTestId('filter-cwe') as HTMLInputElement).value).toBe('')
      expect(screen.queryByTestId('filter-clear')).not.toBeInTheDocument()
    })
  })
})
