import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { getRun, getFileContent, reclassifyFinding, type Run, type Finding, type ToolCall, type Message } from '../api/client'
import Breadcrumbs from '../components/Breadcrumbs'
import CodeViewer from '../components/CodeViewer'
import ConversationViewer from '../components/ConversationViewer'
import DownloadButton from '../components/DownloadButton'

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-600 text-white',
  high: 'bg-orange-500 text-white',
  medium: 'bg-yellow-400 text-gray-900',
  low: 'bg-blue-400 text-white',
  info: 'bg-gray-400 text-white',
}

const MATCH_BADGE: Record<string, string> = {
  tp: 'bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200',
  fp: 'bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200',
  fn: 'bg-orange-100 dark:bg-orange-900 text-orange-800 dark:text-orange-200',
  unlabeled_real: 'bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200',
}

const PAGE_SIZE = 25

function MetricCard({ label, value }: { label: string; value?: number }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 text-center">
      <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">{label}</p>
      <p className="text-2xl font-bold text-gray-900 dark:text-gray-100">
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
        className="w-full flex items-center justify-between px-6 py-4 text-left"
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

type RunFull = Run & { findings: Finding[]; tool_calls: ToolCall[]; messages: Message[] }

export default function RunDetail() {
  const { batchId, runId } = useParams<{ batchId: string; runId: string }>()
  const [run, setRun] = useState<RunFull | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedFinding, setExpandedFinding] = useState<string | null>(null)
  const [sourceContent, setSourceContent] = useState<Record<string, string>>({})
  const [expandedTool, setExpandedTool] = useState<number | null>(null)
  const [findingsPage, setFindingsPage] = useState(0)

  useEffect(() => {
    if (!batchId || !runId) return
    getRun(batchId, runId)
      .then(setRun)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [batchId, runId])

  const loadSource = async (finding: Finding) => {
    if (!finding.file_path || sourceContent[finding.finding_id]) return
    if (!run) return
    try {
      // dataset name not directly available on Run; skip source loading
    } catch {
      // ignore
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading run...</div>
  }

  if (error || !run) {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
        {error ?? 'Run not found'}
      </div>
    )
  }

  const totalFindingPages = Math.ceil(run.findings.length / PAGE_SIZE)
  const pagedFindings = run.findings.slice(findingsPage * PAGE_SIZE, (findingsPage + 1) * PAGE_SIZE)

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: 'Dashboard', to: '/' },
          { label: batchId ?? '', to: `/batches/${batchId}` },
          { label: run.experiment_id },
        ]}
      />

      {/* Header */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h1 className="text-xl font-bold font-mono">{run.experiment_id}</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">Run ID: {run.run_id}</p>
          </div>
          <div className="flex items-center gap-3">
            <span className={`px-3 py-1 rounded-full text-sm font-medium ${MATCH_BADGE[run.status] ?? 'bg-gray-100 text-gray-600'}`}>
              {run.status}
            </span>
            {batchId && <DownloadButton batchId={batchId} label="Download Run" />}
          </div>
        </div>

        <dl className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          {[
            ['Model', run.model],
            ['Strategy', run.strategy],
            ['Tools', run.tool_variant],
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

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <MetricCard label="Precision" value={run.precision} />
        <MetricCard label="Recall" value={run.recall} />
        <MetricCard label="F1" value={run.f1} />
        <MetricCard label="FPR" value={run.fpr} />
      </div>

      {/* Prompt Snapshot */}
      <Collapsible title="Prompt Snapshot">
        <p className="text-sm text-gray-400 dark:text-gray-500">
          Prompt snapshot not available in this run object. Check batch configuration.
        </p>
      </Collapsible>

      {/* Findings */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6">
        <h2 className="font-semibold mb-4">Findings ({run.findings.length})</h2>

        {run.findings.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-gray-400 dark:text-gray-500">
            <svg className="w-12 h-12 mb-3 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <p className="text-sm font-medium">No findings</p>
            <p className="text-xs mt-1">This run produced zero findings.</p>
          </div>
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
                        onClick={() => {
                          setExpandedFinding((id) => id === f.finding_id ? null : f.finding_id)
                          loadSource(f)
                        }}
                        className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
                      >
                        <td className="px-3 py-2">
                          <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${MATCH_BADGE[f.match_status] ?? ''}`}>
                            {f.match_status}
                          </span>
                        </td>
                        <td className="px-3 py-2 font-medium text-gray-900 dark:text-gray-100">{f.title}</td>
                        <td className="px-3 py-2">
                          <span className={`px-2 py-0.5 rounded text-xs font-medium ${SEVERITY_BADGE[f.severity] ?? ''}`}>
                            {f.severity}
                          </span>
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-gray-500">{f.vuln_class}</td>
                        <td className="px-3 py-2 font-mono text-xs text-gray-500 max-w-xs truncate">
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
                              {f.match_status === 'fp' && batchId && (
                                <button
                                  onClick={async (e) => {
                                    e.stopPropagation()
                                    await reclassifyFinding(batchId, run.run_id, f.finding_id, 'unlabeled_real', '')
                                  }}
                                  className="text-xs px-3 py-1 rounded bg-orange-100 dark:bg-orange-900 text-orange-800 dark:text-orange-200 hover:bg-orange-200 transition-colors"
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
                  Page {findingsPage + 1} of {totalFindingPages} ({run.findings.length} findings)
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setFindingsPage((p) => Math.max(0, p - 1))}
                    disabled={findingsPage === 0}
                    className="px-3 py-1 text-xs rounded border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-40 transition-colors"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => setFindingsPage((p) => Math.min(totalFindingPages - 1, p + 1))}
                    disabled={findingsPage >= totalFindingPages - 1}
                    className="px-3 py-1 text-xs rounded border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-40 transition-colors"
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
                      <td className="px-3 py-2 font-mono text-xs text-gray-500 max-w-sm truncate">
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
