import { useState, useEffect } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { compareRuns, type RunComparison, type Finding } from '../api/client'
import Breadcrumbs from '../components/Breadcrumbs'
import CodeViewer from '../components/CodeViewer'

function delta(a?: number, b?: number): React.ReactNode {
  if (a === undefined || b === undefined) return null
  const diff = b - a
  const cls = diff > 0 ? 'text-green-600 dark:text-green-400' : diff < 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-400'
  return (
    <span className={`ml-2 text-sm ${cls}`}>
      {diff > 0 ? '+' : ''}{diff.toFixed(3)}
    </span>
  )
}

function FindingCard({ finding, expandedId, onToggle }: {
  finding: Finding
  expandedId: string | null
  onToggle: (id: string) => void
}) {
  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
      <button
        onClick={() => onToggle(finding.finding_id)}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{finding.title}</span>
          <span className="text-xs text-gray-500 font-mono">{finding.vuln_class}</span>
        </div>
        <span className="text-gray-400 text-xs">{expandedId === finding.finding_id ? '▲' : '▼'}</span>
      </button>
      {expandedId === finding.finding_id && (
        <div className="px-4 pb-4 bg-gray-50 dark:bg-gray-900">
          <CodeViewer content={finding.description} language="markdown" maxHeight="200px" />
        </div>
      )}
    </div>
  )
}

export default function RunCompare() {
  const { id: batchId } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const runAId = searchParams.get('a') ?? ''
  const runBId = searchParams.get('b') ?? ''

  const [comparison, setComparison] = useState<RunComparison | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'both' | 'only_a' | 'only_b'>('both')

  useEffect(() => {
    if (!batchId || !runAId || !runBId) { setLoading(false); return }
    compareRuns(batchId, runAId, runBId)
      .then(setComparison)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [batchId, runAId, runBId])

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading comparison...</div>
  }

  if (error || !comparison) {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
        {error ?? 'Could not load comparison'}
      </div>
    )
  }

  const { run_a, run_b, found_by_both, only_in_a, only_in_b } = comparison

  const tabs = [
    { key: 'both' as const, label: `Found by Both (${found_by_both.length})` },
    { key: 'only_a' as const, label: `Only in A (${only_in_a.length})` },
    { key: 'only_b' as const, label: `Only in B (${only_in_b.length})` },
  ]

  const tabFindings = activeTab === 'both' ? found_by_both : activeTab === 'only_a' ? only_in_a : only_in_b

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: 'Dashboard', to: '/' },
          { label: batchId ?? '', to: `/batches/${batchId}` },
          { label: 'Compare' },
        ]}
      />

      {/* Header comparison */}
      <div className="grid grid-cols-2 gap-4">
        {[
          { label: 'Run A', run: run_a },
          { label: 'Run B', run: run_b },
        ].map(({ label, run }) => (
          <div key={label} className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
            <h2 className="font-bold text-lg mb-3">{label}</h2>
            <dl className="space-y-1 text-sm">
              {[
                ['Model', run.model],
                ['Strategy', run.strategy],
                ['Tools', run.tool_variant],
                ['Profile', run.profile],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <dt className="text-gray-500 dark:text-gray-400">{k}</dt>
                  <dd className="font-mono text-xs">{v}</dd>
                </div>
              ))}
              <div className="pt-2 mt-2 border-t border-gray-100 dark:border-gray-700 space-y-1">
                {(['precision', 'recall', 'f1'] as const).map((m) => (
                  <div key={m} className="flex justify-between">
                    <dt className="text-gray-500 dark:text-gray-400 capitalize">{m}</dt>
                    <dd className="font-mono text-xs">
                      {run[m]?.toFixed(3) ?? '—'}
                      {label === 'Run B' && delta(run_a[m], run_b[m])}
                    </dd>
                  </div>
                ))}
              </div>
            </dl>
          </div>
        ))}
      </div>

      {/* Summary */}
      <div className="bg-indigo-50 dark:bg-indigo-950 rounded-lg px-5 py-3 text-sm text-indigo-800 dark:text-indigo-200">
        Run A found <strong>{only_in_a.length}</strong> vulns that B missed.
        Run B found <strong>{only_in_b.length}</strong> vulns that A missed.{' '}
        <strong>{found_by_both.length}</strong> found by both.
      </div>

      {/* Tabs */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <div className="flex gap-1 mb-5 border-b border-gray-200 dark:border-gray-700">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
                activeTab === t.key
                  ? 'border-indigo-600 text-indigo-600 dark:text-indigo-400'
                  : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="space-y-3">
          {tabFindings.map((f) => (
            <FindingCard
              key={f.finding_id}
              finding={f}
              expandedId={expandedId}
              onToggle={(id) => setExpandedId((prev) => prev === id ? null : id)}
            />
          ))}
          {tabFindings.length === 0 && (
            <p className="text-sm text-gray-400 dark:text-gray-500 text-center py-8">
              No findings in this category.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
