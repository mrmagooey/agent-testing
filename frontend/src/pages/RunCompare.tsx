import React, { useState, useEffect } from 'react'
import { Link, useParams, useSearchParams, useNavigate } from 'react-router-dom'
import { compareRunsCross, type RunComparison, type Finding } from '../api/client'
import Breadcrumbs from '../components/Breadcrumbs'
import CodeViewer from '../components/CodeViewer'
import RunPicker from '../components/RunPicker'
import PageDescription from '../components/PageDescription'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

function DeltaBadge({ a, b }: { a?: number; b?: number }) {
  if (a === undefined || b === undefined) return null
  const diff = b - a
  const formatted = `${diff > 0 ? '+' : ''}${diff.toFixed(3)}`
  if (diff > 0) {
    return (
      <Badge className="ml-2 bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300 border-green-300 dark:border-green-700 font-mono text-xs" variant="outline">
        {formatted}
      </Badge>
    )
  }
  if (diff < 0) {
    return (
      <Badge className="ml-2 bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 border-red-300 dark:border-red-700 font-mono text-xs" variant="outline">
        {formatted}
      </Badge>
    )
  }
  return (
    <Badge className="ml-2 font-mono text-xs" variant="secondary">
      {formatted}
    </Badge>
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

function VennDiagram({ aOnly, overlap, bOnly }: { aOnly: number; overlap: number; bOnly: number }) {
  const w = 160, h = 80, r = 36, cx1 = 52, cx2 = 108, cy = 40
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-label="Venn diagram">
      <circle cx={cx1} cy={cy} r={r} fill="#6366f1" fillOpacity={0.25} stroke="#6366f1" strokeWidth={1.5} />
      <circle cx={cx2} cy={cy} r={r} fill="#6366f1" fillOpacity={0.25} stroke="#6366f1" strokeWidth={1.5} />
      <text x={cx1 - r / 2 - 4} y={cy + 1} textAnchor="middle" dominantBaseline="middle"
        className="fill-indigo-700 dark:fill-indigo-300" fontSize={13} fontWeight="bold">{aOnly}</text>
      <text x={(cx1 + cx2) / 2} y={cy + 1} textAnchor="middle" dominantBaseline="middle"
        className="fill-indigo-900 dark:fill-indigo-100" fontSize={12} fontWeight="bold">{overlap}</text>
      <text x={cx2 + r / 2 + 4} y={cy + 1} textAnchor="middle" dominantBaseline="middle"
        className="fill-indigo-700 dark:fill-indigo-300" fontSize={13} fontWeight="bold">{bOnly}</text>
      <text x={cx1 - r / 2 - 4} y={cy + 17} textAnchor="middle" dominantBaseline="middle"
        className="fill-indigo-500 dark:fill-indigo-400" fontSize={8}>only A</text>
      <text x={(cx1 + cx2) / 2} y={cy + 17} textAnchor="middle" dominantBaseline="middle"
        className="fill-indigo-500 dark:fill-indigo-400" fontSize={8}>both</text>
      <text x={cx2 + r / 2 + 4} y={cy + 17} textAnchor="middle" dominantBaseline="middle"
        className="fill-indigo-500 dark:fill-indigo-400" fontSize={8}>only B</text>
    </svg>
  )
}

function DatasetMismatchBanner({ warnings }: { warnings: string[] }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-amber-300 dark:border-amber-600 bg-amber-50 dark:bg-amber-950 px-4 py-3 text-amber-800 dark:text-amber-200"
      data-testid="dataset-mismatch-banner"
    >
      <p className="text-sm font-semibold mb-1">Dataset mismatch</p>
      {warnings.map((w, i) => (
        <p key={i} className="text-sm">{w}</p>
      ))}
    </div>
  )
}

