import { useState, useEffect } from 'react'
import {
  listBatches,
  compareBatches,
  getFPPatterns,
  type Batch,
  type FPPattern,
} from '../api/client'

function DeltaCell({ value }: { value: number }) {
  const cls =
    value > 0
      ? 'text-green-600 dark:text-green-400'
      : value < 0
      ? 'text-red-600 dark:text-red-400'
      : 'text-gray-400'
  return (
    <span className={`font-mono text-xs ${cls}`}>
      {value > 0 ? '+' : ''}{value.toFixed(3)}
    </span>
  )
}

export default function Feedback() {
  const [batches, setBatches] = useState<Batch[]>([])
  const [batchAId, setBatchAId] = useState('')
  const [batchBId, setBatchBId] = useState('')
  const [loading, setLoading] = useState(true)
  const [comparing, setComparing] = useState(false)
  const [comparison, setComparison] = useState<{
    metric_deltas: Record<string, unknown>[]
    fp_patterns: FPPattern[]
    stability: Record<string, unknown>
  } | null>(null)
  const [compareError, setCompareError] = useState<string | null>(null)

  const [fpBatchId, setFpBatchId] = useState('')
  const [fpPatterns, setFpPatterns] = useState<FPPattern[]>([])
  const [fpLoading, setFpLoading] = useState(false)

  useEffect(() => {
    listBatches()
      .then(setBatches)
      .catch(() => null)
      .finally(() => setLoading(false))
  }, [])

  const handleCompare = async () => {
    if (!batchAId || !batchBId) return
    setComparing(true)
    setCompareError(null)
    try {
      const result = await compareBatches(batchAId, batchBId)
      setComparison(result)
    } catch (err) {
      setCompareError(err instanceof Error ? err.message : 'Comparison failed')
    } finally {
      setComparing(false)
    }
  }

  const handleLoadFP = async () => {
    if (!fpBatchId) return
    setFpLoading(true)
    try {
      const patterns = await getFPPatterns(fpBatchId)
      setFpPatterns(patterns)
    } catch {
      setFpPatterns([])
    } finally {
      setFpLoading(false)
    }
  }

  const completedBatches = batches.filter((b) => b.status === 'completed')

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading...</div>
  }

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Feedback</h1>

      {/* Batch Comparison */}
      <section className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="font-semibold mb-5">Batch Comparison</h2>
        <div className="flex flex-wrap items-end gap-4 mb-6">
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Batch A (baseline)</label>
            <select
              value={batchAId}
              onChange={(e) => setBatchAId(e.target.value)}
              className="text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 min-w-48"
            >
              <option value="">Select batch…</option>
              {completedBatches.map((b) => (
                <option key={b.batch_id} value={b.batch_id}>
                  {b.batch_id.slice(0, 12)}… ({b.dataset})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Batch B (new)</label>
            <select
              value={batchBId}
              onChange={(e) => setBatchBId(e.target.value)}
              className="text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 min-w-48"
            >
              <option value="">Select batch…</option>
              {completedBatches.map((b) => (
                <option key={b.batch_id} value={b.batch_id}>
                  {b.batch_id.slice(0, 12)}… ({b.dataset})
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={handleCompare}
            disabled={!batchAId || !batchBId || comparing}
            className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium transition-colors disabled:opacity-50"
          >
            {comparing ? 'Comparing…' : 'Compare'}
          </button>
        </div>

        {compareError && (
          <p className="text-sm text-red-600 dark:text-red-400 mb-4">{compareError}</p>
        )}

        {comparison && (
          <div className="space-y-6">
            {/* Metric deltas */}
            {comparison.metric_deltas.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">Metric Deltas</h3>
                <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
                      <tr>
                        <th className="px-3 py-2 text-left">Experiment</th>
                        <th className="px-3 py-2 text-left">Precision Δ</th>
                        <th className="px-3 py-2 text-left">Recall Δ</th>
                        <th className="px-3 py-2 text-left">F1 Δ</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                      {comparison.metric_deltas.map((row, i) => {
                        const precision_delta = row.precision_delta as number | undefined
                        const recall_delta = row.recall_delta as number | undefined
                        const f1_delta = row.f1_delta as number | undefined
                        const isRegression = (precision_delta ?? 0) < -0.05 || (recall_delta ?? 0) < -0.05
                        return (
                          <tr key={i} className={isRegression ? 'bg-red-50 dark:bg-red-950/30' : ''}>
                            <td className="px-3 py-2 font-mono text-xs">{String(row.experiment_id ?? '—')}</td>
                            <td className="px-3 py-2">
                              {precision_delta !== undefined ? <DeltaCell value={precision_delta} /> : '—'}
                            </td>
                            <td className="px-3 py-2">
                              {recall_delta !== undefined ? <DeltaCell value={recall_delta} /> : '—'}
                            </td>
                            <td className="px-3 py-2">
                              {f1_delta !== undefined ? <DeltaCell value={f1_delta} /> : '—'}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* FP Patterns from comparison */}
            {comparison.fp_patterns.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">FP Patterns</h3>
                <FPPatternsTable patterns={comparison.fp_patterns} />
              </div>
            )}

            {/* Stability */}
            <div>
              <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">Stability Analysis</h3>
              <p className="text-sm text-gray-400 dark:text-gray-500">
                Stability analysis details coming soon.
              </p>
            </div>
          </div>
        )}
      </section>

      {/* FP Pattern Browser */}
      <section className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="font-semibold mb-5">FP Pattern Browser</h2>
        <div className="flex items-end gap-4 mb-5">
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Select batch</label>
            <select
              value={fpBatchId}
              onChange={(e) => setFpBatchId(e.target.value)}
              className="text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 min-w-48"
            >
              <option value="">Select batch…</option>
              {completedBatches.map((b) => (
                <option key={b.batch_id} value={b.batch_id}>
                  {b.batch_id.slice(0, 12)}… ({b.dataset})
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={handleLoadFP}
            disabled={!fpBatchId || fpLoading}
            className="px-4 py-2 rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-sm font-medium transition-colors disabled:opacity-50"
          >
            {fpLoading ? 'Loading…' : 'Load Patterns'}
          </button>
        </div>

        {fpPatterns.length > 0 && <FPPatternsTable patterns={fpPatterns} />}
        {fpPatterns.length === 0 && fpBatchId && !fpLoading && (
          <p className="text-sm text-gray-400 dark:text-gray-500">No FP patterns found.</p>
        )}
      </section>
    </div>
  )
}

function FPPatternsTable({ patterns }: { patterns: FPPattern[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
          <tr>
            <th className="px-3 py-2 text-left">Model</th>
            <th className="px-3 py-2 text-left">Vuln Class</th>
            <th className="px-3 py-2 text-left">Pattern</th>
            <th className="px-3 py-2 text-left">Count</th>
            <th className="px-3 py-2 text-left">Suggested Action</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
          {patterns.map((p, i) => (
            <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
              <td className="px-3 py-2 font-mono text-xs">{p.model}</td>
              <td className="px-3 py-2 font-mono text-xs">{p.vuln_class}</td>
              <td className="px-3 py-2 text-gray-600 dark:text-gray-400 max-w-xs">{p.pattern}</td>
              <td className="px-3 py-2 text-center font-medium">{p.count}</td>
              <td className="px-3 py-2 text-gray-500 dark:text-gray-400 text-xs">{p.suggested_action}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
