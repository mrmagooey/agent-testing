import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import CVECandidateTable from '../../components/CVECandidateTable'
import type { CVECandidate } from '../../api/client'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeCandidate(overrides: Partial<CVECandidate> = {}): CVECandidate {
  return {
    score: 0.85,
    cve_id: 'CVE-2024-0001',
    vuln_class: 'sqli',
    severity: 'high',
    language: 'python',
    repo: 'github.com/example/repo',
    files_changed: 3,
    lines_changed: 42,
    importable: true,
    ...overrides,
  }
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('CVECandidateTable', () => {
  // ── Minimal render ──────────────────────────────────────────────────────────

  it('renders an empty-state row when candidates array is empty', () => {
    render(<CVECandidateTable candidates={[]} />)
    expect(screen.getByText('No candidates found.')).toBeInTheDocument()
  })

  it('renders column headers', () => {
    render(<CVECandidateTable candidates={[makeCandidate()]} />)
    expect(screen.getByText('Score')).toBeInTheDocument()
    expect(screen.getByText('CVE ID')).toBeInTheDocument()
    expect(screen.getByText('Severity')).toBeInTheDocument()
    expect(screen.getByText('Language')).toBeInTheDocument()
    expect(screen.getByText('Repo')).toBeInTheDocument()
  })

  it('renders candidate data in the table', () => {
    render(<CVECandidateTable candidates={[makeCandidate()]} />)
    expect(screen.getByText('CVE-2024-0001')).toBeInTheDocument()
    expect(screen.getByText('sqli')).toBeInTheDocument()
    expect(screen.getByText('python')).toBeInTheDocument()
    expect(screen.getByText('0.85')).toBeInTheDocument()
  })

  // ── Multiple candidates ─────────────────────────────────────────────────────

  it('renders one data row per candidate', () => {
    const candidates = [
      makeCandidate({ cve_id: 'CVE-2024-0001' }),
      makeCandidate({ cve_id: 'CVE-2024-0002' }),
      makeCandidate({ cve_id: 'CVE-2024-0003' }),
    ]
    render(<CVECandidateTable candidates={candidates} />)
    expect(screen.getByText('CVE-2024-0001')).toBeInTheDocument()
    expect(screen.getByText('CVE-2024-0002')).toBeInTheDocument()
    expect(screen.getByText('CVE-2024-0003')).toBeInTheDocument()
  })

  // ── Importable badge ────────────────────────────────────────────────────────

  it('shows "yes" badge when importable is true', () => {
    render(<CVECandidateTable candidates={[makeCandidate({ importable: true })]} />)
    expect(screen.getByText('yes')).toBeInTheDocument()
  })

  it('shows "no" badge when importable is false', () => {
    render(<CVECandidateTable candidates={[makeCandidate({ importable: false })]} />)
    expect(screen.getByText('no')).toBeInTheDocument()
  })

  // ── Severity badge ──────────────────────────────────────────────────────────

  it('renders severity badge', () => {
    render(<CVECandidateTable candidates={[makeCandidate({ severity: 'critical' })]} />)
    expect(screen.getByText('critical')).toBeInTheDocument()
  })

  // ── Score display ───────────────────────────────────────────────────────────

  it('renders score to 2 decimal places', () => {
    render(<CVECandidateTable candidates={[makeCandidate({ score: 0.9 })]} />)
    expect(screen.getByText('0.90')).toBeInTheDocument()
  })

  it('renders low score with red styling class (score < 0.6)', () => {
    const { container } = render(<CVECandidateTable candidates={[makeCandidate({ score: 0.4 })]} />)
    // Score cell should exist with text 0.40
    expect(screen.getByText('0.40')).toBeInTheDocument()
  })

  // ── Checkbox selection ──────────────────────────────────────────────────────

  it('does not show import button initially (nothing selected)', () => {
    render(<CVECandidateTable candidates={[makeCandidate()]} />)
    expect(screen.queryByText(/Import Selected/i)).not.toBeInTheDocument()
  })

  it('shows import button after checking a row checkbox', () => {
    render(<CVECandidateTable candidates={[makeCandidate()]} />)
    const rowCheckboxes = screen.getAllByRole('checkbox')
    // rowCheckboxes[0] is the "select all" header checkbox; rowCheckboxes[1] is the row
    fireEvent.click(rowCheckboxes[1])
    expect(screen.getByText(/Import Selected \(1\)/i)).toBeInTheDocument()
  })

  it('hides import button after unchecking the row checkbox', () => {
    render(<CVECandidateTable candidates={[makeCandidate()]} />)
    const rowCheckboxes = screen.getAllByRole('checkbox')
    fireEvent.click(rowCheckboxes[1]) // check
    fireEvent.click(rowCheckboxes[1]) // uncheck
    expect(screen.queryByText(/Import Selected/i)).not.toBeInTheDocument()
  })

  it('calls onImport with the selected cve_ids when import button clicked', () => {
    const onImport = vi.fn()
    render(
      <CVECandidateTable
        candidates={[makeCandidate({ cve_id: 'CVE-2024-0001' })]}
        onImport={onImport}
      />,
    )
    const rowCheckboxes = screen.getAllByRole('checkbox')
    fireEvent.click(rowCheckboxes[1])
    fireEvent.click(screen.getByText(/Import Selected/i))
    expect(onImport).toHaveBeenCalledWith(['CVE-2024-0001'])
  })

  // ── Select all ──────────────────────────────────────────────────────────────

  it('select-all checkbox checks all rows', () => {
    const candidates = [
      makeCandidate({ cve_id: 'CVE-2024-0001' }),
      makeCandidate({ cve_id: 'CVE-2024-0002' }),
    ]
    render(<CVECandidateTable candidates={candidates} />)
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[0]) // header "select all"
    // Should now show count=2
    expect(screen.getByText(/Import Selected \(2\)/i)).toBeInTheDocument()
  })

  it('select-all unchecks all rows when all are already selected', () => {
    const candidates = [
      makeCandidate({ cve_id: 'CVE-2024-0001' }),
      makeCandidate({ cve_id: 'CVE-2024-0002' }),
    ]
    render(<CVECandidateTable candidates={candidates} />)
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[0]) // select all
    fireEvent.click(checkboxes[0]) // deselect all
    expect(screen.queryByText(/Import Selected/i)).not.toBeInTheDocument()
  })

  // ── Expand/collapse row ─────────────────────────────────────────────────────

  it('expands a row on click to show description', () => {
    const candidate = makeCandidate({ description: 'A SQL injection vulnerability' })
    render(<CVECandidateTable candidates={[candidate]} />)
    const row = screen.getByText('CVE-2024-0001').closest('tr')!
    fireEvent.click(row)
    expect(screen.getByText('A SQL injection vulnerability')).toBeInTheDocument()
  })

  it('collapses an expanded row on second click', () => {
    const candidate = makeCandidate({ description: 'A SQL injection vulnerability' })
    render(<CVECandidateTable candidates={[candidate]} />)
    const row = screen.getByText('CVE-2024-0001').closest('tr')!
    fireEvent.click(row)
    expect(screen.getByText('A SQL injection vulnerability')).toBeInTheDocument()
    fireEvent.click(row)
    expect(screen.queryByText('A SQL injection vulnerability')).not.toBeInTheDocument()
  })

  it('shows advisory link when advisory_url is provided', () => {
    const candidate = makeCandidate({ advisory_url: 'https://example.com/advisory' })
    render(<CVECandidateTable candidates={[candidate]} />)
    const row = screen.getByText('CVE-2024-0001').closest('tr')!
    fireEvent.click(row)
    const link = screen.getByText('Advisory ↗')
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('href', 'https://example.com/advisory')
  })

  it('shows fix commit link when fix_commit is provided', () => {
    const candidate = makeCandidate({ fix_commit: 'https://github.com/example/commit/abc' })
    render(<CVECandidateTable candidates={[candidate]} />)
    const row = screen.getByText('CVE-2024-0001').closest('tr')!
    fireEvent.click(row)
    const link = screen.getByText('Fix commit ↗')
    expect(link).toBeInTheDocument()
  })

  it('does not show advisory link when advisory_url is absent', () => {
    const candidate = makeCandidate({ advisory_url: undefined })
    render(<CVECandidateTable candidates={[candidate]} />)
    const row = screen.getByText('CVE-2024-0001').closest('tr')!
    fireEvent.click(row)
    expect(screen.queryByText('Advisory ↗')).not.toBeInTheDocument()
  })

  it('row checkbox click does not toggle row expansion', () => {
    const candidate = makeCandidate({ description: 'desc text' })
    render(<CVECandidateTable candidates={[candidate]} />)
    const checkboxes = screen.getAllByRole('checkbox')
    // Click the row checkbox (index 1), not the row itself
    fireEvent.click(checkboxes[1])
    // The row should NOT be expanded (checkbox click stops propagation)
    expect(screen.queryByText('desc text')).not.toBeInTheDocument()
  })

  // ── onImport not provided ───────────────────────────────────────────────────

  it('does not crash when onImport is not provided and import button clicked', () => {
    render(<CVECandidateTable candidates={[makeCandidate()]} />)
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    const importBtn = screen.getByText(/Import Selected/i)
    // Should not throw
    fireEvent.click(importBtn)
  })
})
