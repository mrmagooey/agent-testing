import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { getRun, getFileContent, reclassifyFinding, type Run, type Finding, type ToolCall, type Message } from '../api/client'
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

type RunFull = Run & { findings: Finding[]; tool_calls: ToolCall[]; messages: Message[] }

export default function RunDetail() {
  const { batchId, runId } = useParams<{ batchId: string; runId: string }>()
  const [run, setRun] = useState<RunFull | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedFinding, setExpandedFinding] = useState<string | null>(null)
  const [sourceContent, setSourceContent] = useState<Record<string, string>>({})
  const [expandedTool, setExpandedTool] = useState<number | null>(null)

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
    // dataset name needs to come from batch — use a best-effort approach
    try {
      // We don't have dataset name directly from Run; the batch detail would have it.
      // For now, skip source loading unless we can infer it.
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

  return (
    <div className="space-y-6">
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
              {run.findings.map((f) => (
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
