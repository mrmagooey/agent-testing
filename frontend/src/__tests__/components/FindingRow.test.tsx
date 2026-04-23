import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import FindingRow from '../../components/FindingRow'
import type { Finding } from '../../api/client'
import type { GlobalFinding } from '../../api/client'

// ─── Mock CodeViewer (uses CodeMirror) ────────────────────────────────────────

vi.mock('../../components/CodeViewer', () => ({
  default: ({ content }: { content: string }) => (
    <div data-testid="code-viewer">{content}</div>
  ),
}))

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeExperimentFinding(overrides: Partial<Finding & { finding_id?: string }> = {}): Finding & { finding_id?: string } {
  return {
    finding_id: 'f-001',
    run_id: 'r-001',
    experiment_id: 'e1',
    title: 'SQL Injection',
    description: 'Query built from user input',
    vuln_class: 'sqli',
    severity: 'high',
    match_status: 'tp',
    file_path: 'app/views.py',
    line_start: 42,
    ...overrides,
  }
}

function makeGlobalFinding(overrides: Partial<GlobalFinding & { finding_id?: string }> = {}): GlobalFinding & { finding_id?: string } {
  return {
    finding_id: 'f-002',
    run_id: 'r-002',
    experiment_id: 'e1',
    experiment_name: 'Experiment Alpha',
    title: 'XSS Vulnerability',
    description: 'Unsanitized output',
    vuln_class: 'xss',
    severity: 'medium',
    match_status: 'fp',
    file_path: 'templates/index.html',
    line_start: 10,
    model_id: 'claude-3-5-sonnet',
    strategy: 'basic',
    dataset_name: 'web-vulns',
    created_at: '2024-01-15T00:00:00Z',
    ...overrides,
  }
}

function renderExperiment(
  props: Partial<Parameters<typeof FindingRow>[0]> & {
    finding?: Finding & { finding_id?: string }
    expanded?: boolean
    onToggle?: () => void
  } = {},
) {
  const finding = props.finding ?? makeExperimentFinding()
  const expanded = props.expanded ?? false
  const onToggle = props.onToggle ?? vi.fn()
  return render(
    <MemoryRouter>
      <table>
        <tbody>
          <FindingRow
            scope="experiment"
            finding={finding}
            experimentId="e1"
            expanded={expanded}
            onToggle={onToggle}
          />
        </tbody>
      </table>
    </MemoryRouter>,
  )
}

