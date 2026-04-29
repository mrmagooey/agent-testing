import React, { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Run } from '../api/client'
import { metricTone } from '../constants/colors'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'

export interface MatrixTableProps {
  runs: Run[]
  onSelect?: (runIds: string[]) => void
  selectedIds?: string[]
}

type SortKey = keyof Run
type SortDir = 'asc' | 'desc'

const STICKY_COLS: { key: keyof Run; label: string }[] = [
  { key: 'model', label: 'Model' },
  { key: 'strategy', label: 'Strategy' },
  { key: 'tool_variant', label: 'Tools' },
]

const STICKY_COLS_WITH_EXT: { key: string; label: string }[] = [
  ...STICKY_COLS,
  { key: 'tool_extensions', label: 'Ext' },
]

const METRIC_COLS: { key: keyof Run; label: string; kind?: 'lower-is-better' }[] = [
  { key: 'precision', label: 'Prec' },
  { key: 'recall', label: 'Recall' },
  { key: 'f1', label: 'F1' },
  { key: 'fpr', label: 'FPR', kind: 'lower-is-better' },
]

const DETAIL_COLS: { key: keyof Run; label: string }[] = [
  { key: 'status', label: 'Status' },
  { key: 'profile', label: 'Profile' },
  { key: 'verification', label: 'Verif.' },
  { key: 'tp_count', label: 'TP' },
  { key: 'fp_count', label: 'FP' },
  { key: 'fn_count', label: 'FN' },
]

const STATUS_PILL_CLASSES: Record<string, string> = {
  failed: 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300',
  cancelled: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
  running: 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300',
}

const AUX_COLS: { key: keyof Run; label: string }[] = [
  { key: 'cost_usd', label: 'Cost' },
  { key: 'duration_seconds', label: 'Duration' },
]

