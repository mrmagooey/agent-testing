import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import DatasetDetail from '../../pages/DatasetDetail'
import type { Label, InjectionTemplate } from '../../api/client'

vi.mock('../../api/client', () => ({
  getFileTree: vi.fn(),
  getLabels: vi.fn(),
  getFileContent: vi.fn(),
  listTemplates: vi.fn(),
  previewInjection: vi.fn(),
  injectVuln: vi.fn(),
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
} from '../../api/client'

const mockGetFileTree = vi.mocked(getFileTree)
const mockGetLabels = vi.mocked(getLabels)
const mockGetFileContent = vi.mocked(getFileContent)
const mockListTemplates = vi.mocked(listTemplates)
const mockPreviewInjection = vi.mocked(previewInjection)
const mockInjectVuln = vi.mocked(injectVuln)

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
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('DatasetDetail — loading and error', () => {
  it('shows loading text while fetching', () => {
    mockGetFileTree.mockReturnValue(new Promise(() => {}))
    mockGetLabels.mockReturnValue(new Promise(() => {}))
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
