import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import FindingsFilterBar from '../../components/FindingsFilterBar'
import type { FindingFacets } from '../../api/client'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeFacets(overrides: Partial<FindingFacets> = {}): FindingFacets {
  return {
    vuln_class: {},
    severity: {},
    match_status: {},
    model_id: {},
    strategy: {},
    dataset_name: {},
    ...overrides,
  }
}

function makeActiveFilters(overrides: Partial<{
  vuln_class: string[]
  severity: string[]
  match_status: string[]
  model_id: string[]
  strategy: string[]
  dataset_name: string[]
  created_from: string
  created_to: string
}> = {}) {
  return {
    vuln_class: [],
    severity: [],
    match_status: [],
    model_id: [],
    strategy: [],
    dataset_name: [],
    created_from: '',
    created_to: '',
    ...overrides,
  }
}

type RenderProps = {
  facets?: FindingFacets
  activeFilters?: ReturnType<typeof makeActiveFilters>
  onFilterChange?: (key: string, values: string[]) => void
  onDateChange?: (key: 'created_from' | 'created_to', value: string) => void
  onClearAll?: () => void
}

function renderBar({
  facets = makeFacets(),
  activeFilters = makeActiveFilters(),
  onFilterChange = vi.fn(),
  onDateChange = vi.fn(),
  onClearAll = vi.fn(),
}: RenderProps = {}) {
  return render(
    <FindingsFilterBar
      facets={facets}
      activeFilters={activeFilters}
      onFilterChange={onFilterChange}
      onDateChange={onDateChange}
      onClearAll={onClearAll}
    />,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('FindingsFilterBar', () => {
  // ── Minimal render ──────────────────────────────────────────────────────────

  it('renders the "Filters" heading', () => {
    renderBar()
    expect(screen.getByText('Filters')).toBeInTheDocument()
  })

  it('renders Date Range section', () => {
    renderBar()
    expect(screen.getByText('Date Range')).toBeInTheDocument()
  })

  it('renders "Created from" date input', () => {
    renderBar()
    expect(screen.getByLabelText('Created from')).toBeInTheDocument()
  })

  it('renders "Created to" date input', () => {
    renderBar()
    expect(screen.getByLabelText('Created to')).toBeInTheDocument()
  })

  // ── Clear all button visibility ─────────────────────────────────────────────

  it('does not show "Clear all" when no filters are active', () => {
    renderBar()
    expect(screen.queryByText('Clear all')).not.toBeInTheDocument()
  })

  it('shows "Clear all" when a vuln_class filter is active', () => {
    renderBar({ activeFilters: makeActiveFilters({ vuln_class: ['sqli'] }) })
    expect(screen.getByText('Clear all')).toBeInTheDocument()
  })

  it('shows "Clear all" when a date filter is active', () => {
    renderBar({ activeFilters: makeActiveFilters({ created_from: '2024-01-01' }) })
    expect(screen.getByText('Clear all')).toBeInTheDocument()
  })

  it('calls onClearAll when "Clear all" is clicked', () => {
    const onClearAll = vi.fn()
    renderBar({
      activeFilters: makeActiveFilters({ severity: ['high'] }),
      onClearAll,
    })
    fireEvent.click(screen.getByText('Clear all'))
    expect(onClearAll).toHaveBeenCalledTimes(1)
  })

  // ── FacetGroup rendering ────────────────────────────────────────────────────

  it('renders Vuln Class section label when facets present', () => {
    const facets = makeFacets({ vuln_class: { sqli: 5, xss: 3 } })
    renderBar({ facets })
    expect(screen.getByText('Vuln Class')).toBeInTheDocument()
  })

  it('renders filter chips for vuln_class facets', () => {
    const facets = makeFacets({ vuln_class: { sqli: 5, xss: 3 } })
    renderBar({ facets })
    expect(screen.getByText('sqli')).toBeInTheDocument()
    expect(screen.getByText('xss')).toBeInTheDocument()
  })

  it('renders facet count alongside chip label', () => {
    const facets = makeFacets({ severity: { high: 12 } })
    renderBar({ facets })
    expect(screen.getByText('12')).toBeInTheDocument()
  })

  it('does not render a FacetGroup for empty facet options', () => {
    // vuln_class is empty — should not render "Vuln Class" heading
    const facets = makeFacets({ vuln_class: {}, severity: { high: 1 } })
    renderBar({ facets })
    expect(screen.queryByText('Vuln Class')).not.toBeInTheDocument()
    expect(screen.getByText('Severity')).toBeInTheDocument()
  })

  it('renders all six facet group labels when all have options', () => {
    const facets: FindingFacets = {
      vuln_class: { sqli: 1 },
      severity: { high: 1 },
      match_status: { tp: 1 },
      model_id: { 'claude-3-5-sonnet': 1 },
      strategy: { basic: 1 },
      dataset_name: { 'my-dataset': 1 },
    }
    renderBar({ facets })
    expect(screen.getByText('Vuln Class')).toBeInTheDocument()
    expect(screen.getByText('Severity')).toBeInTheDocument()
    expect(screen.getByText('Status')).toBeInTheDocument()
    expect(screen.getByText('Model')).toBeInTheDocument()
    expect(screen.getByText('Strategy')).toBeInTheDocument()
    expect(screen.getByText('Dataset')).toBeInTheDocument()
  })

  // ── Toggle filter chip ──────────────────────────────────────────────────────

  it('calls onFilterChange with added value when inactive chip clicked', () => {
    const onFilterChange = vi.fn()
    const facets = makeFacets({ severity: { high: 5 } })
    renderBar({ facets, activeFilters: makeActiveFilters(), onFilterChange })
    fireEvent.click(screen.getByText('high'))
    expect(onFilterChange).toHaveBeenCalledWith('severity', ['high'])
  })

  it('calls onFilterChange with removed value when active chip clicked', () => {
    const onFilterChange = vi.fn()
    const facets = makeFacets({ severity: { high: 5, low: 2 } })
    renderBar({
      facets,
      activeFilters: makeActiveFilters({ severity: ['high'] }),
      onFilterChange,
    })
    // Click the already-active 'high' chip to deselect
    fireEvent.click(screen.getByTestId('filter-chip-severity-high'))
    expect(onFilterChange).toHaveBeenCalledWith('severity', [])
  })

  it('adds new value to existing selection when another chip clicked', () => {
    const onFilterChange = vi.fn()
    const facets = makeFacets({ severity: { high: 5, low: 2 } })
    renderBar({
      facets,
      activeFilters: makeActiveFilters({ severity: ['high'] }),
      onFilterChange,
    })
    fireEvent.click(screen.getByTestId('filter-chip-severity-low'))
    expect(onFilterChange).toHaveBeenCalledWith('severity', ['high', 'low'])
  })

  // ── Date range inputs ───────────────────────────────────────────────────────

  it('calls onDateChange with created_from key when from-date changes', () => {
    const onDateChange = vi.fn()
    renderBar({ onDateChange })
    fireEvent.change(screen.getByLabelText('Created from'), {
      target: { value: '2024-03-01' },
    })
    expect(onDateChange).toHaveBeenCalledWith('created_from', '2024-03-01')
  })

  it('calls onDateChange with created_to key when to-date changes', () => {
    const onDateChange = vi.fn()
    renderBar({ onDateChange })
    fireEvent.change(screen.getByLabelText('Created to'), {
      target: { value: '2024-06-30' },
    })
    expect(onDateChange).toHaveBeenCalledWith('created_to', '2024-06-30')
  })

  it('reflects current created_from value in input', () => {
    renderBar({ activeFilters: makeActiveFilters({ created_from: '2024-01-15' }) })
    expect(screen.getByLabelText('Created from')).toHaveValue('2024-01-15')
  })

  it('reflects current created_to value in input', () => {
    renderBar({ activeFilters: makeActiveFilters({ created_to: '2024-12-31' }) })
    expect(screen.getByLabelText('Created to')).toHaveValue('2024-12-31')
  })

  // ── Multiple active filters ─────────────────────────────────────────────────

  it('shows "Clear all" when both array and date filters are active', () => {
    renderBar({
      activeFilters: makeActiveFilters({
        severity: ['high'],
        created_from: '2024-01-01',
      }),
    })
    expect(screen.getByText('Clear all')).toBeInTheDocument()
  })
})
