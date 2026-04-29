import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  getFileTree,
  getLabels,
  getFileContent,
  listTemplates,
  previewInjection,
  injectVuln,
  getDataset,
  rematerializeDataset,
  ApiError,
  type Label,
  type FileTree as FileTreeData,
  type InjectionTemplate,
  type DatasetRow,
} from '../api/client'
import Breadcrumbs from '../components/Breadcrumbs'
import FileTree from '../components/FileTree'
import PageDescription from '../components/PageDescription'
import CodeViewer from '../components/CodeViewer'
import DiffViewer from '../components/DiffViewer'

type InjectStep = 1 | 2 | 3 | 4 | 5

// ─── Date formatting ──────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  } catch {
    return iso
  }
}

// ─── Copy button ──────────────────────────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <button
      onClick={handleCopy}
      title="Copy to clipboard"
      className="ml-1 text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors"
      data-testid="copy-button"
    >
      {copied ? '✓' : '⎘'}
    </button>
  )
}

// ─── Recipe summary ───────────────────────────────────────────────────────────

interface RecipeApp {
  template_id?: string
  target_file?: string
  seed?: string | number
}

interface RecipeData {
  templates_version?: string
  applications?: RecipeApp[]
}

function RecipeSummary({ recipeJson }: { recipeJson: string }) {
  let recipe: RecipeData = {}
  try {
    recipe = JSON.parse(recipeJson) as RecipeData
  } catch {
    return <p className="text-xs text-red-500">Invalid recipe JSON</p>
  }
  const apps = recipe.applications ?? []
  return (
    <div className="space-y-1">
      {recipe.templates_version && (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Templates version:{' '}
          <span className="font-mono text-gray-800 dark:text-gray-200">{recipe.templates_version}</span>
        </p>
      )}
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Applications: <span className="font-mono text-gray-800 dark:text-gray-200">{apps.length}</span>
      </p>
      {apps.length > 0 && (
        <details className="mt-1">
          <summary className="text-xs text-amber-600 dark:text-amber-400 cursor-pointer hover:underline">
            Show applications
          </summary>
          <div className="mt-2 max-h-48 overflow-y-auto rounded border border-gray-200 dark:border-gray-700 text-xs font-mono divide-y divide-gray-100 dark:divide-gray-700">
            {apps.map((app, idx) => (
              <div key={idx} className="px-3 py-1.5 flex flex-col gap-0.5">
                <span className="text-amber-700 dark:text-amber-400">{app.template_id ?? '—'}</span>
                <span className="text-gray-500 dark:text-gray-400">{app.target_file ?? '—'}</span>
                {app.seed !== undefined && (
                  <span className="text-gray-400 dark:text-gray-500">seed: {app.seed}</span>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

// ─── Origin card ──────────────────────────────────────────────────────────────

function OriginCard({
  dataset,
  onMaterialized,
}: {
  dataset: DatasetRow
  onMaterialized: (at: string) => void
}) {
  const [materializing, setMaterializing] = useState(false)
  const [materializeError, setMaterializeError] = useState<string | null>(null)
  const [localMaterializedAt, setLocalMaterializedAt] = useState(dataset.materialized_at)

  const handleMaterialize = async () => {
    setMaterializing(true)
    setMaterializeError(null)
    try {
      const result = await rematerializeDataset(dataset.name)
      setLocalMaterializedAt(result.materialized_at)
      onMaterialized(result.materialized_at)
    } catch (err) {
      if (err instanceof ApiError) {
        setMaterializeError(err.message)
      } else {
        setMaterializeError('Materialization failed. Please try again.')
      }
    } finally {
      setMaterializing(false)
    }
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
      <h2 className="font-semibold text-sm">
        {dataset.kind === 'git' ? 'Git origin' : dataset.kind === 'archive' ? 'Archive origin' : 'Derived from'}
      </h2>

      {dataset.kind === 'git' ? (
        <div className="space-y-2 text-sm">
          {dataset.origin_url && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 w-28 shrink-0">URL</span>
              {/^https?:\/\//.test(dataset.origin_url) ? (
                <a
                  href={dataset.origin_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-mono text-amber-600 dark:text-amber-400 hover:underline break-all"
                >
                  {dataset.origin_url}
                </a>
              ) : (
                <span className="font-mono text-gray-800 dark:text-gray-200 break-all">
                  {dataset.origin_url}
                </span>
              )}
            </div>
          )}
          {dataset.origin_commit && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 w-28 shrink-0">Commit</span>
              <span className="font-mono text-gray-800 dark:text-gray-200">
                {dataset.origin_commit.slice(0, 12)}
                <CopyButton text={dataset.origin_commit} />
              </span>
            </div>
          )}
          {dataset.origin_ref && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 w-28 shrink-0">Ref</span>
              <span className="text-xs text-gray-500 dark:text-gray-400 font-mono">{dataset.origin_ref}</span>
            </div>
          )}
          {dataset.cve_id && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 w-28 shrink-0">CVE</span>
              <Link
                to={`/cve-discovery?id=${encodeURIComponent(dataset.cve_id)}`}
                className="px-2 py-0.5 rounded-full bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200 text-xs font-mono hover:bg-red-200 dark:hover:bg-red-800 transition-colors"
              >
                {dataset.cve_id}
              </Link>
            </div>
          )}
        </div>
      ) : dataset.kind === 'archive' ? (
        <div className="space-y-2 text-sm">
          {dataset.archive_url ? (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 w-28 shrink-0">URL</span>
              {/^https?:\/\//.test(dataset.archive_url) ? (
                <a
                  href={dataset.archive_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-mono text-amber-600 dark:text-amber-400 hover:underline break-all"
                  data-testid="archive-url-link"
                >
                  {dataset.archive_url}
                </a>
              ) : (
                <span className="font-mono text-gray-800 dark:text-gray-200 break-all">
                  {dataset.archive_url}
                </span>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 w-28 shrink-0">URL</span>
              <span className="text-gray-400 dark:text-gray-500">—</span>
            </div>
          )}
          {dataset.archive_sha256 ? (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 w-28 shrink-0">Sha256</span>
              <span className="font-mono text-gray-800 dark:text-gray-200" data-testid="archive-sha256">
                {dataset.archive_sha256.slice(0, 12)}
                <CopyButton text={dataset.archive_sha256} />
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 w-28 shrink-0">Sha256</span>
              <span className="text-gray-400 dark:text-gray-500">—</span>
            </div>
          )}
          {dataset.archive_format && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 w-28 shrink-0">Format</span>
              <span className="font-mono text-gray-800 dark:text-gray-200" data-testid="archive-format">
                {dataset.archive_format}
              </span>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-2 text-sm">
          {dataset.base_dataset && (
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400 w-28 shrink-0">Base dataset</span>
              <Link
                to={`/datasets/${encodeURIComponent(dataset.base_dataset)}`}
                className="font-mono text-amber-600 dark:text-amber-400 hover:underline"
              >
                {dataset.base_dataset}
              </Link>
            </div>
          )}
          {dataset.recipe_json && (
            <div>
              <p className="text-gray-500 dark:text-gray-400 mb-1">Recipe</p>
              <RecipeSummary recipeJson={dataset.recipe_json} />
            </div>
          )}
        </div>
      )}

      {/* Timestamps */}
      <div className="border-t border-gray-100 dark:border-gray-700 pt-3 space-y-1 text-xs text-gray-500 dark:text-gray-400">
        <div className="flex gap-2">
          <span className="w-28 shrink-0">Created</span>
          <span>{formatDate(dataset.created_at)}</span>
        </div>
        <div className="flex gap-2">
          <span className="w-28 shrink-0">Materialized</span>
          <span>{localMaterializedAt ? formatDate(localMaterializedAt) : '—'}</span>
        </div>
      </div>

      {/* Materialization banner */}
      {localMaterializedAt === null && (
        <div className="rounded-lg border border-yellow-300 dark:border-yellow-700 bg-yellow-50 dark:bg-yellow-950/30 px-4 py-3 space-y-2">
          <p className="text-sm text-yellow-800 dark:text-yellow-200">
            This dataset is not currently materialized on this deployment.
          </p>
          <button
            onClick={handleMaterialize}
            disabled={materializing}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-yellow-600 hover:bg-yellow-700 text-white text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            data-testid="materialize-btn"
          >
            {materializing && (
              <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
              </svg>
            )}
            {materializing ? 'Materializing…' : 'Materialize now'}
          </button>
          {materializeError && (
            <p
              role="alert"
              className="text-xs text-red-700 dark:text-red-300"
              data-testid="materialize-error"
            >
              {materializeError}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Labels filter bar ────────────────────────────────────────────────────────

interface LabelFilters {
  cwe: string
  severity: string
  source: string
}

function LabelsFilterBar({
  filters,
  onChange,
}: {
  filters: LabelFilters
  onChange: (f: LabelFilters) => void
}) {
  return (
    <div className="flex flex-wrap gap-3 mb-4">
      <div className="flex items-center gap-1.5">
        <label htmlFor="filter-cwe" className="text-xs font-medium text-gray-500 dark:text-gray-400">
          CWE
        </label>
        <input
          id="filter-cwe"
          type="text"
          placeholder="e.g. CWE-89"
          value={filters.cwe}
          onChange={(e) => onChange({ ...filters, cwe: e.target.value })}
          className="text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 w-28"
          data-testid="filter-cwe"
        />
      </div>
      <div className="flex items-center gap-1.5">
        <label htmlFor="filter-severity" className="text-xs font-medium text-gray-500 dark:text-gray-400">
          Severity
        </label>
        <select
          id="filter-severity"
          value={filters.severity}
          onChange={(e) => onChange({ ...filters, severity: e.target.value })}
          className="text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1"
          data-testid="filter-severity"
        >
          <option value="">All</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
          <option value="info">Info</option>
        </select>
      </div>
      <div className="flex items-center gap-1.5">
        <label htmlFor="filter-source" className="text-xs font-medium text-gray-500 dark:text-gray-400">
          Source
        </label>
        <input
          id="filter-source"
          type="text"
          placeholder="e.g. manual"
          value={filters.source}
          onChange={(e) => onChange({ ...filters, source: e.target.value })}
          className="text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 w-28"
          data-testid="filter-source"
        />
      </div>
      {(filters.cwe || filters.severity || filters.source) && (
        <button
          onClick={() => onChange({ cwe: '', severity: '', source: '' })}
          className="text-xs text-amber-600 dark:text-amber-400 hover:underline"
          data-testid="filter-clear"
        >
          Clear filters
        </button>
      )}
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function DatasetDetail() {
  const { name: datasetName } = useParams<{ name: string }>()
  const [tree, setTree] = useState<FileTreeData>({})
  const [labels, setLabels] = useState<Label[]>([])
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [fileContent, setFileContent] = useState<{ content: string; language: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [datasetRow, setDatasetRow] = useState<DatasetRow | null>(null)

  // Labels filter state
  const [labelFilters, setLabelFilters] = useState<LabelFilters>({ cwe: '', severity: '', source: '' })

  // Injection workflow
  const [injectStep, setInjectStep] = useState<InjectStep | null>(null)
  const [templates, setTemplates] = useState<InjectionTemplate[]>([])
  const [selectedTemplate, setSelectedTemplate] = useState<InjectionTemplate | null>(null)
  const [injectFile, setInjectFile] = useState<string | null>(null)
  const [substitutions, setSubstitutions] = useState<Record<string, string>>({})
  const [preview, setPreview] = useState<{ before: string; after: string; language: string } | null>(null)
  const [injecting, setInjecting] = useState(false)
  const [injectSuccess, setInjectSuccess] = useState<string | null>(null)

  // Initial load: tree + dataset row
  useEffect(() => {
    if (!datasetName) return
    Promise.all([
      getFileTree(datasetName),
      getDataset(datasetName).catch(() => null),
    ])
      .then(([t, ds]) => {
        setTree(t)
        setDatasetRow(ds)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [datasetName])

  // Load labels whenever datasetName or filter state changes
  useEffect(() => {
    if (!datasetName) return
    getLabels(datasetName, {
      cwe: labelFilters.cwe || undefined,
      severity: labelFilters.severity || undefined,
      source: labelFilters.source || undefined,
    })
      .then(setLabels)
      .catch(() => null)
  }, [datasetName, labelFilters])


  useEffect(() => {
    if (!selectedFile || !datasetName) return
    getFileContent(datasetName, selectedFile)
      .then(setFileContent)
      .catch(() => null)
  }, [selectedFile, datasetName])

  const labelCounts = labels.reduce(
    (acc, l) => ({ ...acc, [l.file_path]: (acc[l.file_path] ?? 0) + 1 }),
    {} as Record<string, number>
  )

  const loadTemplates = async () => {
    if (templates.length > 0) return
    const t = await listTemplates()
    setTemplates(t)
  }

  const startInject = async () => {
    await loadTemplates()
    setInjectStep(1)
  }

  const handleTemplateSelect = (t: InjectionTemplate) => {
    setSelectedTemplate(t)
    const matches = t.description.match(/\{\{(\w+)\}\}/g) ?? []
    const placeholders = matches.map((m) => m.slice(2, -2))
    const initial = Object.fromEntries(placeholders.map((p) => [p, '']))
    setSubstitutions(initial)
    setInjectStep(2)
  }

  const handleInjectFileSelect = (path: string) => {
    setInjectFile(path)
    setInjectStep(3)
  }

  const handlePreview = async () => {
    if (!datasetName || !selectedTemplate || !injectFile) return
    try {
      const result = await previewInjection(datasetName, selectedTemplate.template_id, injectFile, substitutions)
      setPreview(result)
      setInjectStep(4)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Preview failed')
    }
  }

  const handleInject = async () => {
    if (!datasetName || !selectedTemplate || !injectFile) return
    setInjecting(true)
    try {
      const result = await injectVuln(datasetName, selectedTemplate.template_id, injectFile, substitutions)
      setInjectSuccess(result.label_id)
      setInjectStep(5)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Injection failed')
    } finally {
      setInjecting(false)
    }
  }

  const hasUnsavedSubstitutions = Object.values(substitutions).some((v) => v.trim() !== '')

  const handleCloseModal = () => {
    if (hasUnsavedSubstitutions && injectStep !== null && injectStep < 5) {
      const confirmed = window.confirm('Discard unsaved changes?')
      if (!confirmed) return
    }
    setInjectStep(null)
    setPreview(null)
    setInjectSuccess(null)
    setSubstitutions({})
    setSelectedTemplate(null)
    setInjectFile(null)
  }

  const handleLabelFiltersChange = (f: LabelFilters) => {
    setLabelFilters(f)
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading dataset...</div>
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
        {error}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs items={[{ label: 'Datasets', to: '/datasets' }, { label: datasetName ?? '' }]} />

      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold font-mono text-gray-900 dark:text-gray-100">{datasetName}</h1>
        <button
          onClick={startInject}
          className="px-4 py-2 bg-orange-600 hover:bg-orange-700 text-white rounded-lg text-sm font-medium transition-colors"
        >
          Inject Vulnerability
        </button>
      </div>
      <PageDescription>
        File tree, ground-truth labels, and code viewer for a single dataset.
        Inject synthetic vulnerabilities from templates to extend coverage beyond what the source already contains.
      </PageDescription>

      {/* Origin card */}
      {datasetRow && (
        <OriginCard
          dataset={datasetRow}
          onMaterialized={(at) => setDatasetRow((prev) => prev ? { ...prev, materialized_at: at } : prev)}
        />
      )}

      {/* Two-panel layout */}
      <div className="grid lg:grid-cols-3 gap-4">
        {/* File tree */}
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4 overflow-auto max-h-[600px]">
          <h2 className="font-semibold text-sm mb-3">Files</h2>
          <FileTree
            tree={tree}
            onSelect={setSelectedFile}
            labelCounts={labelCounts}
            selectedPath={selectedFile ?? undefined}
          />
        </div>

        {/* File viewer */}
        <div className="lg:col-span-2 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
          {selectedFile ? (
            <>
              <p className="text-xs font-mono text-gray-500 dark:text-gray-400 mb-3">{selectedFile}</p>
              {fileContent ? (
                <CodeViewer
                  content={fileContent.content}
                  language={fileContent.language}
                  maxHeight="550px"
                />
              ) : (
                <div className="flex items-center justify-center h-48 text-gray-400">Loading file...</div>
              )}
            </>
          ) : (
            <div className="flex items-center justify-center h-48 text-gray-400">
              Select a file to view
            </div>
          )}
        </div>
      </div>

      {/* Labels table */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="font-semibold mb-4">Labels ({labels.length})</h2>

        <LabelsFilterBar filters={labelFilters} onChange={handleLabelFiltersChange} />

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-gray-500 dark:text-gray-400">
              <tr>
                <th className="text-left pb-2">File</th>
                <th className="text-left pb-2">Lines</th>
                <th className="text-left pb-2">Vuln Class</th>
                <th className="text-left pb-2">Severity</th>
                <th className="text-left pb-2">CWE</th>
                <th className="text-left pb-2">Source</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {labels.map((l) => (
                <tr
                  key={l.label_id}
                  onClick={() => setSelectedFile(l.file_path)}
                  title={l.description}
                  className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
                >
                  <td className="py-2 font-mono text-xs text-amber-600 dark:text-amber-400 max-w-xs truncate">
                    {l.file_path}
                  </td>
                  <td className="py-2 font-mono text-xs text-gray-500">
                    {l.line_start}–{l.line_end}
                  </td>
                  <td className="py-2 font-mono text-xs">{l.vuln_class}</td>
                  <td className="py-2 text-xs">{l.severity}</td>
                  <td className="py-2 font-mono text-xs text-gray-500">{l.cwe ?? '—'}</td>
                  <td className="py-2 text-xs text-gray-500">{l.source}</td>
                </tr>
              ))}
              {labels.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-gray-400">No labels yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Injection workflow modal */}
      {injectStep !== null && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white dark:bg-gray-800 rounded-xl w-full max-w-3xl max-h-[90vh] overflow-y-auto shadow-2xl">
            <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
              <h2 className="font-semibold">
                Inject Vulnerability — Step {injectStep}/5
              </h2>
              <button
                onClick={handleCloseModal}
                className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xl"
              >
                ×
              </button>
            </div>

            <div className="p-6">
              {injectStep === 1 && (
                <div className="space-y-3">
                  <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">Select a vulnerability template:</p>
                  <div className="space-y-2 max-h-80 overflow-y-auto">
                    {templates.map((t) => (
                      <button
                        key={t.template_id}
                        onClick={() => handleTemplateSelect(t)}
                        className="w-full text-left p-3 rounded-lg border border-gray-200 dark:border-gray-700 hover:bg-amber-50 dark:hover:bg-amber-950 transition-colors"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-medium text-sm">{t.vuln_class}</span>
                          <div className="flex gap-2">
                            <span className="text-xs font-mono text-gray-500">{t.language}</span>
                            <span className="text-xs font-mono text-gray-500">{t.cwe}</span>
                            <span className="text-xs text-orange-600">{t.severity}</span>
                          </div>
                        </div>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 truncate">{t.description}</p>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {injectStep === 2 && (
                <div className="space-y-4">
                  <p className="text-sm text-gray-600 dark:text-gray-400">
                    Select a target file (filtered to {selectedTemplate?.language}):
                  </p>
                  <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 max-h-80 overflow-y-auto">
                    <FileTree
                      tree={tree}
                      onSelect={handleInjectFileSelect}
                      selectedPath={injectFile ?? undefined}
                    />
                  </div>
                </div>
              )}

              {injectStep === 3 && (
                <div className="space-y-4">
                  <p className="text-sm text-gray-600 dark:text-gray-400">
                    Fill in substitutions for <code className="font-mono">{selectedTemplate?.template_id}</code>:
                  </p>
                  {Object.keys(substitutions).length === 0 ? (
                    <p className="text-sm text-gray-400">No substitutions required.</p>
                  ) : (
                    Object.entries(substitutions).map(([key, val]) => (
                      <div key={key}>
                        <label className="text-sm font-medium text-gray-700 dark:text-gray-300 block mb-1">
                          {'{{'}{key}{'}}'}
                        </label>
                        <input
                          type="text"
                          value={val}
                          onChange={(e) => setSubstitutions((s) => ({ ...s, [key]: e.target.value }))}
                          className="w-full text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 font-mono"
                        />
                      </div>
                    ))
                  )}
                  <button
                    onClick={handlePreview}
                    className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium transition-colors"
                  >
                    Preview Injection
                  </button>
                </div>
              )}

              {injectStep === 4 && preview && (
                <div className="space-y-4">
                  <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">Review the diff before confirming:</p>
                  <DiffViewer before={preview.before} after={preview.after} language={preview.language} />
                  <button
                    onClick={handleInject}
                    disabled={injecting}
                    className="px-4 py-2 rounded-lg bg-orange-600 hover:bg-orange-700 text-white text-sm font-medium transition-colors disabled:opacity-50"
                  >
                    {injecting ? 'Injecting…' : 'Confirm & Inject'}
                  </button>
                </div>
              )}

              {injectStep === 5 && (
                <div className="text-center py-8">
                  <div className="text-4xl mb-4">✓</div>
                  <p className="font-semibold text-green-600 dark:text-green-400">Injection successful!</p>
                  <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
                    New label ID: <code className="font-mono">{injectSuccess}</code>
                  </p>
                  <button
                    onClick={() => { setInjectStep(null); setPreview(null); setInjectSuccess(null) }}
                    className="mt-4 px-4 py-2 rounded-lg bg-gray-100 dark:bg-gray-700 text-sm"
                  >
                    Close
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
