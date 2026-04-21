import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import CostEstimate from '../../components/CostEstimate'
import type { CostEstimate as CostEstimateType } from '../../api/client'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeEstimate(overrides: Partial<CostEstimateType> = {}): CostEstimateType {
  return {
    total_runs: 20,
    estimated_cost_usd: 4.25,
    by_model: { 'gpt-4o': 2.5, 'claude-3-5-sonnet': 1.75 },
    ...overrides,
  }
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('CostEstimate', () => {
  it('shows "Calculating..." spinner while loading', () => {
    render(<CostEstimate estimate={null} loading={true} />)
    expect(screen.getByText(/calculating/i)).toBeInTheDocument()
  })

  it('shows placeholder message when not loading and no estimate', () => {
    render(<CostEstimate estimate={null} loading={false} />)
    expect(screen.getByText(/configure experiment/i)).toBeInTheDocument()
  })

  it('does not show placeholder when loading', () => {
    render(<CostEstimate estimate={null} loading={true} />)
    expect(screen.queryByText(/configure experiment/i)).not.toBeInTheDocument()
  })

  it('renders total_runs when estimate is provided', () => {
    render(<CostEstimate estimate={makeEstimate({ total_runs: 40 })} loading={false} />)
    expect(screen.getByText('40')).toBeInTheDocument()
  })

  it('renders estimated cost formatted to 2 decimal places', () => {
    render(<CostEstimate estimate={makeEstimate({ estimated_cost_usd: 12.5 })} loading={false} />)
    expect(screen.getByText('$12.50')).toBeInTheDocument()
  })

  it('renders per-model cost breakdown when by_model has entries', () => {
    render(<CostEstimate estimate={makeEstimate()} loading={false} />)
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.getByText('$2.50')).toBeInTheDocument()
    expect(screen.getByText('claude-3-5-sonnet')).toBeInTheDocument()
    expect(screen.getByText('$1.75')).toBeInTheDocument()
  })

  it('does not render per-model table when by_model is empty', () => {
    render(<CostEstimate estimate={makeEstimate({ by_model: {} })} loading={false} />)
    expect(screen.queryByText(/per model/i)).not.toBeInTheDocument()
  })

  it('renders "Cost Estimate" heading', () => {
    render(<CostEstimate estimate={null} loading={false} />)
    expect(screen.getByText(/cost estimate/i)).toBeInTheDocument()
  })

  it('shows estimate correctly after loading transitions to false', () => {
    const { rerender } = render(<CostEstimate estimate={null} loading={true} />)
    expect(screen.getByText(/calculating/i)).toBeInTheDocument()

    rerender(<CostEstimate estimate={makeEstimate({ estimated_cost_usd: 9.99 })} loading={false} />)
    expect(screen.getByText('$9.99')).toBeInTheDocument()
    expect(screen.queryByText(/calculating/i)).not.toBeInTheDocument()
  })
})
