import { useState } from 'react'
import type { Finding } from '../api/client'
import { reclassifyFinding } from '../api/client'
import CodeViewer from './CodeViewer'
import FindingsSearch from './FindingsSearch'

export interface FindingsExplorerProps {
  batchId: string
  findings: Finding[]
}

const MATCH_BADGE: Record<string, string> = {
  tp: 'bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200',
  fp: 'bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200',
  fn: 'bg-orange-100 dark:bg-orange-900 text-orange-800 dark:text-orange-200',
  unlabeled_real: 'bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200',
}

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-600 text-white',
  high: 'bg-orange-500 text-white',
  medium: 'bg-yellow-400 text-gray-900',
  low: 'bg-blue-400 text-white',
  info: 'bg-gray-400 text-white',
}

export default function FindingsExplorer({ batchId, findings: initialFindings }: FindingsExplorerProps) {
  const [searchResults, setSearchResults] = useState<Finding[] | null>(null)
  const [matchFilter, setMatchFilter] = useState('all')
  const [vulnFilter, setVulnFilter] = useState('all')
  const [severityFilter, setSeverityFilter] = useState('all')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [reclassifyModal, setReclassifyModal] = useState<{
    findingId: string
    runId: string
    note: string
  } | null>(null)
  const [reclassifyLoading, setReclassifyLoading] = useState(false)

  const source = searchResults ?? initialFindings

  const vulnClasses = Array.from(new Set(source.map((f) => f.vuln_class))).sort()

  const filtered = source.filter((f) => {
    if (matchFilter !== 'all' && f.match_status !== matchFilter) return false
    if (vulnFilter !== 'all' && f.vuln_class !== vulnFilter) return false
    if (severityFilter !== 'all' && f.severity !== severityFilter) return false
    return true
  })

  const handleReclassify = async () => {
    if (!reclassifyModal) return
    setReclassifyLoading(true)
    try {
      await reclassifyFinding(
        batchId,
        reclassifyModal.runId,
        reclassifyModal.findingId,
        'unlabeled_real',
        reclassifyModal.note
      )
      setReclassifyModal(null)
    } finally {
      setReclassifyLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <FindingsSearch batchId={batchId} onResults={(r) => setSearchResults(r.length > 0 ? r : null)} />

      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <select
          value={matchFilter}
          onChange={(e) => setMatchFilter(e.target.value)}
          className="text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1"
        >
          <option value="all">All statuses</option>
          <option value="tp">True Positive</option>
          <option value="fp">False Positive</option>
          <option value="fn">False Negative</option>
          <option value="unlabeled_real">Unlabeled Real</option>
        </select>
        <select
          value={vulnFilter}
          onChange={(e) => setVulnFilter(e.target.value)}
          className="text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1"
        >
          <option value="all">All vuln classes</option>
          {vulnClasses.map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
        <select
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          className="text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1"
        >
          <option value="all">All severities</option>
          {['critical', 'high', 'medium', 'low', 'info'].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <span className="text-sm text-gray-500 dark:text-gray-400 self-center">
          {filtered.length} findings
        </span>
      </div>

      {/* Table */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
            <tr>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-left">Title</th>
              <th className="px-3 py-2 text-left">Severity</th>
              <th className="px-3 py-2 text-left">Vuln Class</th>
              <th className="px-3 py-2 text-left">File</th>
              <th className="px-3 py-2 text-left">Line</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            {filtered.map((finding) => (
              <>
                <tr
                  key={finding.finding_id}
                  onClick={() =>
                    setExpandedId((id) => (id === finding.finding_id ? null : finding.finding_id))
                  }
                  className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
                >
                  <td className="px-3 py-2">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${MATCH_BADGE[finding.match_status] ?? ''}`}>
                      {finding.match_status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-gray-900 dark:text-gray-100 font-medium">
                    {finding.title}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${SEVERITY_BADGE[finding.severity] ?? ''}`}>
                      {finding.severity}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-400 font-mono text-xs">
                    {finding.vuln_class}
                  </td>
                  <td className="px-3 py-2 text-gray-500 dark:text-gray-400 font-mono text-xs truncate max-w-xs">
                    {finding.file_path ?? '—'}
                  </td>
                  <td className="px-3 py-2 text-gray-500 dark:text-gray-400 font-mono text-xs">
                    {finding.line_start ?? '—'}
                  </td>
                </tr>
                {expandedId === finding.finding_id && (
                  <tr key={`${finding.finding_id}-expanded`}>
                    <td colSpan={6} className="px-4 py-4 bg-gray-50 dark:bg-gray-900">
                      <div className="space-y-3">
                        <CodeViewer
                          content={finding.description}
                          language="markdown"
                          maxHeight="200px"
                        />
                        {finding.matched_label_id && (
                          <p className="text-xs text-gray-500 dark:text-gray-400">
                            Matched label: <code className="font-mono">{finding.matched_label_id}</code>
                          </p>
                        )}
                        {finding.match_status === 'fp' && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              setReclassifyModal({
                                findingId: finding.finding_id,
                                runId: finding.run_id,
                                note: '',
                              })
                            }}
                            className="text-xs px-3 py-1 rounded bg-orange-100 dark:bg-orange-900 text-orange-800 dark:text-orange-200 hover:bg-orange-200 dark:hover:bg-orange-800 transition-colors"
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
            {filtered.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-gray-400">
                  No findings match the current filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Reclassify Modal */}
      {reclassifyModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-gray-800 rounded-xl p-6 w-full max-w-md shadow-xl">
            <h3 className="font-semibold mb-3">Reclassify Finding</h3>
            <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
              Reclassify this FP as <strong>unlabeled_real</strong>. Add a note explaining why.
            </p>
            <textarea
              value={reclassifyModal.note}
              onChange={(e) =>
                setReclassifyModal((m) => m ? { ...m, note: e.target.value } : null)
              }
              placeholder="Reason for reclassification..."
              rows={3}
              className="w-full text-sm rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 mb-4"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setReclassifyModal(null)}
                className="px-4 py-2 text-sm rounded border border-gray-200 dark:border-gray-700"
              >
                Cancel
              </button>
              <button
                onClick={handleReclassify}
                disabled={reclassifyLoading}
                className="px-4 py-2 text-sm rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {reclassifyLoading ? 'Saving...' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
