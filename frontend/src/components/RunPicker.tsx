import { useState, useEffect, useId } from 'react'
import { listExperiments, listRuns, type Experiment, type Run } from '../api/client'

interface RunPickerProps {
  label: string
  selectedExperiment: string
  selectedRun: string
  onExperimentChange: (experimentId: string) => void
  onRunChange: (runId: string) => void
  disabled?: boolean
}

export default function RunPicker({
  label,
  selectedExperiment,
  selectedRun,
  onExperimentChange,
  onRunChange,
  disabled = false,
}: RunPickerProps) {
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [experimentsLoading, setExperimentsLoading] = useState(true)
  const [runsLoading, setRunsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const experimentSelectId = useId()
  const runSelectId = useId()

  useEffect(() => {
    listExperiments()
      .then(setExperiments)
      .catch((e) => setError(e.message))
      .finally(() => setExperimentsLoading(false))
  }, [])

  useEffect(() => {
    if (!selectedExperiment) {
      setRuns([])
      return
    }
    setRunsLoading(true)
    listRuns(selectedExperiment)
      .then(setRuns)
      .catch((e) => setError(e.message))
      .finally(() => setRunsLoading(false))
  }, [selectedExperiment])

  function runLabel(run: Run): string {
    const parts = [run.model, run.strategy, run.tool_variant]
    if (run.tool_extensions && run.tool_extensions.length > 0) {
      parts.push(run.tool_extensions.join('+'))
    }
    return parts.filter(Boolean).join(' / ')
  }

  return (
    <div className="space-y-3">
      <p className="text-sm font-semibold text-gray-700 dark:text-gray-300">{label}</p>

      {error && (
        <p className="text-xs text-red-600 dark:text-red-400">{error}</p>
      )}

      <div className="space-y-2">
        <div>
          <label
            htmlFor={experimentSelectId}
            className="block text-xs text-muted-foreground mb-1"
          >
            Experiment
          </label>
          <select
            id={experimentSelectId}
            value={selectedExperiment}
            onChange={(e) => {
              onExperimentChange(e.target.value)
              onRunChange('')
            }}
            disabled={disabled || experimentsLoading}
            className="w-full rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm px-2 py-1.5 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-amber-500 disabled:opacity-50"
          >
            <option value="">
              {experimentsLoading ? 'Loading…' : '— select experiment —'}
            </option>
            {experiments.map((exp) => (
              <option key={exp.experiment_id} value={exp.experiment_id}>
                {exp.experiment_id} ({exp.dataset ?? '—'}, {exp.status})
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            htmlFor={runSelectId}
            className="block text-xs text-muted-foreground mb-1"
          >
            Run
          </label>
          <select
            id={runSelectId}
            value={selectedRun}
            onChange={(e) => onRunChange(e.target.value)}
            disabled={disabled || !selectedExperiment || runsLoading}
            className="w-full rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm px-2 py-1.5 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-amber-500 disabled:opacity-50"
          >
            <option value="">
              {runsLoading
                ? 'Loading…'
                : !selectedExperiment
                  ? '— pick experiment first —'
                  : '— select run —'}
            </option>
            {runs.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {runLabel(run)}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  )
}
