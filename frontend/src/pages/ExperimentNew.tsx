import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  listDatasets,
  listStrategiesFull,
  parseUnavailableModelsError,
  type Dataset,
  type StrategySummary,
  type CostEstimate as CostEstimateType,
} from '../api/client'
import CostEstimate from '../components/CostEstimate'
import PageDescription from '../components/PageDescription'

/**
 * Generate the power-set of a list of strings.
 * Example: ["a", "b"] => [[], ["a"], ["b"], ["a", "b"]]
 * @deprecated Tool extension sets are now baked into strategies. Kept for tests.
 */
export function generatePowerSet(items: string[]): string[][] {
  const result: string[][] = []
  for (let i = 0; i < 2 ** items.length; i++) {
    const subset: string[] = []
    for (let j = 0; j < items.length; j++) {
      if ((i >> j) & 1) {
        subset.push(items[j])
      }
    }
    result.push(subset)
  }
  return result
}

function StrategyCard({ strategy, selected, onToggle }: {
  strategy: StrategySummary
  selected: boolean
  onToggle: () => void
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`w-full text-left p-3 rounded-lg border transition-colors ${
        selected
          ? 'border-amber-400 dark:border-amber-600 bg-amber-50 dark:bg-amber-950'
          : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600'
      }`}
      data-testid="strategy-card"
      data-selected={selected}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
              {strategy.name}
            </span>
            {strategy.is_builtin ? (
              <span className="flex-shrink-0 inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300">
                builtin
              </span>
            ) : (
              <span className="flex-shrink-0 inline-flex items-center px-1.5 py-0.5 rounded-full text-xs font-medium bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300">
                user
              </span>
            )}
          </div>
          <p className="text-xs font-mono text-gray-500 dark:text-gray-400 truncate">{strategy.id}</p>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            Shape: <span className="font-mono">{strategy.orchestration_shape}</span>
          </p>
        </div>
        <div className={`flex-shrink-0 w-4 h-4 rounded-full border-2 mt-0.5 ${
          selected
            ? 'border-amber-500 bg-amber-500'
            : 'border-gray-300 dark:border-gray-600'
        }`}>
          {selected && (
            <svg className="w-full h-full text-white" viewBox="0 0 16 16" fill="currentColor">
              <path d="M13.78 4.22a.75.75 0 010 1.06l-7.25 7.25a.75.75 0 01-1.06 0L2.22 9.28a.75.75 0 011.06-1.06L6 10.94l6.72-6.72a.75.75 0 011.06 0z" />
            </svg>
          )}
        </div>
      </div>
    </button>
  )
}

