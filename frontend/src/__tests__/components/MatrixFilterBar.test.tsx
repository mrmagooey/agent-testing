import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import MatrixFilterBar from '../../components/MatrixFilterBar'
import type { Run } from '../../api/client'
import { clearMatrixFilter } from '../../lib/matrixFilter'
import type { MatrixFilter } from '../../lib/matrixFilter'

// ─── Helpers ──────────────────────────────────────────────────────────────────

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

function makeFilter(overrides: Partial<MatrixFilter> = {}): MatrixFilter {
  return {
    ...clearMatrixFilter(),
    ...overrides,
  }
}

type RenderProps = {
  runs?: Run[]
  value?: MatrixFilter
  onChange?: (next: MatrixFilter) => void
}

function renderBar({
  runs = [],
  value = makeFilter(),
  onChange = vi.fn(),
}: RenderProps = {}) {
  return render(
    <MatrixFilterBar runs={runs} value={value} onChange={onChange} />,
  )
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('MatrixFilterBar', () => {
  // ── Returns null when ≤1 unique value per dimension ────────────────────────

  it('renders nothing when there are no runs', () => {
    const { container } = renderBar({ runs: [] })
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when all runs have identical dimension values', () => {
    const runs = [
      makeRun({ model: 'claude-3-5-sonnet', strategy: 'basic', tool_variant: 'none', profile: 'default' }),
      makeRun({ model: 'claude-3-5-sonnet', strategy: 'basic', tool_variant: 'none', profile: 'default' }),
    ]
    const { container } = renderBar({ runs })
    expect(container.firstChild).toBeNull()
  })

  // ── Renders when dimensions have multiple unique values ─────────────────────

  it('renders Model filter button when multiple models present', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs })
    expect(screen.getByText('Model')).toBeInTheDocument()
  })

  it('renders Strategy filter button when multiple strategies present', () => {
    const runs = [
      makeRun({ run_id: 'r1', strategy: 'basic' }),
      makeRun({ run_id: 'r2', strategy: 'advanced' }),
    ]
    renderBar({ runs })
    expect(screen.getByText('Strategy')).toBeInTheDocument()
  })

  it('renders Tools filter button when multiple tool_variant values present', () => {
    const runs = [
      makeRun({ run_id: 'r1', tool_variant: 'none' }),
      makeRun({ run_id: 'r2', tool_variant: 'bash' }),
    ]
    renderBar({ runs })
    expect(screen.getByText('Tools')).toBeInTheDocument()
  })

  it('renders Profile filter button when multiple profiles present', () => {
    const runs = [
      makeRun({ run_id: 'r1', profile: 'default' }),
      makeRun({ run_id: 'r2', profile: 'strict' }),
    ]
    renderBar({ runs })
    expect(screen.getByText('Profile')).toBeInTheDocument()
  })

  it('renders Extensions filter button when multiple extensions present', () => {
    const runs = [
      makeRun({ run_id: 'r1', tool_extensions: ['lsp'] }),
      makeRun({ run_id: 'r2', tool_extensions: ['tree_sitter'] }),
    ]
    renderBar({ runs })
    expect(screen.getByText('Extensions')).toBeInTheDocument()
  })

  // ── Run count display ───────────────────────────────────────────────────────

  it('shows "N of M runs" count text', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs })
    expect(screen.getByText('2 of 2 runs')).toBeInTheDocument()
  })

  it('shows filtered count when a filter is active', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    const value = makeFilter({ model: ['gpt-4o'] })
    renderBar({ runs, value })
    expect(screen.getByText('1 of 2 runs')).toBeInTheDocument()
  })

  // ── Clear filters button ────────────────────────────────────────────────────

  it('renders "Clear filters" button', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs })
    expect(screen.getByText('Clear filters')).toBeInTheDocument()
  })

  it('"Clear filters" button is disabled when no filters are active', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs, value: makeFilter() })
    expect(screen.getByText('Clear filters')).toBeDisabled()
  })

  it('"Clear filters" button is enabled when a filter is active', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs, value: makeFilter({ model: ['gpt-4o'] }) })
    expect(screen.getByText('Clear filters')).not.toBeDisabled()
  })

  it('calls onChange with empty filter when "Clear filters" clicked', () => {
    const onChange = vi.fn()
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs, value: makeFilter({ model: ['gpt-4o'] }), onChange })
    fireEvent.click(screen.getByText('Clear filters'))
    expect(onChange).toHaveBeenCalledWith(clearMatrixFilter())
  })

  // ── Popover open/close ──────────────────────────────────────────────────────

  it('opens Model popover when Model button clicked', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs })
    fireEvent.click(screen.getByText('Model'))
    // Options should appear in the listbox
    expect(screen.getByRole('listbox')).toBeInTheDocument()
  })

  it('closes popover on second click of the button', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs })
    fireEvent.click(screen.getByText('Model')) // open
    fireEvent.click(screen.getByText('Model')) // close
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('shows sorted model options in open popover', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'gpt-4o' }),
      makeRun({ run_id: 'r2', model: 'claude-3-5-sonnet' }),
    ]
    renderBar({ runs })
    fireEvent.click(screen.getByText('Model'))
    // Both models should be visible as options
    const options = screen.getAllByRole('option')
    const texts = options.map((o) => o.textContent)
    expect(texts).toContain('claude-3-5-sonnet')
    expect(texts).toContain('gpt-4o')
  })

  it('shows ▲ chevron when popover is open, ▼ when closed', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs })
    expect(screen.getAllByText('▼').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByText('Model'))
    expect(screen.getByText('▲')).toBeInTheDocument()
  })

  // ── Filter toggling ─────────────────────────────────────────────────────────

  it('calls onChange with selected model when option clicked', () => {
    const onChange = vi.fn()
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs, onChange })
    fireEvent.click(screen.getByText('Model'))
    fireEvent.click(screen.getByRole('option', { name: /gpt-4o/ }))
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ model: ['gpt-4o'] }),
    )
  })

  it('removes model from selection when already-selected option clicked', () => {
    const onChange = vi.fn()
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs, value: makeFilter({ model: ['gpt-4o'] }), onChange })
    fireEvent.click(screen.getByText('Model'))
    fireEvent.click(screen.getByRole('option', { name: /gpt-4o/ }))
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ model: [] }),
    )
  })

  it('shows selection count badge on button when filter has active values', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs, value: makeFilter({ model: ['gpt-4o'] }) })
    // The badge shows "1" for the active selection
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  // ── Close on outside click ──────────────────────────────────────────────────

  it('closes popover when clicking outside', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs })
    fireEvent.click(screen.getByText('Model')) // open
    expect(screen.getByRole('listbox')).toBeInTheDocument()

    // Simulate clicking outside by dispatching mousedown on document
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  // ── aria attributes ─────────────────────────────────────────────────────────

  it('sets aria-expanded=false on button when popover closed', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs })
    const modelBtn = screen.getByText('Model').closest('button')!
    expect(modelBtn).toHaveAttribute('aria-expanded', 'false')
  })

  it('sets aria-expanded=true on button when popover open', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs })
    const modelBtn = screen.getByText('Model').closest('button')!
    fireEvent.click(modelBtn)
    expect(modelBtn).toHaveAttribute('aria-expanded', 'true')
  })

  it('sets aria-selected=true on active option', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs, value: makeFilter({ model: ['gpt-4o'] }) })
    fireEvent.click(screen.getByText('Model'))
    const gptOption = screen.getByRole('option', { name: /gpt-4o/ })
    expect(gptOption).toHaveAttribute('aria-selected', 'true')
  })

  it('sets aria-selected=false on inactive option', () => {
    const runs = [
      makeRun({ run_id: 'r1', model: 'claude-3-5-sonnet' }),
      makeRun({ run_id: 'r2', model: 'gpt-4o' }),
    ]
    renderBar({ runs, value: makeFilter({ model: ['gpt-4o'] }) })
    fireEvent.click(screen.getByText('Model'))
    const sonnetOption = screen.getByRole('option', { name: /claude-3-5-sonnet/ })
    expect(sonnetOption).toHaveAttribute('aria-selected', 'false')
  })
})
