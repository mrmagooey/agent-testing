import { useState, useEffect, useMemo } from 'react'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import {
  getExperimentResults,
  cancelExperiment,
  type Run,
  type Finding,
  type ExperimentResults,
} from '../api/client'
import { useExperiment } from '../hooks/useExperiment'
import Breadcrumbs from '../components/Breadcrumbs'
import ProgressBar from '../components/ProgressBar'
import MatrixTable from '../components/MatrixTable'
import MatrixFilterBar from '../components/MatrixFilterBar'
import FindingsExplorer from '../components/FindingsExplorer'
import DimensionChart from '../components/DimensionChart'
import DownloadButton from '../components/DownloadButton'
import ExportMenu from '../components/ExportMenu'
import PageDescription from '../components/PageDescription'
import BenchmarkScorecardPanel from '../components/BenchmarkScorecardPanel'
import { parseMatrixFilter, serializeMatrixFilter, applyMatrixFilter, clearMatrixFilter } from '../lib/matrixFilter'

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

function CancelConfirmModal({
  onConfirm,
  onCancel,
  confirming,
  error,
}: {
  onConfirm: () => void
  onCancel: () => void
  confirming: boolean
  error?: string | null
}) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-sm w-full p-6">
        <h3 className="font-semibold text-gray-900 dark:text-gray-100 mb-2">Stop all pending runs?</h3>
        <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">
          This will cancel all pending and running jobs in this experiment. Completed runs will not be affected.
        </p>
        {error && (
          <div role="alert" className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 p-3 mb-4 text-sm text-red-700 dark:text-red-300">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-3">
          <button
            onClick={onCancel}
            disabled={confirming}
            className="px-4 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            Keep running
          </button>
          <button
            onClick={onConfirm}
            disabled={confirming}
            className="px-4 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white text-sm font-medium transition-colors disabled:opacity-50"
          >
            {confirming ? 'Cancelling…' : 'Stop experiment'}
          </button>
        </div>
      </div>
    </div>
  )
}

function TokenMeter({ runs }: { runs: Run[] }) {
  const totalCost = runs.reduce((sum, r) => sum + (r.cost_usd ?? 0), 0)
  const totalRuns = runs.length
  const avgCost = totalRuns > 0 ? totalCost / totalRuns : 0

  return (
    <div className="flex flex-wrap items-center gap-4 text-sm mt-4 pt-4 border-t border-gray-100 dark:border-gray-700">
      <div className="flex items-center gap-2">
        <span className="text-gray-500 dark:text-gray-300">Experiment total</span>
        <span className="font-semibold font-mono text-gray-900 dark:text-gray-100">
          ${totalCost.toFixed(2)}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-gray-500 dark:text-gray-300">Avg/run</span>
        <span className="font-mono text-gray-700 dark:text-gray-300">
          ${avgCost.toFixed(3)}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-gray-500 dark:text-gray-300">Runs</span>
        <span className="font-mono text-gray-700 dark:text-gray-300">{totalRuns}</span>
      </div>
    </div>
  )
}

