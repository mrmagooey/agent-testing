import { useState, useEffect } from 'react'
import {
  listExperiments,
  compareExperiments,
  getFPPatterns,
  type Experiment,
  type FPPattern,
} from '../api/client'
import { PageLoadingSpinner } from '../components/Skeleton'
import PageDescription from '../components/PageDescription'

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
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [experimentAId, setExperimentAId] = useState('')
  const [experimentBId, setExperimentBId] = useState('')
  const [loading, setLoading] = useState(true)
  const [comparing, setComparing] = useState(false)
  const [comparison, setComparison] = useState<{
    metric_deltas: Record<string, unknown>[]
    fp_patterns: FPPattern[]
    stability: Record<string, unknown>
  } | null>(null)
  const [compareError, setCompareError] = useState<string | null>(null)

  const [fpExperimentId, setFpExperimentId] = useState('')
  const [fpPatterns, setFpPatterns] = useState<FPPattern[]>([])
  const [fpLoading, setFpLoading] = useState(false)

  useEffect(() => {
    listExperiments()
      .then(setExperiments)
      .catch(() => null)
      .finally(() => setLoading(false))
  }, [])

  const handleCompare = async () => {
    if (!experimentAId || !experimentBId) return
    setComparing(true)
    setCompareError(null)
    try {
      const result = await compareExperiments(experimentAId, experimentBId)
      setComparison(result)
    } catch (err) {
      setCompareError(err instanceof Error ? err.message : 'Comparison failed')
    } finally {
      setComparing(false)
    }
  }

  const handleLoadFP = async () => {
    if (!fpExperimentId) return
    setFpLoading(true)
    try {
      const patterns = await getFPPatterns(fpExperimentId)
      setFpPatterns(patterns)
    } catch {
      setFpPatterns([])
    } finally {
      setFpLoading(false)
    }
  }

  const completedExperiments = experiments.filter((b) => b.status === 'completed')

  if (loading) return <PageLoadingSpinner />

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Feedback</h1>
      <PageDescription>
        Experiment-to-experiment accuracy deltas, stability across reruns, and recurring false-positive patterns mined from completed runs.
        Use it to quantify whether a prompt, model, or strategy change actually improved results against a baseline.
      </PageDescription>

      {/* Experiment Comparison */}
      <section className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="font-semibold mb-5">Experiment Comparison</h2>
        <div className="flex flex-wrap items-end gap-4 mb-6">
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Experiment A (baseline)</label>
            <select
              value={experimentAId}
              onChange={(e) => setExperimentAId(e.target.value)}
              className="text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 min-w-48"
            >
              <option value="">Select experiment…</option>
              {completedExperiments.map((b) => (
                <option key={b.experiment_id} value={b.experiment_id}>
                  {b.experiment_id.slice(0, 12)}… ({b.dataset})
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Experiment B (new)</label>
            <select
              value={experimentBId}
              onChange={(e) => setExperimentBId(e.target.value)}
              className="text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 min-w-48"
            >
              <option value="">Select experiment…</option>
              {completedExperiments.map((b) => (
                <option key={b.experiment_id} value={b.experiment_id}>
                  {b.experiment_id.slice(0, 12)}… ({b.dataset})
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={handleCompare}
            disabled={!experimentAId || !experimentBId || comparing}
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
                            <td className="px-3 py-2 font-mono text-xs">
                              <span className="flex items-center gap-1.5">
                                {isRegression && (
                                  <span title="Regression" className="text-red-600 dark:text-red-400 shrink-0">
                                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.962-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
                                    </svg>
                                  </span>
                                )}
                                {String(row.experiment_id ?? '—')}
                              </span>
                            </td>
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
          </div>
        )}
      </section>

      {/* FP Pattern Browser */}
      <section className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="font-semibold mb-5">FP Pattern Browser</h2>
        <div className="flex items-end gap-4 mb-5">
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Select experiment</label>
            <select
              value={fpExperimentId}
              onChange={(e) => setFpExperimentId(e.target.value)}
              className="text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 min-w-48"
            >
              <option value="">Select experiment…</option>
              {completedExperiments.map((b) => (
                <option key={b.experiment_id} value={b.experiment_id}>
                  {b.experiment_id.slice(0, 12)}… ({b.dataset})
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={handleLoadFP}
            disabled={!fpExperimentId || fpLoading}
            className="px-4 py-2 rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-sm font-medium transition-colors disabled:opacity-50"
          >
            {fpLoading ? 'Loading…' : 'Load Patterns'}
          </button>
        </div>

        {fpPatterns.length > 0 && <FPPatternsTable patterns={fpPatterns} />}
        {fpPatterns.length === 0 && fpExperimentId && !fpLoading && (
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
              <td className="px-3 py-2 text-gray-600 dark:text-gray-400 max-w-xs">
                <span className="flex items-center gap-1.5">
                  <svg className="w-3.5 h-3.5 shrink-0 text-orange-500 dark:text-orange-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" />
                  </svg>
                  {p.pattern}
                </span>
              </td>
              <td className="px-3 py-2 text-center font-medium">{p.count}</td>
              <td className="px-3 py-2 text-gray-500 dark:text-gray-400 text-xs">{p.suggested_action}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