export default function ExperimentNew() {
  const navigate = useNavigate()
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [strategies, setStrategies] = useState<StrategySummary[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [submitAttempted, setSubmitAttempted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [unavailableError, setUnavailableError] = useState<Array<{ id: string; status: string; reason?: string }> | null>(null)
  const [allowUnavailable, setAllowUnavailable] = useState(false)
  const [estimate, setEstimate] = useState<CostEstimateType | null>(null)
  const [estimateLoading, setEstimateLoading] = useState(false)

  const [selectedDataset, setSelectedDataset] = useState('')
  const [datasetVersion, setDatasetVersion] = useState('latest')
  const [selectedStrategyIds, setSelectedStrategyIds] = useState<string[]>([])
  const [repetitions, setRepetitions] = useState(1)
  const [spendCapInput, setSpendCapInput] = useState('')

  // Debounced estimate
  useEffect(() => {
    if (selectedStrategyIds.length === 0 || !selectedDataset) {
      setEstimate(null)
      return
    }
    setEstimateLoading(true)
    const timer = setTimeout(async () => {
      try {
        const experimentId = `estimate-${Date.now()}`
        const body = {
          matrix: {
            experiment_id: experimentId,
            dataset_name: selectedDataset,
            dataset_version: datasetVersion,
            strategy_ids: selectedStrategyIds,
            num_repetitions: repetitions,
          },
          target_kloc: 10.0,
        }
        const result = await fetch('/api/experiments/estimate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
        if (result.ok) {
          setEstimate(await result.json() as CostEstimateType)
        } else {
          setEstimate(null)
        }
      } catch {
        setEstimate(null)
      } finally {
        setEstimateLoading(false)
      }
    }, 400)
    return () => clearTimeout(timer)
  }, [selectedStrategyIds, selectedDataset, datasetVersion, repetitions])

  useEffect(() => {
    Promise.all([listDatasets(), listStrategiesFull()])
      .then(([ds, ss]) => {
        setDatasets(ds)
        setStrategies(ss)
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const datasetError = submitAttempted && !selectedDataset ? 'Please select a dataset' : undefined
  const strategiesError = submitAttempted && selectedStrategyIds.length === 0 ? 'Select at least one strategy' : undefined

  const isValid = !!selectedDataset && selectedStrategyIds.length > 0

  const doSubmit = async (overrideAllowUnavailable?: boolean) => {
    setSubmitting(true)
    setError(null)
    setUnavailableError(null)
    const shouldAllow = overrideAllowUnavailable ?? allowUnavailable
    try {
      const experimentId = `exp-${Date.now()}`
      const body: Record<string, unknown> = {
        experiment_id: experimentId,
        dataset_name: selectedDataset,
        dataset_version: datasetVersion,
        strategy_ids: selectedStrategyIds,
        num_repetitions: repetitions,
        ...(spendCapInput ? { max_experiment_cost_usd: parseFloat(spendCapInput) } : {}),
        ...(shouldAllow ? { allow_unavailable_models: true } : {}),
      }
      const res = await fetch('/api/experiments', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        let resBody: Record<string, unknown> = {}
        try { resBody = await res.json() as Record<string, unknown> } catch { /* ignore */ }
        const detail = resBody.detail as Record<string, unknown> | undefined
        if (detail && detail.error === 'unavailable_models') {
          setUnavailableError(detail.models as Array<{ id: string; status: string; reason?: string }>)
          return
        }
        setError(typeof detail === 'string' ? detail : `Submit failed (${res.status})`)
        return
      }
      const data = await res.json() as { experiment_id: string }
      navigate(`/experiments/${data.experiment_id}`)
    } catch (err) {
      const parsed = parseUnavailableModelsError(err)
      if (parsed) {
        setUnavailableError(parsed.models)
      } else {
        setError(err instanceof Error ? err.message : 'Submission failed')
      }
    } finally {
      setSubmitting(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitAttempted(true)
    if (!isValid) return
    await doSubmit()
  }

  const handleSubmitWithOverride = async () => {
    setAllowUnavailable(true)
    await doSubmit(true)
  }

  const toggleStrategy = (id: string) => {
    setSelectedStrategyIds((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    )
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading...</div>
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">New Experiment</h1>
      <PageDescription>
        An experiment runs a set of strategies against a labelled dataset. Each strategy encapsulates
        its own model, tools, prompts, and verification config — pick one or more strategies below.
      </PageDescription>
      <div className="h-6" />

      {error && (
        <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit}>
        <div className="grid lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-6">
            {/* Dataset */}
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
              <h2 className="font-semibold mb-3">Dataset</h2>
              <select
                value={selectedDataset}
                onChange={(e) => setSelectedDataset(e.target.value)}
                className={`w-full text-sm rounded-lg border px-3 py-2 bg-white dark:bg-gray-900 ${
                  datasetError
                    ? 'border-red-400 dark:border-red-600'
                    : 'border-gray-200 dark:border-gray-700'
                }`}
              >
                <option value="">Select a dataset…</option>
                {datasets.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name} ({d.label_count} labels{d.languages.length > 0 ? `, ${d.languages.join('/')}` : ''})
                  </option>
                ))}
              </select>
              {datasetError && (
                <p className="mt-1.5 text-xs text-red-600 dark:text-red-400">{datasetError}</p>
              )}
            </div>

            {/* Strategies */}
            <div className={`bg-white dark:bg-gray-800 rounded-xl border p-5 ${
              strategiesError ? 'border-red-400 dark:border-red-600' : 'border-gray-200 dark:border-gray-700'
            }`}>
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-semibold">Strategies</h2>
                {selectedStrategyIds.length > 0 && (
                  <span className="px-1.5 py-0.5 rounded-full text-xs font-semibold bg-amber-100 dark:bg-amber-900 text-amber-700 dark:text-amber-300">
                    {selectedStrategyIds.length} selected
                  </span>
                )}
              </div>
              {strategies.length === 0 ? (
                <p className="text-sm text-gray-400">No strategies available.{' '}
                  <a href="/strategies/new" className="text-amber-600 hover:underline">Create one</a>.
                </p>
              ) : (
                <div className="grid sm:grid-cols-2 gap-2">
                  {strategies.map((s) => (
                    <StrategyCard
                      key={s.id}
                      strategy={s}
                      selected={selectedStrategyIds.includes(s.id)}
                      onToggle={() => toggleStrategy(s.id)}
                    />
                  ))}
                </div>
              )}
              {strategiesError && (
                <p className="mt-1.5 text-xs text-red-600 dark:text-red-400">{strategiesError}</p>
              )}
            </div>

            {/* Options */}
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
              <h2 className="font-semibold mb-4">Options</h2>
              <div className="space-y-4">
                <div>
                  <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1 block">
                    Repetitions
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={repetitions}
                    onChange={(e) => setRepetitions(parseInt(e.target.value, 10) || 1)}
                    className="w-24 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2"
                  />
                </div>

                <div>
                  <label className="flex items-center gap-2 cursor-pointer" data-testid="allow-unavailable-label">
                    <input
                      type="checkbox"
                      checked={allowUnavailable}
                      onChange={(e) => setAllowUnavailable(e.target.checked)}
                      className="rounded"
                      data-testid="allow-unavailable-checkbox"
                    />
                    <span className="text-sm text-gray-700 dark:text-gray-300">
                      Allow unavailable models (override submit check)
                    </span>
                  </label>
                </div>
              </div>
            </div>

            {/* Spend Cap */}
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
              <h2 className="font-semibold mb-3">Spend Cap (USD)</h2>
              <input
                type="number"
                step="0.01"
                min="0"
                value={spendCapInput}
                onChange={(e) => setSpendCapInput(e.target.value)}
                placeholder={
                  estimate
                    ? `Suggested: $${(estimate.estimated_cost_usd * 1.2).toFixed(2)}`
                    : 'e.g. 10.00'
                }
                className="w-48 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2"
              />
            </div>

            {/* Unavailable models error */}
            {unavailableError && (
              <div
                className="p-3 rounded-lg bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300 text-sm"
                data-testid="unavailable-models-error"
              >
                <p className="font-semibold mb-1">Some strategy models are unavailable:</p>
                <ul className="list-disc list-inside text-xs space-y-0.5 mb-3">
                  {unavailableError.map((m) => (
                    <li key={m.id}>
                      <span className="font-mono">{m.id}</span> — {m.status}
                      {m.reason ? ` (${m.reason})` : ''}
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  onClick={handleSubmitWithOverride}
                  disabled={submitting}
                  className="px-3 py-1.5 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-xs font-semibold transition-colors disabled:opacity-50"
                  data-testid="submit-with-override-btn"
                >
                  {submitting ? 'Submitting…' : 'Submit with override'}
                </button>
              </div>
            )}
          </div>

          <div className="space-y-4">
            <CostEstimate estimate={estimate} loading={estimateLoading} />
            <button
              type="submit"
              disabled={submitting || (submitAttempted && !isValid)}
              className="w-full py-3 rounded-xl bg-amber-600 hover:bg-amber-700 text-white font-semibold transition-colors disabled:opacity-50"
            >
              {submitting ? 'Submitting…' : 'Submit Experiment'}
            </button>
            {submitAttempted && !isValid && (
              <p className="text-xs text-red-600 dark:text-red-400 text-center">
                Fill in all required fields above
              </p>
            )}
          </div>
        </div>
      </form>
    </div>
  )
}
