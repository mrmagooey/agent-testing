/**
 * Pure trend-analysis utilities, mirroring the backend _compute_trend_summary logic.
 * Kept side-effect-free so they can be unit-tested without any React overhead.
 */

export interface TrendPoint {
  experiment_id: string
  completed_at: string
  f1: number
  precision: number
  recall: number
  cost_usd: number
  run_count: number
}

export interface TrendSummary {
  latest_f1: number | null
  prev_f1: number | null
  delta_f1: number | null
  trailing_median_f1: number | null
  is_regression: boolean
}

export interface TrendSeriesKey {
  model: string
  strategy: string
  tool_variant: string
  tool_extensions: string[]
}

export interface TrendSeries {
  key: TrendSeriesKey
  points: TrendPoint[]
  summary: TrendSummary
}

export interface TrendResponse {
  dataset: string
  experiments: Array<{ experiment_id: string; completed_at: string }>
  series: TrendSeries[]
}

/**
 * Compute the trailing median of an array of F1 values, excluding the last element.
 * Returns null when the array has fewer than 2 elements.
 */
function trailingMedian(values: number[]): number | null {
  if (values.length < 2) return null
  const trailing = values.slice(0, -1)
  const sorted = [...trailing].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 1
    ? sorted[mid]
    : (sorted[mid - 1] + sorted[mid]) / 2
}

/**
 * Detect a regression: latest F1 is more than 0.05 below the trailing median,
 * and the series has at least 3 data points.
 *
 * Mirrors the backend `_compute_trend_summary` logic so client-side slicing
 * produces identical results to what the server would report.
 */
export function detectRegression(points: TrendPoint[]): boolean {
  if (points.length < 3) return false
  const f1s = points.map((p) => p.f1)
  const latest = f1s[f1s.length - 1]
  const median = trailingMedian(f1s)
  if (median === null) return false
  return latest - median < -0.05
}

/**
 * Compute a full TrendSummary from a list of points (ascending time order).
 */
export function computeTrendSummary(points: TrendPoint[]): TrendSummary {
  if (points.length === 0) {
    return {
      latest_f1: null,
      prev_f1: null,
      delta_f1: null,
      trailing_median_f1: null,
      is_regression: false,
    }
  }

  const latestF1 = points[points.length - 1].f1
  const prevF1 = points.length >= 2 ? points[points.length - 2].f1 : null
  const deltaF1 = prevF1 !== null ? latestF1 - prevF1 : null
  const median = trailingMedian(points.map((p) => p.f1))

  return {
    latest_f1: latestF1,
    prev_f1: prevF1,
    delta_f1: deltaF1,
    trailing_median_f1: median,
    is_regression: detectRegression(points),
  }
}
