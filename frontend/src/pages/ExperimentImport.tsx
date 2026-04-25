import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Breadcrumbs from '../components/Breadcrumbs'
import { importBundle, ApiError, type ImportSummary } from '../api/client'

type ConflictPolicy = 'reject' | 'rename' | 'merge'

export default function ExperimentImport() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [conflictPolicy, setConflictPolicy] = useState<ConflictPolicy>('reject')
  const [uploading, setUploading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [summary, setSummary] = useState<ImportSummary | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(true)
  }

  const handleDragLeave = () => {
    setDragging(false)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const dropped = e.dataTransfer.files[0]
    if (dropped) {
      setFile(dropped)
      setSummary(null)
      setErrorMsg(null)
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0] ?? null
    if (selected) {
      setFile(selected)
      setSummary(null)
      setErrorMsg(null)
    }
  }

  const handleUpload = async () => {
    if (!file || uploading) return
    setUploading(true)
    setProgress(0)
    setSummary(null)
    setErrorMsg(null)
    try {
      const result = await importBundle(file, conflictPolicy, (pct) => {
        setProgress(pct)
      })
      setSummary(result)
      setProgress(100)
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMsg(err.message)
      } else {
        setErrorMsg('An unexpected error occurred.')
      }
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <Breadcrumbs items={[{ label: 'Dashboard', to: '/' }, { label: 'Import Experiment' }]} />

      <h1 className="text-2xl font-bold">Import Experiment</h1>

      {/* Dropzone */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        role="button"
        aria-label="Drop zone for experiment bundle"
        className={[
          'border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors',
          dragging
            ? 'border-amber-500 bg-amber-50 dark:bg-amber-950/20'
            : 'border-gray-300 dark:border-gray-600 hover:border-amber-400 hover:bg-gray-50 dark:hover:bg-gray-800/50',
        ].join(' ')}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".zip,.secrev.zip"
          className="hidden"
          onChange={handleFileChange}
          aria-label="Select experiment bundle file"
        />
        {file ? (
          <div className="space-y-1">
            <p className="font-medium text-gray-900 dark:text-gray-100">{file.name}</p>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {(file.size / 1024 / 1024).toFixed(2)} MB — click or drop to change
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-gray-600 dark:text-gray-300 font-medium">
              Drop a <code>.secrev.zip</code> bundle here
            </p>
            <p className="text-sm text-gray-400 dark:text-gray-500">or click to select a file</p>
          </div>
        )}
      </div>

      {/* Conflict policy */}
      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-1">
          Conflict policy
        </legend>
        {(['reject', 'rename', 'merge'] as const).map((policy) => (
          <label
            key={policy}
            className="flex items-center gap-2 cursor-pointer text-sm text-gray-700 dark:text-gray-300"
          >
            <input
              type="radio"
              name="conflict_policy"
              value={policy}
              checked={conflictPolicy === policy}
              onChange={() => setConflictPolicy(policy)}
              className="accent-amber-600"
            />
            <span className="capitalize">{policy}</span>
            {policy === 'reject' && (
              <span className="text-gray-400 dark:text-gray-500 text-xs">— fail if experiment ID already exists</span>
            )}
            {policy === 'rename' && (
              <span className="text-gray-400 dark:text-gray-500 text-xs">— import with a new unique ID</span>
            )}
            {policy === 'merge' && (
              <span className="text-gray-400 dark:text-gray-500 text-xs">— add runs to the existing experiment</span>
            )}
          </label>
        ))}
      </fieldset>

      {/* Upload button */}
      <button
        onClick={handleUpload}
        disabled={!file || uploading}
        className="px-6 py-2 bg-amber-600 hover:bg-amber-700 text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {uploading ? 'Uploading…' : 'Upload'}
      </button>

      {/* Progress bar */}
      {uploading && (
        <div className="space-y-1" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-3 rounded-full overflow-hidden bg-gray-200 dark:bg-gray-700">
            <div
              className="bg-amber-500 h-full transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 text-right">{progress}%</p>
        </div>
      )}

      {/* Error banner */}
      {errorMsg && (
        <div
          role="alert"
          className="rounded-lg border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-950 px-4 py-3 text-sm text-red-700 dark:text-red-300"
        >
          {errorMsg}
        </div>
      )}

      {/* Success summary */}
      {summary && (
        <div className="rounded-xl border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/30 p-6 space-y-4">
          <h2 className="text-lg font-semibold text-green-800 dark:text-green-200">Import successful</h2>

          <div className="space-y-1 text-sm text-gray-700 dark:text-gray-300">
            <div>
              <span className="font-medium">Experiment: </span>
              <Link
                to={`/experiments/${summary.experiment_id}`}
                className="text-amber-600 hover:text-amber-700 underline font-mono"
              >
                {summary.experiment_id}
              </Link>
            </div>
            {summary.renamed_from && (
              <div className="text-amber-700 dark:text-amber-400 text-xs">
                Renamed from <span className="font-mono">{summary.renamed_from}</span>
              </div>
            )}
            <div>
              <span className="font-medium">Runs imported: </span>
              {summary.runs_imported}
            </div>
            {summary.runs_skipped > 0 && (
              <div>
                <span className="font-medium">Runs skipped: </span>
                {summary.runs_skipped}
              </div>
            )}
            <div>
              <span className="font-medium">Findings indexed: </span>
              {summary.findings_indexed}
            </div>
          </div>

          {(summary.datasets_imported > 0 || summary.dataset_labels_imported > 0) && (
            <div className="space-y-1 text-sm text-gray-700 dark:text-gray-300">
              {summary.datasets_imported > 0 && (
                <div>
                  <span className="font-medium">Datasets imported: </span>
                  {summary.datasets_imported}
                </div>
              )}
              {summary.dataset_labels_imported > 0 && (
                <div>
                  <span className="font-medium">Dataset labels imported: </span>
                  {summary.dataset_labels_imported}
                </div>
              )}
            </div>
          )}

          {summary.datasets_rehydrated && summary.datasets_rehydrated.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-semibold text-green-700 dark:text-green-400 uppercase tracking-wide">
                Rehydrated datasets
              </p>
              <div className="flex flex-wrap gap-2">
                {summary.datasets_rehydrated.map((ds) => (
                  <span
                    key={ds}
                    className="px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200 text-xs font-mono"
                    data-testid="chip-rehydrated"
                  >
                    Rehydrated: {ds}
                  </span>
                ))}
              </div>
            </div>
          )}

          {summary.datasets_missing.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-semibold text-amber-700 dark:text-amber-400 uppercase tracking-wide">
                Datasets not available
              </p>
              <div className="flex flex-wrap gap-2">
                {summary.datasets_missing.map((ds) => (
                  <span
                    key={ds}
                    className="px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900 text-amber-800 dark:text-amber-200 text-xs font-mono"
                    data-testid="chip-missing"
                  >
                    Not available: {ds}
                  </span>
                ))}
              </div>
            </div>
          )}

          {summary.warnings.length > 0 && (
            <ul className="space-y-1 text-xs text-gray-500 dark:text-gray-400 list-disc list-inside">
              {summary.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