export default function RunCompare() {
  const { id: experimentId } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()

  // Detect whether we are on the legacy route (/experiments/:id/compare?a=&b=)
  // or the new cross-experiment route (/compare?a_experiment=&a_run=&b_experiment=&b_run=).
  const isLegacyRoute = experimentId !== undefined

  // Resolve effective experiment/run IDs from either URL shape.
  const initialAExperiment = isLegacyRoute
    ? (experimentId ?? '')
    : (searchParams.get('a_experiment') ?? '')
  const initialBExperiment = isLegacyRoute
    ? (experimentId ?? '')
    : (searchParams.get('b_experiment') ?? '')
  const initialARun = isLegacyRoute
    ? (searchParams.get('a') ?? '')
    : (searchParams.get('a_run') ?? '')
  const initialBRun = isLegacyRoute
    ? (searchParams.get('b') ?? '')
    : (searchParams.get('b_run') ?? '')

  const [aExperiment, setAExperiment] = useState(initialAExperiment)
  const [bExperiment, setBExperiment] = useState(initialBExperiment)
  const [aRun, setARun] = useState(initialARun)
  const [bRun, setBRun] = useState(initialBRun)

  const [comparison, setComparison] = useState<RunComparison | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'both' | 'only_a' | 'only_b'>('both')

  const canCompare = Boolean(aExperiment && bExperiment && aRun && bRun)

  useEffect(() => {
    if (!canCompare) { setComparison(null); return }

    setLoading(true)
    setError(null)
    setComparison(null)

    compareRunsCross({ aExperiment, aRun, bExperiment, bRun })
      .then(setComparison)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [aExperiment, bExperiment, aRun, bRun, canCompare])

  // Sync picker selections back into the URL on the cross-experiment route
  // so deep links work.
  function handlePickerChange(
    field: 'a_experiment' | 'b_experiment' | 'a_run' | 'b_run',
    value: string,
  ) {
    const next = new URLSearchParams(searchParams)
    next.set(field, value)
    navigate({ search: next.toString() }, { replace: true })
    if (field === 'a_experiment') setAExperiment(value)
    if (field === 'b_experiment') setBExperiment(value)
    if (field === 'a_run') setARun(value)
    if (field === 'b_run') setBRun(value)
  }

  const breadcrumbs = isLegacyRoute
    ? [
        { label: 'Dashboard', to: '/' },
        { label: aExperiment || experimentId || '', to: `/experiments/${aExperiment || experimentId}` },
        { label: 'Compare' },
      ]
    : [{ label: 'Dashboard', to: '/' }, { label: 'Compare' }]

  return (
    <div className="space-y-6">
      <Breadcrumbs items={breadcrumbs} />

      <PageDescription>
        Side-by-side diff of two runs' findings — split into <em>found by both</em>, <em>only in A</em>, and <em>only in B</em>.
        Use it to see exactly what a model or strategy change caught or missed relative to a baseline.
      </PageDescription>

      {/* Pickers — only shown on /compare (cross-experiment route) */}
      {!isLegacyRoute && (
        <Card>
          <CardContent className="pt-6">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
              <RunPicker
                label="Run A"
                selectedExperiment={aExperiment}
                selectedRun={aRun}
                onExperimentChange={(v) => handlePickerChange('a_experiment', v)}
                onRunChange={(v) => handlePickerChange('a_run', v)}
              />
              <RunPicker
                label="Run B"
                selectedExperiment={bExperiment}
                selectedRun={bRun}
                onExperimentChange={(v) => handlePickerChange('b_experiment', v)}
                onRunChange={(v) => handlePickerChange('b_run', v)}
              />
            </div>
          </CardContent>
        </Card>
      )}

      {loading && (
        <div className="flex items-center justify-center h-32 text-gray-400">Loading comparison...</div>
      )}

      {!loading && error && (
        <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {!loading && !error && !canCompare && (
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 p-8 text-center text-muted-foreground text-sm">
          Select two runs above to compare them.
        </div>
      )}

      {!loading && comparison && (
        <>
          {comparison.dataset_mismatch && comparison.warnings.length > 0 && (
            <DatasetMismatchBanner warnings={comparison.warnings} />
          )}

          {/* Header comparison — shadcn Card for each run panel */}
          <div className="grid grid-cols-2 gap-4">
            {[
              { label: 'Run A', run: comparison.run_a },
              { label: 'Run B', run: comparison.run_b },
            ].map(({ label, run }) => (
              <Card key={label}>
                <CardHeader>
                  <CardTitle>{label}</CardTitle>
                </CardHeader>
                <CardContent>
                  <dl className="space-y-1 text-sm">
                    {([
                      ['Experiment', run.experiment_name
                        ? <Link to={`/experiments/${run.experiment_id}`} className="text-indigo-600 dark:text-indigo-400 hover:underline font-mono text-xs">{run.experiment_name}</Link>
                        : null],
                      ['Dataset', run.dataset || '—'],
                      ['Model', run.model],
                      ['Strategy', run.strategy],
                      ['Tools', run.tool_variant],
                      ['Extensions', (run.tool_extensions ?? []).join(', ') || '—'],
                      ['Profile', run.profile],
                    ] as [string, React.ReactNode][]).filter(([, v]) => v !== null).map(([k, v]) => (
                      <div key={k} className="flex justify-between">
                        <dt className="text-muted-foreground">{k}</dt>
                        <dd className="font-mono text-xs">{v}</dd>
                      </div>
                    ))}
                    <div className="pt-2 mt-2 border-t border-gray-100 dark:border-gray-700 space-y-1">
                      {(['precision', 'recall', 'f1'] as const).map((m) => (
                        <div key={m} className="flex justify-between items-center">
                          <dt className="text-muted-foreground capitalize">{m}</dt>
                          <dd className="font-mono text-xs flex items-center">
                            {run[m]?.toFixed(3) ?? '—'}
                            {label === 'Run B' && (
                              <DeltaBadge a={comparison.run_a[m]} b={comparison.run_b[m]} />
                            )}
                          </dd>
                        </div>
                      ))}
                    </div>
                  </dl>
                </CardContent>
              </Card>
            ))}
          </div>

          {/* Summary — Venn + cost delta */}
          <div className="bg-indigo-50 dark:bg-indigo-950 rounded-lg px-5 py-4">
            <div className="flex flex-wrap items-center gap-6">
              <div className="shrink-0">
                <VennDiagram
                  aOnly={comparison.only_in_a.length}
                  overlap={comparison.found_by_both.length}
                  bOnly={comparison.only_in_b.length}
                />
              </div>
              <div className="text-sm text-indigo-800 dark:text-indigo-200 space-y-1">
                <p>Run A found <strong>{comparison.only_in_a.length}</strong> vulns that B missed.</p>
                <p>Run B found <strong>{comparison.only_in_b.length}</strong> vulns that A missed.</p>
                <p><strong>{comparison.found_by_both.length}</strong> found by both.</p>
              </div>
              {comparison.run_a.cost_usd !== undefined && comparison.run_b.cost_usd !== undefined && (() => {
                const costDiff = comparison.run_b.cost_usd! - comparison.run_a.cost_usd!
                return (
                  <div className="ml-auto text-sm text-indigo-800 dark:text-indigo-200 text-right">
                    <p className="text-xs opacity-70 mb-1">Cost delta (B − A)</p>
                    <Badge
                      variant="outline"
                      className={`font-mono font-bold text-base px-3 py-1 ${
                        costDiff > 0
                          ? 'border-red-400 text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950'
                          : costDiff < 0
                          ? 'border-emerald-400 text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950'
                          : 'border-gray-300 text-gray-500'
                      }`}
                    >
                      {costDiff >= 0 ? '+' : ''}${costDiff.toFixed(4)}
                    </Badge>
                    <p className="text-xs opacity-60 mt-1">
                      A: ${comparison.run_a.cost_usd!.toFixed(4)} / B: ${comparison.run_b.cost_usd!.toFixed(4)}
                    </p>
                  </div>
                )
              })()}
            </div>
          </div>

          {/* Findings tabs */}
          <Card>
            <CardContent className="pt-6">
              {(() => {
                const tabs = [
                  { key: 'both' as const, label: `Found by Both (${comparison.found_by_both.length})` },
                  { key: 'only_a' as const, label: `Only in A (${comparison.only_in_a.length})` },
                  { key: 'only_b' as const, label: `Only in B (${comparison.only_in_b.length})` },
                ]
                const tabFindings =
                  activeTab === 'both'
                    ? comparison.found_by_both
                    : activeTab === 'only_a'
                    ? comparison.only_in_a
                    : comparison.only_in_b
                return (
                  <>
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
                        <p className="text-sm text-muted-foreground text-center py-8">
                          No findings in this category.
                        </p>
                      )}
                    </div>
                  </>
                )
              })()}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