function renderGlobal(
  finding: GlobalFinding & { finding_id?: string } = makeGlobalFinding(),
  expanded = false,
  onToggle = vi.fn(),
) {
  return render(
    <MemoryRouter>
      <table>
        <tbody>
          <FindingRow
            scope="global"
            finding={finding}
            expanded={expanded}
            onToggle={onToggle}
          />
        </tbody>
      </table>
    </MemoryRouter>,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('FindingRow — experiment scope', () => {
  it('renders finding title', () => {
    renderExperiment()
    expect(screen.getByText('SQL Injection')).toBeInTheDocument()
  })

  it('renders match_status badge', () => {
    renderExperiment()
    expect(screen.getByText('tp')).toBeInTheDocument()
  })

  it('renders severity badge', () => {
    renderExperiment()
    expect(screen.getByText('high')).toBeInTheDocument()
  })

  it('renders vuln_class', () => {
    renderExperiment()
    expect(screen.getByText('sqli')).toBeInTheDocument()
  })

  it('renders file_path', () => {
    renderExperiment()
    expect(screen.getByText('app/views.py')).toBeInTheDocument()
  })

  it('renders line_start', () => {
    renderExperiment()
    expect(screen.getByText('42')).toBeInTheDocument()
  })

  it('renders em-dash for missing file_path', () => {
    renderExperiment({ finding: makeExperimentFinding({ file_path: undefined }) })
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('renders em-dash for missing line_start', () => {
    renderExperiment({ finding: makeExperimentFinding({ line_start: undefined }) })
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(1)
  })

  it('renders em-dash for missing match_status', () => {
    renderExperiment({ finding: makeExperimentFinding({ match_status: undefined as unknown as string }) })
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(1)
  })

  it('calls onToggle when row is clicked', () => {
    const onToggle = vi.fn()
    renderExperiment({ onToggle })
    fireEvent.click(screen.getByText('SQL Injection').closest('tr')!)
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('does not show expanded content when expanded=false', () => {
    renderExperiment({ expanded: false })
    expect(screen.queryByTestId('code-viewer')).not.toBeInTheDocument()
  })

  it('shows CodeViewer with description when expanded=true', () => {
    renderExperiment({ expanded: true })
    expect(screen.getByTestId('code-viewer')).toBeInTheDocument()
    expect(screen.getByText('Query built from user input')).toBeInTheDocument()
  })

  it('shows matched_label_id when expanded and field is present', () => {
    const finding = makeExperimentFinding({ matched_label_id: 'label-abc' } as Finding & { finding_id?: string; matched_label_id?: string })
    renderExperiment({ finding, expanded: true })
    expect(screen.getByText('label-abc')).toBeInTheDocument()
  })

  it('renders different severity variants', () => {
    for (const severity of ['critical', 'medium', 'low', 'info']) {
      const { unmount } = renderExperiment({ finding: makeExperimentFinding({ severity }) })
      expect(screen.getByText(severity)).toBeInTheDocument()
      unmount()
    }
  })

  it('renders different match_status variants', () => {
    for (const match_status of ['tp', 'fp', 'fn', 'unlabeled_real']) {
      const { unmount } = renderExperiment({ finding: makeExperimentFinding({ match_status }) })
      expect(screen.getByText(match_status)).toBeInTheDocument()
      unmount()
    }
  })
})

describe('FindingRow — global scope', () => {
  it('renders finding title', () => {
    renderGlobal()
    expect(screen.getByText('XSS Vulnerability')).toBeInTheDocument()
  })

  it('renders experiment_name as a link when present', () => {
    renderGlobal()
    expect(screen.getByText('Experiment Alpha')).toBeInTheDocument()
  })

  it('renders model_id', () => {
    renderGlobal()
    expect(screen.getByText('claude-3-5-sonnet')).toBeInTheDocument()
  })

  it('renders strategy', () => {
    renderGlobal()
    expect(screen.getByText('basic')).toBeInTheDocument()
  })

  it('renders em-dash for missing experiment_id', () => {
    const finding = makeGlobalFinding({ experiment_id: '' })
    renderGlobal(finding)
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(1)
  })

  it('renders em-dash for missing model_id', () => {
    const finding = makeGlobalFinding({ model_id: undefined as unknown as string })
    renderGlobal(finding)
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(1)
  })

  it('shows "Open run" link when expanded and run_id present', () => {
    renderGlobal(makeGlobalFinding(), true)
    expect(screen.getByText('Open run')).toBeInTheDocument()
  })

  it('shows "View source" link when expanded, dataset_name and file_path present', () => {
    renderGlobal(makeGlobalFinding({ dataset_name: 'web-vulns', file_path: 'templates/index.html' }), true)
    expect(screen.getByText('View source')).toBeInTheDocument()
  })

  it('shows cwe_ids when expanded and field present', () => {
    const finding = makeGlobalFinding({ cwe_ids: ['CWE-79', 'CWE-89'] })
    renderGlobal(finding, true)
    expect(screen.getByText('CWE-79')).toBeInTheDocument()
    expect(screen.getByText('CWE-89')).toBeInTheDocument()
  })

  it('calls onToggle when row clicked', () => {
    const onToggle = vi.fn()
    renderGlobal(makeGlobalFinding(), false, onToggle)
    fireEvent.click(screen.getByText('XSS Vulnerability').closest('tr')!)
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('does not show expanded content when expanded=false', () => {
    renderGlobal(makeGlobalFinding(), false)
    expect(screen.queryByTestId('code-viewer')).not.toBeInTheDocument()
  })

  it('shows CodeViewer with description when expanded=true', () => {
    renderGlobal(makeGlobalFinding(), true)
    expect(screen.getByTestId('code-viewer')).toBeInTheDocument()
  })
})