export default function MatrixTable({ runs, onSelect, selectedIds = [] }: MatrixTableProps) {
  const navigate = useNavigate()
  const [sortKey, setSortKey] = useState<SortKey>('f1')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [localSelected, setLocalSelected] = useState<string[]>(selectedIds)
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set())

  // Check if any run has tool_extensions to decide whether to show the column
  const hasToolExtensions = useMemo(
    () => runs.some((r) => (r.tool_extensions?.length ?? 0) > 0),
    [runs]
  )

  // Use conditional sticky cols based on whether we have extensions
  const activeStickyColsArray = hasToolExtensions ? STICKY_COLS_WITH_EXT : STICKY_COLS

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

  const toggleExpand = (runId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setExpandedRows((prev) => {
      const next = new Set(prev)
      if (next.has(runId)) {
        next.delete(runId)
      } else {
        next.add(runId)
      }
      return next
    })
  }

  // Sticky background must be explicit for sticky cells
  const stickyBase = 'sticky bg-white dark:bg-gray-900'
  const totalCols = 1 + 1 + activeStickyColsArray.length + METRIC_COLS.length + AUX_COLS.length

  return (
    <div>
      {localSelected.length > 0 && (
        <div className="mb-2 text-sm text-amber-600 dark:text-amber-400">
          {localSelected.length} run(s) selected
        </div>
      )}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
        {/* Use shadcn Table — but override its wrapper div to allow overflow-x-auto with sticky cols */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm caption-bottom">
            <TableHeader className="bg-gray-50 dark:bg-gray-800">
              <TableRow className="border-b-0">
                {/* checkbox col */}
                <TableHead
                  className={cn(stickyBase, 'left-0 px-3 py-2 w-8 z-20 text-gray-600 dark:text-gray-400')}
                >
                  <span className="sr-only">Select</span>
                </TableHead>
                {/* expand toggle col */}
                <TableHead
                  className={cn(stickyBase, 'left-8 px-2 py-2 w-6 z-20 text-gray-600 dark:text-gray-400')}
                >
                  <span className="sr-only">Expand</span>
                </TableHead>
                {/* sticky identity cols */}
                {activeStickyColsArray.map((col, i) => {
                  const leftPx = 8 + 24 + i * 96
                  const isSortableKey = col.key !== 'tool_extensions'
                  return (
                    <TableHead
                      key={col.key}
                      style={{ left: leftPx }}
                      className={cn(
                        stickyBase,
                        isSortableKey ? 'cursor-pointer hover:text-gray-900 dark:hover:text-gray-100 select-none' : '',
                        'px-3 py-2 whitespace-nowrap z-20 text-gray-600 dark:text-gray-400'
                      )}
                      onClick={() => {
                        if (isSortableKey && col.key in (STICKY_COLS[0] as any)) {
                          handleSort(col.key as SortKey)
                        }
                      }}
                    >
                      {col.label}
                      {sortKey === col.key && isSortableKey && (
                        <span className="ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>
                      )}
                    </TableHead>
                  )
                })}
                {/* metric cols */}
                {METRIC_COLS.map((col) => (
                  <TableHead
                    key={col.key}
                    className="px-3 py-2 cursor-pointer hover:text-gray-900 dark:hover:text-gray-100 select-none whitespace-nowrap text-gray-600 dark:text-gray-400"
                    onClick={() => handleSort(col.key)}
                  >
                    {col.label}
                    {sortKey === col.key && (
                      <span className="ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>
                    )}
                  </TableHead>
                ))}
                {/* aux cols */}
                {AUX_COLS.map((col) => (
                  <TableHead
                    key={col.key}
                    className="px-3 py-2 cursor-pointer hover:text-gray-900 dark:hover:text-gray-100 select-none whitespace-nowrap text-gray-600 dark:text-gray-400"
                    onClick={() => handleSort(col.key)}
                  >
                    {col.label}
                    {sortKey === col.key && (
                      <span className="ml-1">{sortDir === 'asc' ? '↑' : '↓'}</span>
                    )}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody className="divide-y divide-gray-100 dark:divide-gray-800">
              {sorted.map((run) => {
                const isExpanded = expandedRows.has(run.run_id)
                const isSelected = localSelected.includes(run.run_id)
                const rowBase = isSelected
                  ? 'bg-amber-50 dark:bg-amber-950'
                  : 'bg-white dark:bg-gray-900'

                return (
                  <React.Fragment key={run.run_id}>
                    <TableRow
                      onClick={() => navigate(`/experiments/${run.experiment_id}/runs/${run.run_id}`)}
                      className={cn(
                        'cursor-pointer transition-colors hover:bg-gray-50 dark:hover:bg-gray-800/50 border-b-0',
                        rowBase
                      )}
                    >
                      {/* checkbox */}
                      <TableCell className={cn(stickyBase, 'left-0 px-3 py-2 z-10', rowBase)}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => {}}
                          onClick={(e) => toggleSelect(run.run_id, e)}
                          className="rounded focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none"
                        />
                      </TableCell>
                      {/* expand toggle */}
                      <TableCell className={cn(stickyBase, 'left-8 px-2 py-2 z-10', rowBase)}>
                        <div className="flex items-center gap-1.5">
                          <button
                            onClick={(e) => toggleExpand(run.run_id, e)}
                            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 text-xs leading-none focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none rounded"
                            title={isExpanded ? 'Collapse details' : 'Expand details'}
                          >
                            {isExpanded ? '▲' : '▼'}
                          </button>
                          {run.status !== 'completed' && (
                            <span
                              data-testid="matrix-row-status-pill"
                              className={cn(
                                'text-[10px] font-mono uppercase px-1.5 py-0.5 rounded whitespace-nowrap',
                                STATUS_PILL_CLASSES[run.status] ?? 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'
                              )}
                            >
                              {run.status}
                            </span>
                          )}
                        </div>
                      </TableCell>
                      {/* sticky identity cols */}
                      {activeStickyColsArray.map((col, i) => {
                        const leftPx = 8 + 24 + i * 96
                        return (
                          <TableCell
                            key={col.key}
                            style={{ left: leftPx }}
                            className={cn(
                              stickyBase,
                              'px-3 py-2 font-mono text-xs text-gray-700 dark:text-gray-300 whitespace-nowrap z-10',
                              rowBase
                            )}
                          >
                            {col.key === 'tool_extensions' ? (
                              <div className="flex gap-1 flex-wrap">
                                {(run.tool_extensions ?? []).map((ext) => (
                                  <Badge key={ext} variant="outline" className="text-[10px] px-1.5 py-0.5">
                                    {ext}
                                  </Badge>
                                ))}
                              </div>
                            ) : (
                              String(run[col.key as keyof Run] ?? '—')
                            )}
                          </TableCell>
                        )
                      })}
                      {/* metric cols with heatmap cell background */}
                      {METRIC_COLS.map((col) => {
                        const raw = run[col.key] as number | undefined
                        const { cls, label } = metricTone(raw, col.kind ?? 'higher-is-better')
                        return (
                          <TableCell key={col.key} className={cn('px-3 py-2 font-mono text-xs', cls)}>
                            {raw !== undefined && raw !== null ? (
                              <span className="flex items-center gap-1.5">
                                <span>{raw.toFixed(3)}</span>
                                <span className="text-[10px] font-semibold opacity-70 tracking-wide">{label}</span>
                              </span>
                            ) : '—'}
                          </TableCell>
                        )
                      })}
                      {/* aux cols */}
                      {AUX_COLS.map((col) => {
                        const raw = run[col.key]
                        let display: string
                        if (raw === undefined || raw === null) display = '—'
                        else if (col.key === 'cost_usd') display = `$${(raw as number).toFixed(3)}`
                        else if (col.key === 'duration_seconds') display = `${Math.round(raw as number)}s`
                        else display = String(raw)
                        return (
                          <TableCell key={col.key} className="px-3 py-2 text-gray-700 dark:text-gray-300 font-mono text-xs whitespace-nowrap">
                            {display}
                          </TableCell>
                        )
                      })}
                    </TableRow>
                    {isExpanded && (
                      <TableRow className="border-b-0">
                        <TableCell colSpan={totalCols} className="px-6 py-3 bg-gray-50 dark:bg-gray-800/60 border-b border-gray-100 dark:border-gray-800">
                          <dl className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
                            {DETAIL_COLS.map(({ key, label }) => (
                              <div key={key} className="flex gap-1.5">
                                <dt className="text-gray-500 dark:text-gray-400">{label}:</dt>
                                <dd className="font-mono font-medium text-gray-800 dark:text-gray-200">
                                  {String(run[key] ?? '—')}
                                </dd>
                              </div>
                            ))}
                          </dl>
                          {run.error && (
                            <div
                              data-testid="matrix-row-error"
                              className="mt-2 rounded border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 p-2 text-xs text-red-700 dark:text-red-300"
                            >
                              <span className="font-mono uppercase tracking-wider mr-2">Error:</span>
                              <span className="font-mono whitespace-pre-wrap break-words">{run.error}</span>
                            </div>
                          )}
                        </TableCell>
                      </TableRow>
                    )}
                  </React.Fragment>
                )
              })}
              {runs.length === 0 && (
                <TableRow className="border-b-0">
                  <TableCell colSpan={totalCols} className="px-3 py-8 text-center text-gray-400">
                    No runs yet
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </table>
        </div>
      </div>
      <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
        Virtualization skipped — typical experiment sizes (5 models × 4 strategies × 2 variants = 40 rows) do not require it.
      </p>
    </div>
  )
}
