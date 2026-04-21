import type { CostEstimate as CostEstimateType } from '../api/client'

export interface CostEstimateProps {
  estimate: CostEstimateType | null
  loading: boolean
}

export default function CostEstimate({ estimate, loading }: CostEstimateProps) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg p-4 border border-gray-200 dark:border-gray-700">
      <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">Cost Estimate</h3>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
          <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          Calculating...
        </div>
      )}

      {!loading && !estimate && (
        <p className="text-sm text-gray-400 dark:text-gray-500">Configure experiment to see estimate.</p>
      )}

      {!loading && estimate && (
        <div className="space-y-3">
          <div className="flex items-baseline justify-between">
            <span className="text-sm text-gray-600 dark:text-gray-400">Total runs</span>
            <span className="font-medium">{estimate.total_runs}</span>
          </div>
          <div className="flex items-baseline justify-between">
            <span className="text-sm text-gray-600 dark:text-gray-400">Estimated cost</span>
            <span className="text-lg font-bold text-indigo-600 dark:text-indigo-400">
              ${estimate.estimated_cost_usd.toFixed(2)}
            </span>
          </div>

          {Object.keys(estimate.by_model).length > 0 && (
            <div className="pt-2 border-t border-gray-100 dark:border-gray-700">
              <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">Per model</p>
              <table className="w-full text-sm">
                <tbody>
                  {Object.entries(estimate.by_model).map(([model, cost]) => (
                    <tr key={model}>
                      <td className="text-gray-600 dark:text-gray-400 font-mono text-xs py-0.5">
                        {model}
                      </td>
                      <td className="text-right font-medium">${(cost as number).toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
