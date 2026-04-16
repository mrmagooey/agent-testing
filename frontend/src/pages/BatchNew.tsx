import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  listDatasets,
  listModels,
  listStrategies,
  listProfiles,
  submitBatch,
  type Dataset,
  type BatchConfig,
} from '../api/client'
import { useEstimate } from '../hooks/useEstimate'
import CostEstimate from '../components/CostEstimate'

function CheckboxGroup({
  label,
  options,
  selected,
  onChange,
}: {
  label: string
  options: string[]
  selected: string[]
  onChange: (vals: string[]) => void
}) {
  const toggle = (val: string) => {
    onChange(
      selected.includes(val) ? selected.filter((v) => v !== val) : [...selected, val]
    )
  }
  return (
    <div>
      <p className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{label}</p>
      <div className="flex flex-wrap gap-2">
        {options.map((opt) => (
          <label key={opt} className="flex items-center gap-1.5 cursor-pointer text-sm">
            <input
              type="checkbox"
              checked={selected.includes(opt)}
              onChange={() => toggle(opt)}
              className="rounded"
            />
            <span className="font-mono text-xs text-gray-700 dark:text-gray-300">{opt}</span>
          </label>
        ))}
      </div>
    </div>
  )
}

export default function BatchNew() {
  const navigate = useNavigate()
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [models, setModels] = useState<string[]>([])
  const [strategies, setStrategies] = useState<string[]>([])
  const [profiles, setProfiles] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Form state
  const [selectedDataset, setSelectedDataset] = useState('')
  const [selectedModels, setSelectedModels] = useState<string[]>([])
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([])
  const [selectedProfile, setSelectedProfile] = useState('')
  const [toolVariants, setToolVariants] = useState<string[]>(['with_tools', 'without_tools'])
  const [verification, setVerification] = useState<string[]>(['none'])
  const [repetitions, setRepetitions] = useState(1)
  const [spendCapInput, setSpendCapInput] = useState('')

  const config: Partial<BatchConfig> = {
    dataset: selectedDataset || undefined,
    models: selectedModels,
    strategies: selectedStrategies,
    profiles: selectedProfile ? [selectedProfile] : undefined,
    tool_variants: toolVariants,
    verification,
    repetitions,
  }

  const { estimate, loading: estimateLoading } = useEstimate(config)

  useEffect(() => {
    Promise.all([listDatasets(), listModels(), listStrategies(), listProfiles()])
      .then(([ds, ms, ss, ps]) => {
        setDatasets(ds)
        setModels(ms)
        setStrategies(ss)
        setProfiles(ps)
        if (ps.length > 0) setSelectedProfile(ps[0])
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedDataset) { setError('Please select a dataset'); return }
    if (selectedModels.length === 0) { setError('Select at least one model'); return }
    if (selectedStrategies.length === 0) { setError('Select at least one strategy'); return }

    setSubmitting(true)
    setError(null)
    try {
      const batchConfig: BatchConfig = {
        dataset: selectedDataset,
        models: selectedModels,
        strategies: selectedStrategies,
        profiles: selectedProfile ? [selectedProfile] : [],
        tool_variants: toolVariants,
        verification,
        repetitions,
        spend_cap_usd: spendCapInput ? parseFloat(spendCapInput) : undefined,
      }
      const batch = await submitBatch(batchConfig)
      navigate(`/batches/${batch.batch_id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Submission failed')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading...</div>
  }

  return (
    <div className="max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-6">New Batch</h1>

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
                className="w-full text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2"
              >
                <option value="">Select a dataset…</option>
                {datasets.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.name} ({d.label_count} labels, {d.languages.join('/')})
                  </option>
                ))}
              </select>
            </div>

            {/* Models */}
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
              <h2 className="font-semibold mb-3">Models</h2>
              <CheckboxGroup
                label=""
                options={models}
                selected={selectedModels}
                onChange={setSelectedModels}
              />
            </div>

            {/* Strategies */}
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
              <h2 className="font-semibold mb-3">Strategies</h2>
              <CheckboxGroup
                label=""
                options={strategies}
                selected={selectedStrategies}
                onChange={setSelectedStrategies}
              />
            </div>

            {/* Profile */}
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
              <h2 className="font-semibold mb-3">Profile</h2>
              <div className="flex flex-wrap gap-3">
                {profiles.map((p) => (
                  <label key={p} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="profile"
                      value={p}
                      checked={selectedProfile === p}
                      onChange={() => setSelectedProfile(p)}
                    />
                    <span className="text-sm font-mono text-gray-700 dark:text-gray-300">{p}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Dimensions */}
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
              <h2 className="font-semibold mb-4">Dimensions</h2>
              <div className="space-y-4">
                <CheckboxGroup
                  label="Tool variants"
                  options={['with_tools', 'without_tools']}
                  selected={toolVariants}
                  onChange={setToolVariants}
                />
                <CheckboxGroup
                  label="Verification"
                  options={['none', 'with_verification']}
                  selected={verification}
                  onChange={setVerification}
                />
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
              </div>
            </div>

            {/* Spend cap */}
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
          </div>

          {/* Sidebar */}
          <div className="space-y-4">
            <CostEstimate estimate={estimate} loading={estimateLoading} />
            <button
              type="submit"
              disabled={submitting}
              className="w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 text-white font-semibold transition-colors disabled:opacity-50"
            >
              {submitting ? 'Submitting…' : 'Submit Batch'}
            </button>
          </div>
        </div>
      </form>
    </div>
  )
}
