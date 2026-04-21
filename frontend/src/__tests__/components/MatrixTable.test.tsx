import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import MatrixTable from '../../components/MatrixTable'
import type { Run } from '../../api/client'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    run_id: 'r1',
    experiment_id: 'exp1',
    model: 'claude-3-5-sonnet',
    strategy: 'basic',
    tool_variant: 'none',
    profile: 'default',
    verification: 'none',
    status: 'completed',
    precision: 0.85,
    recall: 0.75,
    f1: 0.80,
    ...overrides,
  }
}

function renderTable(runs: Run[]) {
  return render(
    <MemoryRouter>
      <MatrixTable runs={runs} />
    </MemoryRouter>,
  )
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('MatrixTable', () => {
  it('renders a table row for each run provided', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
      makeRun({ run_id: 'r3', model: 'gemini-1.5-pro' }),
    ]
    renderTable(runs)

    // One tbody row per run
    const rows = screen.getAllByRole('row')
    // rows includes the header row
    expect(rows.length).toBe(runs.length + 1)
  })

  it('displays model, precision, recall, and F1 column headers', () => {
    renderTable([makeRun()])

    expect(screen.getByRole('columnheader', { name: /model/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /prec/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /recall/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /f1/i })).toBeInTheDocument()
  })

  it('renders a "No runs yet" message when the runs array is empty', () => {
    renderTable([])

    expect(screen.getByText(/no runs yet/i)).toBeInTheDocument()
  })

  it('renders an em-dash for metric columns that have no value', () => {
    const run = makeRun({ precision: undefined, recall: undefined, f1: undefined })
    renderTable([run])

    // metricCell returns '—' for undefined values; expect at least one dash
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(3)
  })

  it('does not show Ext column when no runs have tool_extensions', () => {
    const runs = [
      makeRun({ run_id: 'r1', tool_extensions: undefined }),
      makeRun({ run_id: 'r2', tool_extensions: undefined }),
    ]
    renderTable(runs)

    // Should not have 'Ext' header when all runs have undefined extensions
    const extHeaders = screen.queryAllByRole('columnheader', { name: /ext/i })
    expect(extHeaders.length).toBe(0)
  })

  it('shows Ext column when at least one run has tool_extensions', () => {
    const runs = [
      makeRun({ run_id: 'r1', tool_extensions: ['lsp'] }),
      makeRun({ run_id: 'r2', tool_extensions: undefined }),
    ]
    renderTable(runs)

    // Should have 'Ext' header when at least one run has extensions
    const extHeader = screen.getByRole('columnheader', { name: /ext/i })
    expect(extHeader).toBeInTheDocument()
  })

  it('renders tool_extensions as badges', () => {
    const runs = [
      makeRun({ run_id: 'r1', tool_extensions: ['lsp', 'tree_sitter'] }),
    ]
    renderTable(runs)

    // Should show both extension badges
    expect(screen.getByText('lsp')).toBeInTheDocument()
    expect(screen.getByText('tree_sitter')).toBeInTheDocument()
  })

  it('does not show Ext column when tool_extensions is empty array', () => {
    const runs = [
      makeRun({ run_id: 'r1', tool_extensions: [] }),
    ]
    renderTable(runs)

    // Should not show the Ext column because the array is empty (length === 0)
    const extHeaders = screen.queryAllByRole('columnheader', { name: /ext/i })
    expect(extHeaders.length).toBe(0)
  })
})
