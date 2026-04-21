import { describe, it, expect } from 'vitest'
import { detectRegression, computeTrendSummary } from '../../lib/trends'
import type { TrendPoint } from '../../lib/trends'

function pt(f1: number, id = 'x'): TrendPoint {
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

// ---------------------------------------------------------------------------
// detectRegression truth table
// ---------------------------------------------------------------------------

describe('detectRegression', () => {
  it('returns false for empty series', () => {
    expect(detectRegression([])).toBe(false)
  })

  it('returns false for single point', () => {
    expect(detectRegression([pt(0.5)])).toBe(false)
  })

  it('returns false for two points even with large drop', () => {
    expect(detectRegression([pt(0.9), pt(0.1)])).toBe(false)
  })

  it('returns false when drop is slightly above threshold (-0.04)', () => {
    // trailing = [0.8, 0.8] → median = 0.8; latest = 0.76 → delta = -0.04
    // condition is < -0.05 (strict), so -0.04 is NOT a regression
    expect(detectRegression([pt(0.8), pt(0.8), pt(0.76)])).toBe(false)
  })

  it('returns true when drop exceeds -0.05 (delta = -0.06)', () => {
    // trailing = [0.8, 0.8] → median = 0.8; latest = 0.74 → delta = -0.06
    expect(detectRegression([pt(0.8), pt(0.8), pt(0.74)])).toBe(true)
  })

  it('returns true for larger drop (-0.35)', () => {
    // trailing = [0.8, 0.9] → median = 0.85; latest = 0.5 → delta = -0.35
    expect(detectRegression([pt(0.8), pt(0.9), pt(0.5)])).toBe(true)
  })

  it('returns false when series improves (positive delta)', () => {
    expect(detectRegression([pt(0.5), pt(0.6), pt(0.9)])).toBe(false)
  })

  it('handles 4-point series with odd-length trailing (median computation)', () => {
    // trailing = [0.7, 0.8, 0.9] → sorted median = 0.8; latest = 0.7 → delta = -0.1
    expect(detectRegression([pt(0.7), pt(0.8), pt(0.9), pt(0.7)])).toBe(true)
  })

  it('handles 4-point series where latest is near trailing median', () => {
    // trailing = [0.8, 0.8, 0.8] → median = 0.8; latest = 0.77 → delta = -0.03
    expect(detectRegression([pt(0.8), pt(0.8), pt(0.8), pt(0.77)])).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// computeTrendSummary
// ---------------------------------------------------------------------------

describe('computeTrendSummary', () => {
  it('returns null fields for empty points', () => {
    const s = computeTrendSummary([])
    expect(s.latest_f1).toBeNull()
    expect(s.prev_f1).toBeNull()
    expect(s.delta_f1).toBeNull()
    expect(s.trailing_median_f1).toBeNull()
    expect(s.is_regression).toBe(false)
  })

  it('returns correct values for single point', () => {
    const s = computeTrendSummary([pt(0.8)])
    expect(s.latest_f1).toBeCloseTo(0.8)
    expect(s.prev_f1).toBeNull()
    expect(s.delta_f1).toBeNull()
    expect(s.trailing_median_f1).toBeNull()
    expect(s.is_regression).toBe(false)
  })

  it('computes delta for two points', () => {
    const s = computeTrendSummary([pt(0.7), pt(0.8)])
    expect(s.latest_f1).toBeCloseTo(0.8)
    expect(s.prev_f1).toBeCloseTo(0.7)
    expect(s.delta_f1).toBeCloseTo(0.1)
    expect(s.is_regression).toBe(false)
  })

  it('detects regression with 3+ points', () => {
    const s = computeTrendSummary([pt(0.85), pt(0.85), pt(0.78)])
    // trailing = [0.85, 0.85] → median = 0.85; delta = 0.78 - 0.85 = -0.07 → regression
    expect(s.is_regression).toBe(true)
    expect(s.trailing_median_f1).toBeCloseTo(0.85)
  })

  it('trailing_median uses even-length median formula', () => {
    // points = [0.7, 0.8, 0.9, 0.5]
    // trailing = [0.7, 0.8, 0.9] → sorted → [0.7, 0.8, 0.9] → median = 0.8
    const s = computeTrendSummary([pt(0.7), pt(0.8), pt(0.9), pt(0.5)])
    expect(s.trailing_median_f1).toBeCloseTo(0.8)
    // 0.5 - 0.8 = -0.3 → regression
    expect(s.is_regression).toBe(true)
  })
})
