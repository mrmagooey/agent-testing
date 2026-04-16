import { useState } from 'react'
import type { CVECandidate } from '../api/client'

export interface CVECandidateTableProps {
  candidates: CVECandidate[]
  onImport?: (cveIds: string[]) => void
}

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-600 text-white',
  high: 'bg-orange-500 text-white',
  medium: 'bg-yellow-400 text-gray-900',
  low: 'bg-blue-400 text-white',
  info: 'bg-gray-400 text-white',
}

function scoreColor(score: number): string {
  if (score >= 0.8) return 'text-green-600 dark:text-green-400'
  if (score >= 0.6) return 'text-yellow-600 dark:text-yellow-400'
  return 'text-red-600 dark:text-red-400'
}

export default function CVECandidateTable({ candidates, onImport }: CVECandidateTableProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const allSelected = candidates.length > 0 && candidates.every((c) => selected.has(c.cve_id))

  const toggleAll = () => {
    if (allSelected) {
      setSelected(new Set())
    } else {
      setSelected(new Set(candidates.map((c) => c.cve_id)))
    }
  }

  const toggle = (cveId: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(cveId)) next.delete(cveId)
      else next.add(cveId)
      return next
    })
  }

  return (
    <div className="space-y-3">
      {selected.size > 0 && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-gray-600 dark:text-gray-400">{selected.size} selected</span>
          <button
            onClick={() => onImport?.(Array.from(selected))}
            className="text-sm px-4 py-1.5 rounded bg-indigo-600 hover:bg-indigo-700 text-white transition-colors"
          >
            Import Selected ({selected.size})
          </button>
        </div>
      )}

      <div className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
            <tr>
              <th className="px-3 py-2 w-8">
                <input type="checkbox" checked={allSelected} onChange={toggleAll} className="rounded" />
              </th>
              <th className="px-3 py-2 text-left">Score</th>
              <th className="px-3 py-2 text-left">CVE ID</th>
              <th className="px-3 py-2 text-left">Vuln Class</th>
              <th className="px-3 py-2 text-left">Severity</th>
              <th className="px-3 py-2 text-left">Language</th>
              <th className="px-3 py-2 text-left">Repo</th>
              <th className="px-3 py-2 text-left">Files</th>
              <th className="px-3 py-2 text-left">Lines</th>
              <th className="px-3 py-2 text-left">Importable</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            {candidates.map((c) => (
              <>
                <tr
                  key={c.cve_id}
                  onClick={() => setExpandedId((id) => (id === c.cve_id ? null : c.cve_id))}
                  className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
                >
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={selected.has(c.cve_id)}
                      onChange={() => {}}
                      onClick={(e) => { e.stopPropagation(); toggle(c.cve_id) }}
                      className="rounded"
                    />
                  </td>
                  <td className={`px-3 py-2 font-mono font-bold ${scoreColor(c.score)}`}>
                    {c.score.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-indigo-600 dark:text-indigo-400">
                    {c.cve_id}
                  </td>
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-400 font-mono text-xs">
                    {c.vuln_class}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${SEVERITY_BADGE[c.severity] ?? 'bg-gray-200'}`}>
                      {c.severity}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-400">{c.language}</td>
                  <td className="px-3 py-2 text-gray-500 dark:text-gray-400 font-mono text-xs max-w-xs truncate">
                    {c.repo}
                  </td>
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-400 text-center">{c.files_changed}</td>
                  <td className="px-3 py-2 text-gray-600 dark:text-gray-400 text-center">{c.lines_changed}</td>
                  <td className="px-3 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs ${c.importable ? 'bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200' : 'bg-gray-100 dark:bg-gray-800 text-gray-500'}`}>
                      {c.importable ? 'yes' : 'no'}
                    </span>
                  </td>
                </tr>
                {expandedId === c.cve_id && (
                  <tr key={`${c.cve_id}-expanded`}>
                    <td colSpan={10} className="px-4 py-4 bg-gray-50 dark:bg-gray-900 text-sm">
                      {c.description && (
                        <p className="text-gray-600 dark:text-gray-400 mb-2">{c.description}</p>
                      )}
                      <div className="flex gap-4">
                        {c.advisory_url && (
                          <a
                            href={c.advisory_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-indigo-600 dark:text-indigo-400 hover:underline text-xs"
                          >
                            Advisory ↗
                          </a>
                        )}
                        {c.fix_commit && (
                          <a
                            href={c.fix_commit}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-indigo-600 dark:text-indigo-400 hover:underline text-xs"
                          >
                            Fix commit ↗
                          </a>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
            {candidates.length === 0 && (
              <tr>
                <td colSpan={10} className="px-3 py-8 text-center text-gray-400">
                  No candidates found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
