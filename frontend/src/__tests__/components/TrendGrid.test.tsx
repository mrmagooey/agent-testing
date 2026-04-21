import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TrendGrid from '../../components/TrendGrid'
import type { TrendSeries } from '../../api/client'

// Recharts ResizeObserver not available in jsdom — mock ResponsiveContainer
vi.mock('recharts', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="responsive-container">{children}</div>
    ),
  }
})

// Mock useNavigate
const mockNavigate = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

function pt(f1: number, id: string) {
  return {
    experiment_id: id,
    completed_at: '2026-01-01',
    f1,
    precision: f1,
    recall: f1,
    cost_usd: 0,
    run_count: 1,
  }
}

function makeSeries(overrides: Partial<TrendSeries> = {}): TrendSeries {
  return {
    key: {
      model: 'gpt-4o',
      strategy: 'single_agent',
      tool_variant: 'with_tools',
      tool_extensions: [],
    },
    points: [pt(0.8, 'exp-aaa'), pt(0.82, 'exp-bbb'), pt(0.85, 'exp-ccc')],
    summary: {
      latest_f1: 0.85,
      prev_f1: 0.82,
      delta_f1: 0.03,
      trailing_median_f1: 0.81,
      is_regression: false,
    },
    ...overrides,
  }
}

function makeRegressionSeries(): TrendSeries {
  return makeSeries({
    points: [pt(0.85, 'exp-1'), pt(0.85, 'exp-2'), pt(0.74, 'exp-3')],
    summary: {
      latest_f1: 0.74,
      prev_f1: 0.85,
      delta_f1: -0.11,
      trailing_median_f1: 0.85,
      is_regression: true,
    },
  })
}

function renderGrid(series: TrendSeries[]) {
  return render(
    <MemoryRouter>
      <TrendGrid series={series} />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TrendGrid', () => {
  it('shows empty state when no series', () => {
    renderGrid([])
    expect(screen.getByText(/No trend data available/)).toBeInTheDocument()
  })

  it('renders one row per series', () => {
    const s1 = makeSeries()
    const s2 = makeSeries({
      key: { model: 'claude-3', strategy: 'per_file', tool_variant: 'without_tools', tool_extensions: [] },
    })
    renderGrid([s1, s2])
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('claude-3')).toBeInTheDocument()
  })

  it('renders a sparkline (ResponsiveContainer) per series', () => {
    renderGrid([makeSeries(), makeSeries({ key: { model: 'claude-3', strategy: 'single_agent', tool_variant: 'with_tools', tool_extensions: [] } })])
    // Each series has one ResponsiveContainer inside Sparkline
    const containers = screen.getAllByTestId('responsive-container')
    expect(containers.length).toBeGreaterThanOrEqual(2)
  })

  it('regression row has red background class', () => {
    renderGrid([makeRegressionSeries()])
    const grid = screen.getByTestId('trend-grid')
    // Find the row containing gpt-4o
    const rows = grid.querySelectorAll('tbody tr')
    expect(rows.length).toBe(1)
    // Regression row should have bg-red-50 or bg-red-950 class
    const rowClass = rows[0].className
    expect(rowClass).toMatch(/bg-red/)
  })

  it('non-regression row does not have red background', () => {
    renderGrid([makeSeries()])
    const grid = screen.getByTestId('trend-grid')
    const rows = grid.querySelectorAll('tbody tr')
    expect(rows[0].className).not.toMatch(/bg-red/)
  })

  it('regression badge is rendered for regression series', () => {
    renderGrid([makeRegressionSeries()])
    const badge = screen.getByLabelText('Regression detected')
    expect(badge).toBeInTheDocument()
  })

  it('sparse badge shown when series has < 3 points and is not regression', () => {
    const sparseSeries = makeSeries({
      points: [pt(0.8, 'exp-1'), pt(0.82, 'exp-2')],
      summary: {
        latest_f1: 0.82,
        prev_f1: 0.8,
        delta_f1: 0.02,
        trailing_median_f1: null,
        is_regression: false,
      },
    })
    renderGrid([sparseSeries])
    expect(screen.getByTitle('Insufficient history')).toBeInTheDocument()
  })

  it('shows latest F1 value in the row', () => {
    renderGrid([makeSeries()])
    expect(screen.getByText('0.850')).toBeInTheDocument()
  })

  it('shows positive delta in green', () => {
    renderGrid([makeSeries()])
    const deltaEl = screen.getByText('+0.030')
    expect(deltaEl.className).toMatch(/text-green/)
  })

  it('shows negative delta in red', () => {
    renderGrid([makeRegressionSeries()])
    // delta is -0.110 or -0.11
    const deltaEl = screen.getByText(/-0\.1/)
    expect(deltaEl.className).toMatch(/text-red/)
  })

  it('shows "none" for empty tool extensions', () => {
    renderGrid([makeSeries()])
    expect(screen.getByText('none')).toBeInTheDocument()
  })

  it('shows joined extension names when extensions present', () => {
    const s = makeSeries({
      key: { model: 'gpt-4o', strategy: 'single_agent', tool_variant: 'with_tools', tool_extensions: ['tree_sitter', 'lsp'] },
    })
    renderGrid([s])
    expect(screen.getByText('tree_sitter+lsp')).toBeInTheDocument()
  })
})
