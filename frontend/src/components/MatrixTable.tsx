import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Run } from '../api/client'

export interface MatrixTableProps {
  runs: Run[]
  onSelect?: (runIds: string[]) => void
  selectedIds?: string[]
}

type SortKey = keyof Run
type SortDir = 'asc' | 'desc'

function metricCell(value: number | undefined) {
  if (value === undefined || value === null) return { text: '—', label: '', cls: '' }
  const text = value.toFixed(3)
  if (value >= 0.8) return { text, label: 'PASS', cls: 'bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200' }
  if (value >= 0.6) return { text, label: 'WARN', cls: 'bg-yellow-100 dark:bg-yellow-900 text-yellow-800 dark:text-yellow-200' }
  return { text, label: 'FAIL', cls: 'bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200' }
}

const COLUMNS: { key: keyof Run; label: string; heatmap?: boolean }[] = [
  { key: 'model', label: 'Model' },
  { key: 'strategy', label: 'Strategy' },
  { key: 'tool_variant', label: 'Tools' },
  { key: 'profile', label: 'Profile' },
  { key: 'verification', label: 'Verif.' },
  { key: 'precision', label: 'Prec', heatmap: true },
  { key: 'recall', label: 'Recall', heatmap: true },
  { key: 'f1', label: 'F1', heatmap: true },
  { key: 'fpr', label: 'FPR' },
  { key: 'tp_count', label: 'TP' },
  { key: 'fp_count', label: 'FP' },
  { key: 'fn_count', label: 'FN' },
  { key: 'cost_usd', label: 'Cost' },
  { key: 'duration_seconds', label: 'Duration' },
]

export default function MatrixTable({ runs, onSelect, selectedIds = [] }: MatrixTableProps) {
  const navigate = useNavigate()
  const [sortKey, setSortKey] = useState<SortKey>('f1')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [localSelected, setLocalSelected] = useState<string[]>(selectedIds)

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const sorted = [...runs].sort((a, b) => {
    const av = a[sortKey]
    const bv = b[sortKey]
    if (av === undefined || av === null) return 1
    if (bv === undefined || bv === null) return -1
    const cmp = av < bv ? -1 : av > bv ? 1 : 0
    return sortDir === 'asc' ? cmp : -cmp
  })

  const toggleSelect = (runId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setLocalSelected((prev) => {
      let next: string[]
      if (prev.includes(runId)) {
        next = prev.filter((id) => id !== runId)
      } else if (prev.length < 2) {
        next = [...prev, runId]
      } else {
        next = [prev[1], runId]
      }
      onSelect?.(next)
      return next
    })
  }

  return (
    <div>
      {localSelected.length > 0 && (
        <div className="mb-2 text-sm text-indigo-600 dark:text-indigo-400">
          {localSelected.length} run(s) selected
        </div>
      )}
      <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
            <tr>
              <th className="px-3 py-2 text-left w-8">
                <span className="sr-only">Select</span>
              </th>
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  className="px-3 py-2 text-left cursor-pointer hover:text-gray-900 dark:hover:text-gray-100 select-none whitespace-nowrap"
                  onClick={() => handleSort(col.key)}
                >
                  {col.label}
                  {sortKey === col.key && (
                    <span className="ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
            {sorted.map((run) => (
              <tr
                key={run.run_id}
                onClick={() => navigate(`/batches/${run.batch_id}/runs/${run.run_id}`)}
                className={`cursor-pointer transition-colors hover:bg-gray-50 dark:hover:bg-gray-800/50 ${
                  localSelected.includes(run.run_id) ? 'bg-indigo-50 dark:bg-indigo-950' : ''
                }`}
              >
                <td className="px-3 py-2">
                  <input
                    type="checkbox"
                    checked={localSelected.includes(run.run_id)}
                    onChange={() => {}}
                    onClick={(e) => toggleSelect(run.run_id, e)}
                    className="rounded"
                  />
                </td>
                {COLUMNS.map((col) => {
                  const raw = run[col.key]
                  if (col.heatmap) {
                    const { text, label, cls } = metricCell(raw as number | undefined)
                    return (
                      <td key={col.key} className={`px-3 py-2 font-mono text-xs rounded ${cls}`}>
                        {text !== '—' ? (
                          <span className="flex items-center gap-1.5">
                            <span>{text}</span>
                            <span className="text-[10px] font-semibold opacity-70 tracking-wide">{label}</span>
                          </span>
                        ) : (
                          text
                        )}
                      </td>
                    )
                  }
                  let display: string
                  if (raw === undefined || raw === null) display = '—'
                  else if (col.key === 'cost_usd') display = `$${(raw as number).toFixed(3)}`
                  else if (col.key === 'duration_seconds') display = `${Math.round(raw as number)}s`
                  else display = String(raw)
                  return (
                    <td key={col.key} className="px-3 py-2 text-gray-700 dark:text-gray-300 font-mono text-xs whitespace-nowrap">
                      {display}
                    </td>
                  )
                })}
              </tr>
            ))}
            {runs.length === 0 && (
              <tr>
                <td colSpan={COLUMNS.length + 1} className="px-3 py-8 text-center text-gray-400">
                  No runs yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
        Virtualization skipped — typical batch sizes (5 models × 4 strategies × 2 variants = 40 rows) do not require it.
      </p>
    </div>
  )
}