export default function ExperimentDetail() {
  const { id: experimentId } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { experiment, loading, error, refetch } = useExperiment(experimentId)
  const [results, setResults] = useState<ExperimentResults | null>(null)
  const [resultsLoading, setResultsLoading] = useState(false)
  const [selectedRuns, setSelectedRuns] = useState<string[]>([])
  const [cancelling, setCancelling] = useState(false)
  const [showCancelModal, setShowCancelModal] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)

  const filter = useMemo(() => parseMatrixFilter(searchParams), [searchParams])
  const filteredRuns = useMemo(() => (results ? applyMatrixFilter(results.runs, filter) : []), [results, filter])

  const isTerminal = experiment && ['completed', 'failed', 'cancelled'].includes(experiment.status)

  useEffect(() => {
    if (!experimentId || !isTerminal) return
    setResultsLoading(true)
    getExperimentResults(experimentId)
      .then(setResults)
      .catch(() => null)
      .finally(() => setResultsLoading(false))
  }, [experimentId, isTerminal])

  const handleCancelRequest = () => {
    setShowCancelModal(true)
  }

  const handleCancelConfirm = async () => {
    if (!experimentId || cancelling) return
    setCancelError(null)
    setCancelling(true)
    try {
      await cancelExperiment(experimentId)
      setShowCancelModal(false)
      // Refetch immediately so the status badge flips to 'cancelled'
      // without waiting for the 10s poll cycle.
      await refetch()
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : 'Cancel failed')
    } finally {
      setCancelling(false)
    }
  }

  const handleCompare = () => {
    if (selectedRuns.length === 2) {
      navigate(`/experiments/${experimentId}/compare?a=${selectedRuns[0]}&b=${selectedRuns[1]}`)
    }
  }

  if (loading && !experiment) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading experiment...</div>
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
        {error}
      </div>
    )
  }

  if (!experiment) return null

  return (
    <div className="space-y-6">
      {showCancelModal && (
        <CancelConfirmModal
          onConfirm={handleCancelConfirm}
          onCancel={() => { setShowCancelModal(false); setCancelError(null) }}
          confirming={cancelling}
          error={cancelError}
        />
      )}

      <Breadcrumbs items={[{ label: 'Dashboard', to: '/' }, { label: experiment.experiment_id }]} />

      <PageDescription>
        Live progress, cost, and per-cell results for a single experiment, plus cancel and download controls.
        Click any matrix row to inspect a run in detail, or select two runs to diff their findings side-by-side.
      </PageDescription>

      {/* Header */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h1 className="text-xl font-bold font-mono">{experiment.experiment_id}</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">Dataset: {experiment.dataset}</p>
          </div>
          <div className="flex items-center gap-3">
            <span className={`px-3 py-1 rounded-full text-sm font-medium ${STATUS_BADGE[experiment.status] ?? ''}`}>
              {experiment.status}
            </span>
            {!isTerminal && (
              <button
                onClick={handleCancelRequest}
                disabled={cancelling}
                className="px-3 py-1 rounded-lg text-sm border border-red-300 dark:border-red-700 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
            )}
            <button
              onClick={() => navigate(`/compare?a_experiment=${experimentId}`)}
              className="px-3 py-1 rounded-lg text-sm border border-amber-300 dark:border-amber-700 text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950 transition-colors"
            >
              Compare with another
            </button>
            {isTerminal && experimentId && <ExportMenu experimentId={experimentId} />}
          </div>
        </div>

        {/* Progress */}
        <ProgressBar
          completed={experiment.completed_runs}
          running={experiment.running_runs}
          pending={experiment.pending_runs}
          failed={experiment.failed_runs}
          total={experiment.total_runs}
        />

        {/* Cost vs cap */}
        <div className="mt-4 flex items-center gap-4 text-sm">
          <span className="text-gray-600 dark:text-gray-400">
            Cost: <strong>${experiment.total_cost_usd.toFixed(2)}</strong>
          </span>
          {experiment.spend_cap_usd && (
            <span className="text-gray-600 dark:text-gray-400">
              Cap: <strong>${experiment.spend_cap_usd.toFixed(2)}</strong>
              {experiment.total_cost_usd / experiment.spend_cap_usd > 0.8 && (
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
          {/* Experiment Matrix */}
          <section className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
            <h2 className="text-lg font-semibold mb-4">Experiment Matrix</h2>
            <MatrixFilterBar
              runs={results.runs}
              value={filter}
              onChange={(next) => setSearchParams(serializeMatrixFilter(next), { replace: true })}
            />
            {filteredRuns.length === 0 && results.runs.length > 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 py-12 border-2 border-dashed border-gray-200 dark:border-gray-700 rounded-lg text-center">
                <p className="text-gray-500 dark:text-gray-400 font-medium">No runs match these filters</p>
                <button
                  onClick={() => setSearchParams(serializeMatrixFilter(clearMatrixFilter()), { replace: true })}
                  className="px-4 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
                >
                  Clear filters
                </button>
              </div>
            ) : (
              <MatrixTable
                runs={filteredRuns}
                onSelect={setSelectedRuns}
                selectedIds={selectedRuns}
              />
            )}
          </section>

          {/* Dimension Charts */}
          <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <DimensionChart
              data={buildModelChart(filteredRuns)}
              xKey="model"
              yKey="avg_f1"
              title="Model Comparison (Avg F1)"
            />
            <DimensionChart
              data={(() => {
                const byStrategy: Record<string, number[]> = {}
                for (const r of filteredRuns) {
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
              <thead className="text-gray-500 dark:text-gray-300">
                <tr>
                  <th className="text-left pb-2">Model</th>
                  <th className="text-right pb-2">Runs</th>
                  <th className="text-right pb-2">Total Cost</th>
                  <th className="text-right pb-2">Avg Cost/Run</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {Object.entries(
                  filteredRuns.reduce(
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
                    <td className="py-2 text-right text-gray-600 dark:text-gray-300">{count}</td>
                    <td className="py-2 text-right font-medium">${total.toFixed(2)}</td>
                    <td className="py-2 text-right text-gray-600 dark:text-gray-300">
                      ${(total / count).toFixed(3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <TokenMeter runs={filteredRuns} />
          </section>

          {/* Findings Explorer — intentionally uses full run set */}
          {experimentId && (
            <section className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
              <h2 className="text-lg font-semibold mb-4">Findings</h2>
              <FindingsExplorer experimentId={experimentId} findings={results.findings} datasetName={experiment?.dataset} />
            </section>
          )}

          {/* Benchmark Scorecard — only shown for benchmark datasets */}
          <BenchmarkScorecardPanel scorecards={results.benchmark_scorecards} />
        </>
      )}

      {/* Sticky action bar (item 7) */}
      {selectedRuns.length > 0 && (
        <div className="fixed bottom-0 left-0 right-0 z-40 bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 shadow-lg">
          <div className="max-w-screen-xl mx-auto px-4 py-3 flex items-center gap-3">
            <span className="text-sm text-gray-600 dark:text-gray-300">
              {selectedRuns.length} run{selectedRuns.length > 1 ? 's' : ''} selected
            </span>
            <div className="ml-auto flex items-center gap-2">
              {selectedRuns.length === 2 && (
                <button
                  onClick={handleCompare}
                  className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none"
                >
                  Compare Selected
                </button>
              )}
              {experimentId && <DownloadButton experimentId={experimentId} label="Download Results" />}
              <button
                onClick={() => setSelectedRuns([])}
                className="px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none"
              >
                Clear
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
