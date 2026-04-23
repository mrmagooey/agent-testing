import React, { useEffect, useState } from 'react'
import { getAccuracyMatrix, type AccuracyMatrix, type AccuracyMatrixCell } from '../api/client'

// Single-hue amber ramp: oklch(0.96 0.03 80) → oklch(0.70 0.17 65)
// We interpolate lightness and chroma across the 0–1 accuracy range.
function amberCellStyle(accuracy: number): React.CSSProperties {
  const t = Math.max(0, Math.min(1, accuracy))
  // lightness: 0.96 (low) → 0.70 (high)
  const l = 0.96 - t * 0.26
  // chroma: 0.03 (low) → 0.17 (high)
  const c = 0.03 + t * 0.14
  // hue: 80 → 65
  const h = 80 - t * 15
  return {
    backgroundColor: `oklch(${l.toFixed(3)} ${c.toFixed(3)} ${h.toFixed(1)})`,
    color: t > 0.5 ? '#1a1209' : '#78716c',
  }
}

function signalLabel(accuracy: number): string {
  if (accuracy >= 0.8) return 'PASS'
  if (accuracy >= 0.6) return 'WARN'
  return 'FAIL'
}

// Signal text colors chosen for contrast against the amber cell ramp in both
// light and dark modes. The cell background is theme-independent (oklch amber),
// so we use solid, dark signal hues rather than `text-signal-*` tokens whose
// default shades were too light/saturated to read on saturated amber.
function signalCls(accuracy: number): string {
  if (accuracy >= 0.8) return 'text-green-900'
  if (accuracy >= 0.6) return 'text-orange-900'
  return 'text-red-900'
}

function HeatmapCell({ cell }: { cell: AccuracyMatrixCell }) {
  const label = signalLabel(cell.accuracy)
  const labelCls = signalCls(cell.accuracy)
  return (
    <td
      data-testid="heatmap-cell"
      data-signal={label}
      className="px-3 py-3 text-center font-mono text-xs"
      style={amberCellStyle(cell.accuracy)}
      title={`${cell.model} × ${cell.strategy}: ${cell.accuracy.toFixed(3)} recall (${cell.run_count} run${cell.run_count !== 1 ? 's' : ''})`}
    >
      <div className="font-semibold tabular-nums">{cell.accuracy.toFixed(3)}</div>
      <div data-testid="heatmap-cell-signal" className={`text-[10px] font-semibold ${labelCls}`}>{label}</div>
    </td>
  )
}

function EmptyCell() {
  return (
    <td className="px-3 py-3 text-center text-muted-foreground/40 font-mono text-xs bg-muted/20">
      —
    </td>
  )
}

export default function AccuracyHeatmap() {
  const [matrix, setMatrix] = useState<AccuracyMatrix | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getAccuracyMatrix()
      .then(setMatrix)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="animate-pulse h-24 bg-muted rounded-sm" />
    )
  }

  if (error) {
    return (
      <p className="text-sm text-signal-danger font-mono">{error}</p>
    )
  }

  if (!matrix || matrix.cells.length === 0) {
    return (
      <p className="text-sm text-muted-foreground font-mono">
        No completed runs with evaluation data yet.
      </p>
    )
  }

  const cellIndex: Record<string, AccuracyMatrixCell> = {}
  for (const cell of matrix.cells) {
    cellIndex[`${cell.model}::${cell.strategy}`] = cell
  }

  return (
    <div data-testid="accuracy-heatmap">
      <div className="overflow-x-auto border border-border rounded-sm">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 border-b border-border">
            <tr>
              <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground whitespace-nowrap">Model</th>
              {matrix.strategies.map((strategy) => (
                <th key={strategy} className="px-3 py-2 text-center font-mono text-[10px] uppercase tracking-[0.15em] text-muted-foreground whitespace-nowrap">
                  {strategy}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {matrix.models.map((model) => (
              <tr key={model} className="bg-card">
                <td className="px-3 py-3 font-mono text-xs text-foreground whitespace-nowrap">
                  {model}
                </td>
                {matrix.strategies.map((strategy) => {
                  const cell = cellIndex[`${model}::${strategy}`]
                  return cell ? (
                    <HeatmapCell key={strategy} cell={cell} />
                  ) : (
                    <EmptyCell key={strategy} />
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[10px] text-muted-foreground font-mono uppercase tracking-[0.1em]">
        Accuracy = recall (TP / (TP + FN)), averaged across all completed runs per cell.
        Amber ramp: dim = low, saturated = high.
      </p>
    </div>
  )
}
