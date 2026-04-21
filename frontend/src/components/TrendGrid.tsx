import { useNavigate } from 'react-router-dom'
import Sparkline from './Sparkline'
import type { TrendSeries } from '../api/client'

function RegressionBadge({ sparse }: { sparse: boolean }) {
  if (sparse) {
    return (
      <span
        title="Insufficient history"
        className="inline-flex items-center opacity-30 cursor-not-allowed"
        aria-label="Insufficient history"
      >
        <WarningIcon className="w-3.5 h-3.5 text-amber-500" />
      </span>
    )
  }
  return (
    <span
      title="Regression detected"
      className="inline-flex items-center"
      aria-label="Regression detected"
    >
      <WarningIcon className="w-3.5 h-3.5 text-red-500 dark:text-red-400" />
    </span>
  )
}

function WarningIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2.5}
      aria-hidden="true"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.962-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z"
      />
    </svg>
  )
}

interface TrendGridProps {
  series: TrendSeries[]
}

export default function TrendGrid({ series }: TrendGridProps) {
  const navigate = useNavigate()

  if (series.length === 0) {
    return (
      <p className="text-sm text-gray-400 dark:text-gray-500">
        No trend data available for this dataset.
      </p>
    )
  }

  // Sort by latest_f1 desc (already done server-side but defensive client-side sort)
  const sorted = [...series].sort(
    (a, b) => (b.summary.latest_f1 ?? 0) - (a.summary.latest_f1 ?? 0)
  )

  return (
    <div
      className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700"
      data-testid="trend-grid"
    >
      <table className="w-full text-sm">
        <thead className="bg-gray-50 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
          <tr>
            <th className="px-3 py-2 text-left font-medium">Model</th>
            <th className="px-3 py-2 text-left font-medium">Strategy</th>
            <th className="px-3 py-2 text-left font-medium">Tool Variant</th>
            <th className="px-3 py-2 text-left font-medium">Extensions</th>
            <th className="px-3 py-2 text-left font-medium">Sparkline</th>
            <th className="px-3 py-2 text-right font-medium">Latest F1</th>
            <th className="px-3 py-2 text-right font-medium">Δ vs Prev</th>
            <th className="px-3 py-2 text-center font-medium">Badge</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
          {sorted.map((s, i) => {
            const { key, points, summary } = s
            const isRegression = summary.is_regression
            const sparse = points.length < 3

            const rowCls = isRegression
              ? 'bg-red-50 dark:bg-red-950/30'
              : 'bg-white dark:bg-gray-900'

            const extLabel =
              key.tool_extensions.length > 0
                ? key.tool_extensions.join('+')
                : 'none'

            const deltaF1 = summary.delta_f1
            const deltaDisplay =
              deltaF1 !== null
                ? `${deltaF1 >= 0 ? '+' : ''}${deltaF1.toFixed(3)}`
                : '—'
            const deltaCls =
              deltaF1 === null
                ? 'text-gray-400'
                : deltaF1 > 0
                ? 'text-green-600 dark:text-green-400'
                : deltaF1 < 0
                ? 'text-red-600 dark:text-red-400'
                : 'text-gray-400'

            return (
              <tr key={i} className={rowCls}>
                <td className="px-3 py-2 font-mono text-xs text-gray-800 dark:text-gray-200 whitespace-nowrap">
                  {key.model}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-gray-700 dark:text-gray-300 whitespace-nowrap">
                  {key.strategy}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-gray-600 dark:text-gray-400">
                  {key.tool_variant}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-gray-500 dark:text-gray-500">
                  {extLabel}
                </td>
                <td className="px-3 py-2">
                  <Sparkline
                    points={points}
                    onPointClick={(experimentId) =>
                      navigate(`/experiments/${experimentId}`)
                    }
                  />
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs font-semibold text-gray-800 dark:text-gray-200">
                  {summary.latest_f1 !== null
                    ? summary.latest_f1.toFixed(3)
                    : '—'}
                </td>
                <td className={`px-3 py-2 text-right font-mono text-xs ${deltaCls}`}>
                  {deltaDisplay}
                </td>
                <td className="px-3 py-2 text-center">
                  {isRegression && <RegressionBadge sparse={false} />}
                  {!isRegression && sparse && points.length > 0 && (
                    <RegressionBadge sparse={true} />
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
