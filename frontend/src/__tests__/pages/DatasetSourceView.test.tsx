import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import DatasetSourceView from '../../pages/DatasetSourceView'

// DatasetSourceViewer fetches via global fetch (not api/client).
// Mock it at component level so we don't need a real fetch.
vi.mock('../../components/DatasetSourceViewer', () => ({
  default: vi.fn(({ datasetName, filePath }: { datasetName: string; filePath: string }) => (
    <div data-testid="source-viewer">
      viewer:{datasetName}:{filePath}
    </div>
  )),
}))

vi.mock('../../components/Breadcrumbs', () => ({
  default: vi.fn(({ items }: { items: { label: string; to?: string }[] }) => (
    <nav data-testid="breadcrumbs">{items.map((i) => i.label).join(' / ')}</nav>
  )),
}))

function renderDSV(path: string, initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path={path} element={<DatasetSourceView />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('DatasetSourceView — initial render', () => {
  it('renders the file path as heading when path param is present', () => {
    renderDSV(
      '/datasets/:name/source',
      '/datasets/my-dataset/source?path=src/foo.py',
    )

    expect(screen.getByText('src/foo.py')).toBeInTheDocument()
  })

  it('renders "(no path)" heading when path param is absent', () => {
    renderDSV('/datasets/:name/source', '/datasets/my-dataset/source')

    expect(screen.getByText('(no path)')).toBeInTheDocument()
  })

  it('renders the DatasetSourceViewer when both datasetName and path are present', () => {
    renderDSV(
      '/datasets/:name/source',
      '/datasets/cve-2024-python/source?path=src/vuln.py',
    )

    expect(screen.getByTestId('source-viewer')).toBeInTheDocument()
    expect(screen.getByTestId('source-viewer').textContent).toContain('cve-2024-python')
    expect(screen.getByTestId('source-viewer').textContent).toContain('src/vuln.py')
  })

  it('renders "No file path specified" placeholder when path is missing', () => {
    renderDSV('/datasets/:name/source', '/datasets/my-dataset/source')

    expect(screen.getByText('No file path specified.')).toBeInTheDocument()
  })
})

describe('DatasetSourceView — breadcrumbs', () => {
  it('includes dataset name and file path in breadcrumbs', () => {
    renderDSV(
      '/datasets/:name/source',
      '/datasets/cve-2024-python/source?path=src/vuln.py',
    )

    const bc = screen.getByTestId('breadcrumbs')
    expect(bc.textContent).toContain('Datasets')
    expect(bc.textContent).toContain('cve-2024-python')
    expect(bc.textContent).toContain('src/vuln.py')
  })
})

describe('DatasetSourceView — "Back to run" link', () => {
  it('shows "Back to run" link when from_experiment and from_run params are provided', () => {
    renderDSV(
      '/datasets/:name/source',
      '/datasets/my-dataset/source?path=src/vuln.py&from_experiment=exp-1&from_run=run-abc',
    )

    const link = screen.getByText('Back to run')
    expect(link).toBeInTheDocument()
    expect(link.closest('a')).toHaveAttribute(
      'href',
      '/experiments/exp-1/runs/run-abc',
    )
  })

  it('does NOT show "Back to run" link when from_experiment / from_run params are absent', () => {
    renderDSV(
      '/datasets/:name/source',
      '/datasets/my-dataset/source?path=src/vuln.py',
    )

    expect(screen.queryByText('Back to run')).not.toBeInTheDocument()
  })

  it('does NOT show "Back to run" link when only from_experiment is provided', () => {
    renderDSV(
      '/datasets/:name/source',
      '/datasets/my-dataset/source?path=src/vuln.py&from_experiment=exp-1',
    )

    expect(screen.queryByText('Back to run')).not.toBeInTheDocument()
  })
})

describe('DatasetSourceView — URL-param-driven highlight props', () => {
  it('passes line and end params to the viewer', async () => {
    const { default: MockViewer } = await import('../../components/DatasetSourceViewer')

    renderDSV(
      '/datasets/:name/source',
      '/datasets/my-dataset/source?path=src/foo.py&line=10&end=20',
    )

    // The viewer is rendered and receives numeric highlight props
    expect(screen.getByTestId('source-viewer')).toBeInTheDocument()
    const calls = vi.mocked(MockViewer).mock.calls
    expect(calls.length).toBeGreaterThan(0)
    expect(calls[0][0]).toMatchObject({ highlightStart: 10, highlightEnd: 20 })
  })
})
