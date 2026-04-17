import { useEffect, useState } from 'react'
import { getAccuracyMatrix, type AccuracyMatrix, type AccuracyMatrixCell } from '../api/client'
import { metricTone } from '../constants/colors'

function HeatmapCell({ cell }: { cell: AccuracyMatrixCell }) {
  const { cls, label } = metricTone(cell.accuracy, 'higher-is-better')
  return (
    <td
      className={`px-3 py-3 text-center font-mono text-xs ${cls}`}
      title={`${cell.model} × ${cell.strategy}: ${cell.accuracy.toFixed(3)} recall (${cell.run_count} run${cell.run_count !== 1 ? 's' : ''})`}
    >
      <div className="font-semibold">{cell.accuracy.toFixed(3)}</div>
      <div className="text-[10px] opacity-70">{label}</div>
    </td>
  )
}

function EmptyCell() {
  return (
    <td className="px-3 py-3 text-center text-gray-300 dark:text-gray-600 font-mono text-xs bg-gray-50 dark:bg-gray-800/40">
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
      <div className="animate-pulse h-24 bg-gray-100 dark:bg-gray-700 rounded-lg" />
    )
  }

  if (error) {
    return (
      <p className="text-sm text-red-500 dark:text-red-400">{error}</p>
    )
  }

  if (!matrix || matrix.cells.length === 0) {
    return (
      <p className="text-sm text-gray-400 dark:text-gray-500">
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
      <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
            <tr>
              <th className="px-3 py-2 text-left font-medium whitespace-nowrap">Model</th>
              {matrix.strategies.map((strategy) => (
                <th key={strategy} className="px-3 py-2 text-center font-medium whitespace-nowrap font-mono">
                  {strategy}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            {matrix.models.map((model) => (
              <tr key={model} className="bg-white dark:bg-gray-900">
                <td className="px-3 py-3 font-mono text-xs text-gray-700 dark:text-gray-300 whitespace-nowrap font-medium">
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
      <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
        Accuracy = recall (TP / (TP + FN)), averaged across all completed runs per cell.
        Color scale: emerald ≥ 0.8, amber ≥ 0.6, rose &lt; 0.6.
      </p>
    </div>
  )
}
