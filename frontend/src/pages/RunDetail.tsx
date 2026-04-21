import { useState, useEffect, useRef } from 'react'
import { useParams, useSearchParams, useLocation } from 'react-router-dom'
import { getRun, getFileContent, reclassifyFinding, type Run, type Finding, type ToolCall, type Message, type PromptSnapshot } from '../api/client'
import Breadcrumbs from '../components/Breadcrumbs'
import CodeViewer from '../components/CodeViewer'
import ConversationViewer from '../components/ConversationViewer'
import DownloadButton from '../components/DownloadButton'
import EmptyState from '../components/EmptyState'
import PromptInjectionViewer from '../components/PromptInjectionViewer'
import PageDescription from '../components/PageDescription'
import { PageLoadingSpinner } from '../components/Skeleton'
import { SEVERITY_COLORS, MATCH_STATUS_COLORS, metricTone } from '../constants/colors'

const PAGE_SIZE = 25

function HeroMetricCard({ label, value }: { label: string; value?: number }) {
  const { cls } = metricTone(value)
  return (
    <div className={`rounded-xl border border-gray-200 dark:border-gray-700 p-6 text-center ${cls || 'bg-white dark:bg-gray-800'}`}>
      <p className="text-sm font-medium mb-2 opacity-80">{label}</p>
      <p className="text-5xl font-bold tracking-tight">
        {value !== undefined ? value.toFixed(3) : '—'}
      </p>
    </div>
  )
}

function SecondaryMetricCard({ label, value, kind }: {
  label: string
  value?: number
  kind?: 'lower-is-better'
}) {
  const { cls } = metricTone(value, kind ?? 'higher-is-better')
  return (
    <div className={`rounded-lg border border-gray-200 dark:border-gray-700 p-4 text-center ${cls || 'bg-white dark:bg-gray-800'}`}>
      <p className="text-xs opacity-70 mb-1">{label}</p>
      <p className="text-2xl font-bold">
        {value !== undefined ? value.toFixed(3) : '—'}
      </p>
    </div>
  )
}

function Collapsible({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-6 py-4 text-left focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none rounded-xl"
      >
        <h2 className="font-semibold">{title}</h2>
        <span className="text-gray-400">{open ? '▲' : '▼'}</span>
      </button>
      {open && <div className="px-6 pb-6">{children}</div>}
    </div>
  )
}

function CostBadge({ costUsd, durationSeconds }: { costUsd?: number; durationSeconds?: number }) {
  if (costUsd === undefined && durationSeconds === undefined) return null
  return (
    <div className="flex items-center gap-3 mt-4 pt-4 border-t border-gray-100 dark:border-gray-700">
      {costUsd !== undefined && (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200">
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          ${costUsd.toFixed(4)}
        </span>
      )}
      {durationSeconds !== undefined && (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300">
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          {Math.round(durationSeconds)}s
        </span>
      )}
    </div>
  )
}

type RunFull = Run & { findings: Finding[]; tool_calls: ToolCall[]; messages: Message[]; prompt_snapshot?: PromptSnapshot }

