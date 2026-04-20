import { useState, useEffect } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { compareRuns, type RunComparison, type Finding } from '../api/client'
import Breadcrumbs from '../components/Breadcrumbs'
import CodeViewer from '../components/CodeViewer'
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

  const costDiff = run_a.cost_usd !== undefined && run_b.cost_usd !== undefined
    ? run_b.cost_usd - run_a.cost_usd
    : undefined

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: 'Dashboard', to: '/' },
          { label: batchId ?? '', to: `/batches/${batchId}` },
          { label: 'Compare' },
        ]}
      />

      <PageDescription>
        Side-by-side diff of two runs' findings — split into <em>found by both</em>, <em>only in A</em>, and <em>only in B</em>.
        Use it to see exactly what a model or strategy change caught or missed relative to a baseline.
      </PageDescription>

      {/* Header comparison — shadcn Card for each run panel */}
      <div className="grid grid-cols-2 gap-4">
        {[
          { label: 'Run A', run: run_a },
          { label: 'Run B', run: run_b },
        ].map(({ label, run }) => (
          <Card key={label}>
            <CardHeader>
              <CardTitle>{label}</CardTitle>
            </CardHeader>
            <CardContent>
              <dl className="space-y-1 text-sm">
                {[
                  ['Model', run.model],
                  ['Strategy', run.strategy],
                  ['Tools', run.tool_variant],
                  ['Extensions', (run.tool_extensions ?? []).join(', ') || '—'],
                  ['Profile', run.profile],
                ].map(([k, v]) => (
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
                          <DeltaBadge a={run_a[m]} b={run_b[m]} />
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

      {/* Summary — Venn + cost delta. Kept as indigo callout panel (not a Card) per spec */}
      <div className="bg-indigo-50 dark:bg-indigo-950 rounded-lg px-5 py-4">
        <div className="flex flex-wrap items-center gap-6">
          {/* Hand-rolled SVG Venn — preserved */}
          <div className="shrink-0">
            <VennDiagram aOnly={only_in_a.length} overlap={found_by_both.length} bOnly={only_in_b.length} />
          </div>
          <div className="text-sm text-indigo-800 dark:text-indigo-200 space-y-1">
            <p>Run A found <strong>{only_in_a.length}</strong> vulns that B missed.</p>
            <p>Run B found <strong>{only_in_b.length}</strong> vulns that A missed.</p>
            <p><strong>{found_by_both.length}</strong> found by both.</p>
          </div>
          {/* Cost delta — wrapped in a Badge for the diff */}
          {costDiff !== undefined && run_a.cost_usd !== undefined && run_b.cost_usd !== undefined && (
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
                A: ${run_a.cost_usd.toFixed(4)} / B: ${run_b.cost_usd.toFixed(4)}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Findings tabs — shadcn Card wrapper */}
      <Card>
        <CardContent className="pt-6">
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
        </CardContent>
      </Card>
    </div>
  )
}
