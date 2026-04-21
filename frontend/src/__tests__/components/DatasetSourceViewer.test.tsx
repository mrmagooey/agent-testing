import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import DatasetSourceViewer from '../../components/DatasetSourceViewer'
import type { Label } from '../../api/client'

// CodeMirror is not available in jsdom; stub CodeViewer.
vi.mock('../../components/CodeViewer', () => ({
  default: ({
    content,
    annotations,
    scrollToLine,
  }: {
    content: string
    annotations?: Array<{ line: number; className: string }>
    scrollToLine?: number
  }) => (
    <div
      data-testid="code-viewer"
      data-annotations={JSON.stringify(annotations ?? [])}
      data-scroll-to-line={scrollToLine}
    >
      {content}
    </div>
  ),
}))

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeLabel(overrides: Partial<Label> = {}): Label {
  return {
    label_id: 'lbl-1',
    dataset: 'myds',
    file_path: 'src/auth.py',
    line_start: 10,
    line_end: 15,
    vuln_class: 'sqli',
    severity: 'high',
    description: 'SQL injection',
    source: 'manual',
    ...overrides,
  }
}

function mockFetch(body: unknown, status = 200) {
  globalThis.fetch = vi.fn().mockResolvedValueOnce({
    ok: status < 400,
    status,
    json: async () => body,
  } as Response)
}

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('DatasetSourceViewer', () => {
  it('shows loading skeleton initially', () => {
    globalThis.fetch = vi.fn().mockReturnValue(new Promise(() => {}))
    render(
      <DatasetSourceViewer datasetName="myds" filePath="src/auth.py" />,
    )
    expect(screen.getByLabelText('Loading source file')).toBeInTheDocument()
  })

  it('renders file content in CodeViewer after successful fetch', async () => {
    mockFetch({
      path: 'src/auth.py',
      content: 'def login(): pass\n',
      language: 'python',
      line_count: 1,
      size_bytes: 18,
      labels: [],
    })
    render(<DatasetSourceViewer datasetName="myds" filePath="src/auth.py" />)
    await waitFor(() => {
      expect(screen.getByTestId('code-viewer')).toBeInTheDocument()
    })
    expect(screen.getByTestId('code-viewer').textContent).toContain('def login(): pass')
  })

  it('shows 404 message when file is not found', async () => {
    mockFetch({ detail: 'File not found' }, 404)
    render(<DatasetSourceViewer datasetName="myds" filePath="gone.py" />)
    await waitFor(() => {
      expect(screen.getByText(/file no longer in dataset/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/gone\.py/)).toBeInTheDocument()
  })

  it('shows path-escapes error for 400', async () => {
    mockFetch({ detail: 'path escapes dataset' }, 400)
    render(<DatasetSourceViewer datasetName="myds" filePath="../etc/passwd" />)
    await waitFor(() => {
      expect(screen.getByText(/path escapes dataset/i)).toBeInTheDocument()
    })
  })

  it('shows binary placeholder for binary files', async () => {
    mockFetch({
      path: 'data.bin',
      binary: true,
      content: '',
      language: 'text',
      line_count: 0,
      size_bytes: 4096,
      labels: [],
    })
    render(<DatasetSourceViewer datasetName="myds" filePath="data.bin" />)
    await waitFor(() => {
      expect(screen.getByText(/binary file/i)).toBeInTheDocument()
    })
  })

  it('shows truncation banner for large files', async () => {
    mockFetch({
      path: 'huge.py',
      content: 'x'.repeat(100) + '\n\n... [truncated] ...\n\n' + 'y'.repeat(100),
      language: 'python',
      line_count: 5,
      size_bytes: 3 * 1024 * 1024,
      labels: [],
      truncated: true,
    })
    render(<DatasetSourceViewer datasetName="myds" filePath="huge.py" />)
    await waitFor(() => {
      expect(screen.getByText(/file is large/i)).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /load anyway/i })).toBeInTheDocument()
  })

  it('hides truncation banner when "Load anyway" is clicked', async () => {
    mockFetch({
      path: 'huge.py',
      content: 'content',
      language: 'python',
      line_count: 1,
      size_bytes: 3 * 1024 * 1024,
      labels: [],
      truncated: true,
    })
    render(<DatasetSourceViewer datasetName="myds" filePath="huge.py" />)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /load anyway/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /load anyway/i }))
    expect(screen.queryByText(/file is large/i)).not.toBeInTheDocument()
  })

  it('builds amber annotations for finding range', async () => {
    mockFetch({
      path: 'src/auth.py',
      content: 'line1\nline2\nline3\n',
      language: 'python',
      line_count: 3,
      size_bytes: 18,
      labels: [],
    })
    render(
      <DatasetSourceViewer
        datasetName="myds"
        filePath="src/auth.py"
        highlightStart={2}
        highlightEnd={3}
      />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('code-viewer')).toBeInTheDocument()
    })
    const annotations = JSON.parse(
      screen.getByTestId('code-viewer').getAttribute('data-annotations') ?? '[]',
    ) as Array<{ line: number; className: string }>
    const amberLines = annotations.filter((a) => a.className.includes('amber'))
    expect(amberLines.map((a) => a.line)).toEqual(expect.arrayContaining([2, 3]))
  })

  it('builds emerald annotations for ground-truth labels', async () => {
    mockFetch({
      path: 'src/auth.py',
      content: 'line1\nline2\nline3\n',
      language: 'python',
      line_count: 3,
      size_bytes: 18,
      labels: [],
    })
    const labels = [makeLabel({ line_start: 1, line_end: 2 })]
    render(
      <DatasetSourceViewer
        datasetName="myds"
        filePath="src/auth.py"
        groundTruthLabels={labels}
      />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('code-viewer')).toBeInTheDocument()
    })
    const annotations = JSON.parse(
      screen.getByTestId('code-viewer').getAttribute('data-annotations') ?? '[]',
    ) as Array<{ line: number; className: string }>
    const emeraldLines = annotations.filter((a) => a.className.includes('emerald'))
    expect(emeraldLines.map((a) => a.line)).toEqual(expect.arrayContaining([1, 2]))
  })

  it('passes highlightStart as scrollToLine to CodeViewer', async () => {
    mockFetch({
      path: 'src/auth.py',
      content: 'line1\nline2\nline3\n',
      language: 'python',
      line_count: 3,
      size_bytes: 18,
      labels: [],
    })
    render(
      <DatasetSourceViewer
        datasetName="myds"
        filePath="src/auth.py"
        highlightStart={42}
        highlightEnd={47}
      />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('code-viewer')).toBeInTheDocument()
    })
    expect(screen.getByTestId('code-viewer').getAttribute('data-scroll-to-line')).toBe('42')
  })

  it('includes start and end query params in fetch URL', async () => {
    const mockFn = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        path: 'src/auth.py',
        content: '',
        language: 'python',
        line_count: 0,
        size_bytes: 0,
        labels: [],
      }),
    })
    globalThis.fetch = mockFn
    render(
      <DatasetSourceViewer
        datasetName="myds"
        filePath="src/auth.py"
        highlightStart={10}
        highlightEnd={20}
      />,
    )
    await waitFor(() => {
      expect(mockFn).toHaveBeenCalledOnce()
    })
    const url = mockFn.mock.calls[0][0] as string
    expect(url).toContain('start=10')
    expect(url).toContain('end=20')
  })
})
