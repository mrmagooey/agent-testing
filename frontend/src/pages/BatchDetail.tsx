import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  getBatchResults,
  cancelBatch,
  type Run,
  type Finding,
} from '../api/client'
import { useBatch } from '../hooks/useBatch'
import ProgressBar from '../components/ProgressBar'
import MatrixTable from '../components/MatrixTable'
import FindingsExplorer from '../components/FindingsExplorer'
import DimensionChart from '../components/DimensionChart'
import DownloadButton from '../components/DownloadButton'

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400',
  running: 'bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 animate-pulse',
  completed: 'bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300',
  failed: 'bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300',
  cancelled: 'bg-yellow-100 dark:bg-yellow-900 text-yellow-700 dark:text-yellow-300',
}

function buildModelChart(runs: Run[]) {
  const byModel: Record<string, number[]> = {}
  for (const r of runs) {
    if (r.f1 !== undefined) {
      byModel[r.model] = byModel[r.model] ?? []
      byModel[r.model].push(r.f1)
    }
  }
  return Object.entries(byModel).map(([model, vals]) => ({
    model,
    avg_f1: vals.reduce((a, b) => a + b, 0) / vals.length,
  }))
}

export default function BatchDetail() {
  const { id: batchId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { batch, loading, error } = useBatch(batchId)
  const [results, setResults] = useState<{ runs: Run[]; findings: Finding[] } | null>(null)
  const [resultsLoading, setResultsLoading] = useState(false)
  const [selectedRuns, setSelectedRuns] = useState<string[]>([])
  const [cancelling, setCancelling] = useState(false)

  const isTerminal = batch && ['completed', 'failed', 'cancelled'].includes(batch.status)

  useEffect(() => {
    if (!batchId || !isTerminal) return
    setResultsLoading(true)
    getBatchResults(batchId)
      .then(setResults)
      .catch(() => null)
      .finally(() => setResultsLoading(false))
  }, [batchId, isTerminal])

  const handleCancel = async () => {
    if (!batchId || cancelling) return
    setCancelling(true)
    try {
      await cancelBatch(batchId)
    } finally {
      setCancelling(false)
    }
  }

  const handleCompare = () => {
    if (selectedRuns.length === 2) {
      navigate(`/batches/${batchId}/compare?a=${selectedRuns[0]}&b=${selectedRuns[1]}`)
    }
  }

  if (loading && !batch) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading batch...</div>
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
        {error}
      </div>
    )
  }

  if (!batch) return null

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h1 className="text-xl font-bold font-mono">{batch.batch_id}</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">Dataset: {batch.dataset}</p>
          </div>
          <div className="flex items-center gap-3">
            <span className={`px-3 py-1 rounded-full text-sm font-medium ${STATUS_BADGE[batch.status] ?? ''}`}>
              {batch.status}
            </span>
            {!isTerminal && (
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="px-3 py-1 rounded-lg text-sm border border-red-300 dark:border-red-700 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 transition-colors disabled:opacity-50"
              >
                {cancelling ? 'Cancelling…' : 'Cancel'}
              </button>
            )}
            {isTerminal && batchId && <DownloadButton batchId={batchId} />}
          </div>
        </div>

        {/* Progress */}
        <ProgressBar
          completed={batch.completed_runs}
          running={batch.running_runs}
          pending={batch.pending_runs}
          failed={batch.failed_runs}
          total={batch.total_runs}
        />

        {/* Cost vs cap */}
        <div className="mt-4 flex items-center gap-4 text-sm">
          <span className="text-gray-600 dark:text-gray-400">
            Cost: <strong>${batch.total_cost_usd.toFixed(2)}</strong>
          </span>
          {batch.spend_cap_usd && (
            <span className="text-gray-600 dark:text-gray-400">
              Cap: <strong>${batch.spend_cap_usd.toFixed(2)}</strong>
              {batch.total_cost_usd / batch.spend_cap_usd > 0.8 && (
                <span className="ml-2 text-orange-600 dark:text-orange-400 font-medium">
                  ⚠ Near cap
                </span>
              )}
            </span>
          )}
        </div>
      </div>

      {/* Results */}
      {resultsLoading && (
        <div className="flex items-center justify-center h-32 text-gray-400">Loading results...</div>
      )}

      {results && (
        <>
          {/* Comparative Matrix */}
          <section className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold">Comparative Matrix</h2>
              {selectedRuns.length === 2 && (
                <button
                  onClick={handleCompare}
                  className="text-sm px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white transition-colors"
                >
                  Compare Selected
                </button>
              )}
            </div>
            <MatrixTable
              runs={results.runs}
              onSelect={setSelectedRuns}
              selectedIds={selectedRuns}
            />
          </section>

          {/* Dimension Charts */}
          <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <DimensionChart
              data={buildModelChart(results.runs)}
              xKey="model"
              yKey="avg_f1"
              title="Model Comparison (Avg F1)"
            />
            <DimensionChart
              data={(() => {
                const byStrategy: Record<string, number[]> = {}
                for (const r of results.runs) {
                  if (r.f1 !== undefined) {
                    byStrategy[r.strategy] = byStrategy[r.strategy] ?? []
                    byStrategy[r.strategy].push(r.f1)
                  }
                }
                return Object.entries(byStrategy).map(([strategy, vals]) => ({
                  strategy,
                  avg_f1: vals.reduce((a, b) => a + b, 0) / vals.length,
                }))
              })()}
              xKey="strategy"
              yKey="avg_f1"
              title="Strategy Comparison (Avg F1)"
              color="#10b981"
            />
          </section>

          {/* Cost Analysis */}
          <section className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
            <h2 className="text-lg font-semibold mb-4">Cost Analysis</h2>
            <table className="w-full text-sm">
              <thead className="text-gray-500 dark:text-gray-400">
                <tr>
                  <th className="text-left pb-2">Model</th>
                  <th className="text-right pb-2">Runs</th>
                  <th className="text-right pb-2">Total Cost</th>
                  <th className="text-right pb-2">Avg Cost/Run</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {Object.entries(
                  results.runs.reduce(
                    (acc, r) => {
                      if (!acc[r.model]) acc[r.model] = { count: 0, total: 0 }
                      acc[r.model].count++
                      acc[r.model].total += r.cost_usd ?? 0
                      return acc
                    },
                    {} as Record<string, { count: number; total: number }>
                  )
                ).map(([model, { count, total }]) => (
                  <tr key={model}>
                    <td className="py-2 font-mono text-xs">{model}</td>
                    <td className="py-2 text-right text-gray-600 dark:text-gray-400">{count}</td>
                    <td className="py-2 text-right font-medium">${total.toFixed(2)}</td>
                    <td className="py-2 text-right text-gray-600 dark:text-gray-400">
                      ${(total / count).toFixed(3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {/* Findings Explorer */}
          {batchId && (
            <section className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
              <h2 className="text-lg font-semibold mb-4">Findings</h2>
              <FindingsExplorer batchId={batchId} findings={results.findings} />
            </section>
          )}
        </>
      )}
    </div>
  )
}