export default function RunDetail() {
  const { experimentId, runId } = useParams<{ experimentId: string; runId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()
  const [run, setRun] = useState<RunFull | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedFinding, setExpandedFinding] = useState<string | null>(null)
  const [sourceContent] = useState<Record<string, string>>({})
  const [expandedTool, setExpandedTool] = useState<number | null>(null)
  const [findingsPage, setFindingsPage] = useState(0)
  const hashScrolledRef = useRef(false)

  // URL-state filters (item 3)
  const severityFilter = searchParams.get('severity') ?? 'all'
  const matchFilter = searchParams.get('match') ?? 'all'
  const searchQuery = searchParams.get('q') ?? ''

  const setFilter = (key: string, value: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value === 'all' || value === '') {
        next.delete(key)
      } else {
        next.set(key, value)
      }
      return next
    }, { replace: true })
    setFindingsPage(0)
  }

  useEffect(() => {
    if (!experimentId || !runId) return
    getRun(experimentId, runId)
      .then(setRun)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [experimentId, runId])

  // Scroll to and expand a specific finding when navigated via hash (#finding-<id>)
  useEffect(() => {
    if (!run || hashScrolledRef.current) return
    const hash = location.hash
    if (!hash.startsWith('#finding-')) return
    const findingId = hash.slice('#finding-'.length)
    setExpandedFinding(findingId)
    hashScrolledRef.current = true
    // Give the DOM time to render the expanded row before scrolling
    setTimeout(() => {
      const el = document.getElementById(`finding-${findingId}`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }, 150)
  }, [run, location.hash])

  if (loading) return <PageLoadingSpinner />

  if (error || !run) {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
        {error ?? 'Run not found'}
      </div>
    )
  }

  const filteredFindings = run.findings.filter((f) => {
    if (severityFilter !== 'all' && f.severity !== severityFilter) return false
    if (matchFilter !== 'all' && f.match_status !== matchFilter) return false
    if (searchQuery && !f.title.toLowerCase().includes(searchQuery.toLowerCase()) &&
        !(f.file_path ?? '').toLowerCase().includes(searchQuery.toLowerCase())) return false
    return true
  })

  const totalFindingPages = Math.ceil(filteredFindings.length / PAGE_SIZE)
  const pagedFindings = filteredFindings.slice(findingsPage * PAGE_SIZE, (findingsPage + 1) * PAGE_SIZE)

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: 'Dashboard', to: '/' },
          { label: experimentId ?? '', to: `/experiments/${experimentId}` },
          { label: run.run_id },
        ]}
      />

      <PageDescription>
        Everything a single experiment produced: findings with match status (TP/FP/FN), the tool-call audit, the assistant transcript, and the exact prompt that was sent to the model.
        Use this when you need to understand <em>why</em> a run got the precision and recall it did, and to reclassify individual findings.
      </PageDescription>

      {/* Header */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h1 className="text-xl font-bold font-mono">{run.run_id}</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">Experiment ID: {run.experiment_id}</p>
          </div>
          <div className="flex items-center gap-3">
            <span className={`px-3 py-1 rounded-full text-sm font-medium ${MATCH_STATUS_COLORS[run.status] ?? 'bg-gray-100 text-gray-600'}`}>
              {run.status}
            </span>
            {experimentId && <DownloadButton experimentId={experimentId} label="Download Run" />}
          </div>
        </div>

        <dl className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          {[
            ['Model', run.model],
            ['Strategy', run.strategy],
            ['Tools', run.tool_variant],
            ['Extensions', (run.tool_extensions ?? []).join(', ') || '—'],
            ['Profile', run.profile],
            ['Verification', run.verification],
            ['Duration', run.duration_seconds ? `${Math.round(run.duration_seconds)}s` : '—'],
            ['Cost', run.cost_usd !== undefined ? `$${run.cost_usd.toFixed(4)}` : '—'],
          ].map(([k, v]) => (
            <div key={k}>
              <dt className="text-gray-500 dark:text-gray-400">{k}</dt>
              <dd className="font-mono font-medium">{v}</dd>
            </div>
          ))}
        </dl>

        <CostBadge costUsd={run.cost_usd} durationSeconds={run.duration_seconds} />
      </div>

      {/* Metrics — F1 hero, others secondary (item 4) */}
      <div className="space-y-3">
        <HeroMetricCard label="F1" value={run.f1} />
        <div className="grid grid-cols-3 gap-3">
          <SecondaryMetricCard label="Precision" value={run.precision} />
          <SecondaryMetricCard label="Recall" value={run.recall} />
          <SecondaryMetricCard label="FPR" value={run.fpr} kind="lower-is-better" />
        </div>
      </div>

      {/* Prompt Snapshot */}
      <Collapsible title="Prompt Snapshot">
        {run.prompt_snapshot ? (
          <PromptInjectionViewer promptSnapshot={run.prompt_snapshot} />
        ) : (
          <p className="text-sm text-gray-400 dark:text-gray-500">
            Prompt snapshot not available in this run object. Check experiment configuration.
          </p>
        )}
      </Collapsible>

      {/* Findings */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="font-semibold mb-4">Findings ({run.findings.length})</h2>

        {/* Inline filters with URL-state (item 3) */}
        <div className="flex flex-wrap gap-3 mb-4">
          <input
            type="search"
            placeholder="Search title / file…"
            value={searchQuery}
            onChange={(e) => setFilter('q', e.target.value)}
            className="text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none"
          />
          <select
            value={severityFilter}
            onChange={(e) => setFilter('severity', e.target.value)}
            className="text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none"
          >
            <option value="all">All severities</option>
            {['critical', 'high', 'medium', 'low', 'info'].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <select
            value={matchFilter}
            onChange={(e) => setFilter('match', e.target.value)}
            className="text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none"
          >
            <option value="all">All statuses</option>
            <option value="tp">True Positive</option>
            <option value="fp">False Positive</option>
            <option value="fn">False Negative</option>
            <option value="unlabeled_real">Unlabeled Real</option>
          </select>
          <span className="text-sm text-gray-500 dark:text-gray-400 self-center">
            {filteredFindings.length} of {run.findings.length}
          </span>
        </div>

        {run.findings.length === 0 ? (
          <EmptyState
            icon={
              <svg className="w-12 h-12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            }
            title="No findings"
            subtitle="This run produced zero findings."
          />
        ) : (
          <>
            <div className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
                  <tr>
                    <th className="px-3 py-2 text-left">Status</th>
                    <th className="px-3 py-2 text-left">Title</th>
                    <th className="px-3 py-2 text-left">Severity</th>
                    <th className="px-3 py-2 text-left">Class</th>
                    <th className="px-3 py-2 text-left">File</th>
                    <th className="px-3 py-2 text-left">Line</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {pagedFindings.map((f) => (
                    <>
                      <tr
                        key={f.finding_id}
                        id={`finding-${f.finding_id}`}
                        onClick={() => {
                          setExpandedFinding((id) => id === f.finding_id ? null : f.finding_id)
                        }}
                        className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
                      >
                        <td className="px-3 py-2">
                          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${MATCH_STATUS_COLORS[f.match_status] ?? ''}`}>
                            {f.match_status}
                          </span>
                        </td>
                        <td className="px-3 py-2 font-medium text-gray-900 dark:text-gray-100" title={f.title}>{f.title}</td>
                        <td className="px-3 py-2">
                          <span className={`px-2 py-0.5 rounded text-xs font-medium ${SEVERITY_COLORS[f.severity] ?? ''}`}>
                            {f.severity}
                          </span>
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-gray-500" title={f.vuln_class}>{f.vuln_class}</td>
                        <td className="px-3 py-2 font-mono text-xs text-gray-500 max-w-xs truncate" title={f.file_path ?? ''}>
                          {f.file_path ?? '—'}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-gray-500">{f.line_start ?? '—'}</td>
                      </tr>
                      {expandedFinding === f.finding_id && (
                        <tr key={`${f.finding_id}-expanded`}>
                          <td colSpan={6} className="px-4 py-4 bg-gray-50 dark:bg-gray-900">
                            <div className="space-y-4">
                              <div>
                                <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Description</p>
                                <CodeViewer content={f.description} language="markdown" maxHeight="200px" />
                              </div>
                              {f.recommendation && (
                                <div>
                                  <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Recommendation</p>
                                  <CodeViewer content={f.recommendation} language="markdown" maxHeight="150px" />
                                </div>
                              )}
                              {sourceContent[f.finding_id] && (
                                <div>
                                  <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Source Context</p>
                                  <CodeViewer content={sourceContent[f.finding_id]} maxHeight="200px" />
                                </div>
                              )}
                              {f.match_status === 'fp' && experimentId && (
                                <button
                                  onClick={async (e) => {
                                    e.stopPropagation()
                                    await reclassifyFinding(experimentId, run.run_id, f.finding_id, 'unlabeled_real', '')
                                  }}
                                  className="text-xs px-3 py-1 rounded bg-orange-100 dark:bg-orange-900 text-orange-800 dark:text-orange-200 hover:bg-orange-200 transition-colors focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none"
                                >
                                  Reclassify as Unlabeled Real
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  ))}
                </tbody>
              </table>
            </div>

            {totalFindingPages > 1 && (
              <div className="flex items-center justify-between mt-4">
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Page {findingsPage + 1} of {totalFindingPages} ({filteredFindings.length} findings)
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setFindingsPage((p) => Math.max(0, p - 1))}
                    disabled={findingsPage === 0}
                    className="px-3 py-1 text-xs rounded border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-40 transition-colors focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => setFindingsPage((p) => Math.min(totalFindingPages - 1, p + 1))}
                    disabled={findingsPage >= totalFindingPages - 1}
                    className="px-3 py-1 text-xs rounded border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-40 transition-colors focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none"
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Tool Call Audit */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="font-semibold mb-4">Tool Call Audit ({run.tool_calls.length})</h2>
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
              <tr>
                <th className="px-3 py-2 text-left">Tool</th>
                <th className="px-3 py-2 text-left">Input</th>
                <th className="px-3 py-2 text-left">Timestamp</th>
                <th className="px-3 py-2 text-left">Flagged</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {run.tool_calls.map((tc, i) => {
                const inputStr = JSON.stringify(tc.input)
                const flagged = tc.flagged || /https?:\/\//.test(inputStr)
                return (
                  <>
                    <tr
                      key={i}
                      onClick={() => setExpandedTool((t) => t === i ? null : i)}
                      className={`cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors ${flagged ? 'bg-red-50 dark:bg-red-950/30' : ''}`}
                    >
                      <td className="px-3 py-2 font-mono text-xs font-medium">{tc.tool_name}</td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-500 max-w-sm truncate" title={inputStr}>
                        {inputStr.slice(0, 100)}{inputStr.length > 100 ? '…' : ''}
                      </td>
                      <td className="px-3 py-2 text-xs text-gray-500">
                        {new Date(tc.timestamp).toLocaleTimeString()}
                      </td>
                      <td className="px-3 py-2">
                        {flagged && <span className="text-xs text-red-600 dark:text-red-400 font-medium">⚠ URL</span>}
                      </td>
                    </tr>
                    {expandedTool === i && (
                      <tr key={`tool-${i}-expanded`}>
                        <td colSpan={4} className="px-4 py-3 bg-gray-50 dark:bg-gray-900">
                          <CodeViewer content={inputStr} language="json" maxHeight="200px" />
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Conversation Transcript */}
      <Collapsible title={`Conversation Transcript (${run.messages.length} messages)`}>
        <ConversationViewer messages={run.messages} />
      </Collapsible>
    </div>
  )
}
