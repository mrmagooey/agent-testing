import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import BenchmarkScorecardPanel from '../../components/BenchmarkScorecardPanel'
import { benchmarkScorecards, singleScorecard } from '../fixtures/benchmark_scorecard.fixture'
import type { BenchmarkScorecard } from '../../api/client'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function renderPanel(scorecards?: BenchmarkScorecard[]) {
  return render(<BenchmarkScorecardPanel scorecards={scorecards} />)
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('BenchmarkScorecardPanel — hidden when no scorecards', () => {
  it('renders nothing when scorecards prop is undefined', () => {
    const { container } = renderPanel(undefined)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when scorecards is an empty array', () => {
    const { container } = renderPanel([])
    expect(container.firstChild).toBeNull()
  })
})

describe('BenchmarkScorecardPanel — section heading', () => {
  it('renders the Benchmark Scorecard heading', () => {
    renderPanel(singleScorecard)
    expect(screen.getByRole('heading', { name: /Benchmark Scorecard/i })).toBeInTheDocument()
  })

  it('shows the dataset name', () => {
    renderPanel(singleScorecard)
    expect(screen.getByText('BenchmarkPython-1.0')).toBeInTheDocument()
  })

  it('shows all dataset names when multiple scorecards given', () => {
    renderPanel(benchmarkScorecards)
    expect(screen.getByText('BenchmarkPython-1.0')).toBeInTheDocument()
    expect(screen.getByText('BenchmarkPython-2.0-beta')).toBeInTheDocument()
  })
})

describe('BenchmarkScorecardPanel — headline OWASP score', () => {
  it('displays aggregate owasp_score formatted as a percentage', () => {
    renderPanel(singleScorecard)
    // aggregate owasp_score = 0.522 → "52.2%"
    expect(screen.getByTestId('owasp-headline')).toHaveTextContent('52.2%')
  })
})

describe('BenchmarkScorecardPanel — per-CWE table', () => {
  it('renders table column headers', () => {
    renderPanel(singleScorecard)
    expect(screen.getByText('CWE')).toBeInTheDocument()
    expect(screen.getByText('TP')).toBeInTheDocument()
    expect(screen.getByText('FP')).toBeInTheDocument()
    expect(screen.getByText('TN')).toBeInTheDocument()
    expect(screen.getByText('FN')).toBeInTheDocument()
    expect(screen.getByText('Precision')).toBeInTheDocument()
    expect(screen.getByText('Recall')).toBeInTheDocument()
    expect(screen.getByText('F1')).toBeInTheDocument()
    expect(screen.getByText('FP-rate')).toBeInTheDocument()
    expect(screen.getByText('OWASP Score')).toBeInTheDocument()
  })

  it('renders all CWE rows from fixture', () => {
    renderPanel(singleScorecard)
    expect(screen.getByText('CWE-89')).toBeInTheDocument()
    expect(screen.getByText('CWE-22')).toBeInTheDocument()
    expect(screen.getByText('CWE-79')).toBeInTheDocument()
    expect(screen.getByText('CWE-78')).toBeInTheDocument()
  })

  it('renders an Aggregate row', () => {
    renderPanel(singleScorecard)
    expect(screen.getByText('Aggregate')).toBeInTheDocument()
  })
})

describe('BenchmarkScorecardPanel — warning badge', () => {
  it('shows warning icon on rows where warning is non-null', () => {
    renderPanel(singleScorecard)
    // CWE-22 has warning = 'insufficient sample size (n<25 per polarity)'
    const warnings = screen.getAllByRole('img', { name: 'warning' })
    expect(warnings.length).toBeGreaterThanOrEqual(1)
  })

  it('warning icon has the correct title attribute', () => {
    renderPanel(singleScorecard)
    const warnings = screen.getAllByRole('img', { name: 'warning' })
    // At least one should match the CWE-22 warning
    const titles = warnings.map((el) => el.getAttribute('title'))
    expect(titles).toContain('insufficient sample size (n<25 per polarity)')
  })

  it('does not show warning icon on rows where warning is null', () => {
    // Single scorecard with only a warning-free row
    const noWarnScorecard: BenchmarkScorecard[] = [
      {
        dataset_name: 'CleanDataset',
        per_cwe: [
          {
            cwe_id: 'CWE-89',
            tp: 38,
            fp: 4,
            tn: 46,
            fn: 12,
            precision: 0.905,
            recall: 0.76,
            f1: 0.826,
            fp_rate: 0.08,
            owasp_score: 0.68,
            warning: null,
          },
        ],
        aggregate: {
          tp: 38,
          fp: 4,
          tn: 46,
          fn: 12,
          precision: 0.905,
          recall: 0.76,
          f1: 0.826,
          fp_rate: 0.08,
          owasp_score: 0.68,
          warning: null,
        },
      },
    ]
    renderPanel(noWarnScorecard)
    expect(screen.queryAllByRole('img', { name: 'warning' })).toHaveLength(0)
  })
})

describe('BenchmarkScorecardPanel — null metric rendering', () => {
  it('renders em-dash for null precision', () => {
    renderPanel(singleScorecard)
    // CWE-22 has precision: null — there should be em-dashes present
    // Multiple cells will show '—' so just check it exists
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThan(0)
  })
})

describe('BenchmarkScorecardPanel — collapsible sections', () => {
  it('table is visible by default (expanded)', () => {
    renderPanel(singleScorecard)
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('collapses the table when the header button is clicked', () => {
    renderPanel(singleScorecard)
    const headerBtn = screen.getByRole('button', { name: /BenchmarkPython-1\.0/ })
    fireEvent.click(headerBtn)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('re-expands the table when the header button is clicked again', () => {
    renderPanel(singleScorecard)
    const headerBtn = screen.getByRole('button', { name: /BenchmarkPython-1\.0/ })
    fireEvent.click(headerBtn) // collapse
    fireEvent.click(headerBtn) // expand
    expect(screen.getByRole('table')).toBeInTheDocument()
  })
})
