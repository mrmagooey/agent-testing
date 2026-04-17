import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import FindingsExplorer from '../../components/FindingsExplorer'
import type { Finding } from '../../api/client'

// ─── Mock API client and CodeViewer ─────────────────────────────────────────

vi.mock('../../api/client', () => ({
  searchFindings: vi.fn().mockResolvedValue([]),
  reclassifyFinding: vi.fn().mockResolvedValue(undefined),
}))

// CodeViewer uses CodeMirror which isn't available in jsdom
vi.mock('../../components/CodeViewer', () => ({
  default: ({ content }: { content: string }) => <div data-testid="code-viewer">{content}</div>,
}))

import { reclassifyFinding } from '../../api/client'
const mockReclassify = vi.mocked(reclassifyFinding)

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeFinding(overrides: Partial<Finding> = {}): Finding {
  return {
    finding_id: 'f-001',
    run_id: 'r-001',
    batch_id: 'b1',
    title: 'SQL Injection',
    description: 'SQL query built from user input',
    vuln_class: 'sqli',
    severity: 'high',
    match_status: 'tp',
    file_path: 'app/views.py',
    line_start: 42,
    ...overrides,
  }
}

// ─── Setup ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.runAllTimers()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('FindingsExplorer', () => {
  it('renders the findings count', () => {
    const findings = [makeFinding({ finding_id: 'f1' }), makeFinding({ finding_id: 'f2' })]
    render(<FindingsExplorer batchId="b1" findings={findings} />)
    expect(screen.getByText(/2 findings/i)).toBeInTheDocument()
  })

  it('shows "No findings match" message when list is empty', () => {
    render(<FindingsExplorer batchId="b1" findings={[]} />)
    expect(screen.getByText(/no findings match/i)).toBeInTheDocument()
  })

  it('renders finding title in a table row', () => {
    render(<FindingsExplorer batchId="b1" findings={[makeFinding()]} />)
    expect(screen.getByText('SQL Injection')).toBeInTheDocument()
  })

  it('renders match status badge', () => {
    render(<FindingsExplorer batchId="b1" findings={[makeFinding({ match_status: 'fp' })]} />)
    expect(screen.getByText('fp')).toBeInTheDocument()
  })

  it('renders severity badge in the table', () => {
    render(<FindingsExplorer batchId="b1" findings={[makeFinding({ severity: 'critical' })]} />)
    // getAllByText since 'critical' also appears in the severity dropdown
    const items = screen.getAllByText('critical')
    expect(items.length).toBeGreaterThanOrEqual(1)
  })

  it('renders file path in row', () => {
    render(<FindingsExplorer batchId="b1" findings={[makeFinding()]} />)
    expect(screen.getByText('app/views.py')).toBeInTheDocument()
  })

  it('renders dash for missing file_path', () => {
    const finding = makeFinding({ file_path: undefined })
    render(<FindingsExplorer batchId="b1" findings={[finding]} />)
    // Should show '—' for missing file path
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(1)
  })

  it('renders line_start in row', () => {
    render(<FindingsExplorer batchId="b1" findings={[makeFinding({ line_start: 99 })]} />)
    expect(screen.getByText('99')).toBeInTheDocument()
  })

  it('filters by match status', () => {
    const findings = [
      makeFinding({ finding_id: 'f1', title: 'TP Finding', match_status: 'tp' }),
      makeFinding({ finding_id: 'f2', title: 'FP Finding', match_status: 'fp' }),
    ]
    render(<FindingsExplorer batchId="b1" findings={findings} />)

    const statusSelect = screen.getAllByRole('combobox')[0]
    fireEvent.change(statusSelect, { target: { value: 'fp' } })

    expect(screen.getByText('FP Finding')).toBeInTheDocument()
    expect(screen.queryByText('TP Finding')).not.toBeInTheDocument()
  })

  it('filters by severity', () => {
    const findings = [
      makeFinding({ finding_id: 'f1', title: 'High Finding', severity: 'high' }),
      makeFinding({ finding_id: 'f2', title: 'Low Finding', severity: 'low' }),
    ]
    render(<FindingsExplorer batchId="b1" findings={findings} />)

    const severitySelect = screen.getAllByRole('combobox')[2]
    fireEvent.change(severitySelect, { target: { value: 'high' } })

    expect(screen.getByText('High Finding')).toBeInTheDocument()
    expect(screen.queryByText('Low Finding')).not.toBeInTheDocument()
  })

  it('expands finding on row click to show description', async () => {
    render(<FindingsExplorer batchId="b1" findings={[makeFinding()]} />)

    const row = screen.getByText('SQL Injection').closest('tr')!
    await act(async () => {
      fireEvent.click(row)
    })

    expect(screen.getByTestId('code-viewer')).toBeInTheDocument()
  })

  it('collapses finding on second click', async () => {
    render(<FindingsExplorer batchId="b1" findings={[makeFinding()]} />)

    const row = screen.getByText('SQL Injection').closest('tr')!
    await act(async () => { fireEvent.click(row) })
    expect(screen.getByTestId('code-viewer')).toBeInTheDocument()

    await act(async () => { fireEvent.click(row) })
    expect(screen.queryByTestId('code-viewer')).not.toBeInTheDocument()
  })

  it('shows reclassify button for FP findings when expanded', async () => {
    render(<FindingsExplorer batchId="b1" findings={[makeFinding({ match_status: 'fp' })]} />)

    const row = screen.getByText('SQL Injection').closest('tr')!
    await act(async () => {
      fireEvent.click(row)
    })

    expect(screen.getByText(/reclassify as unlabeled real/i)).toBeInTheDocument()
  })

  it('does not show reclassify button for TP findings', async () => {
    render(<FindingsExplorer batchId="b1" findings={[makeFinding({ match_status: 'tp' })]} />)

    const row = screen.getByText('SQL Injection').closest('tr')!
    await act(async () => {
      fireEvent.click(row)
    })

    expect(screen.queryByText(/reclassify as unlabeled real/i)).not.toBeInTheDocument()
  })
})
