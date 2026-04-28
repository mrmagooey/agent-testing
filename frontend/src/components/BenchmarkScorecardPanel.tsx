import { useState } from 'react'
import type { BenchmarkScorecard, CweRow, AggregateRow } from '../api/client'

export interface BenchmarkScorecardPanelProps {
  scorecards?: BenchmarkScorecard[]
}

function fmtPct(value: number | null): string {
  if (value === null) return '—'
  return `${(value * 100).toFixed(1)}%`
}

function fmtNum(value: number | null): string {
  if (value === null) return '—'
  return value.toFixed(3)
}

function WarningIcon({ message }: { message: string }) {
  return (
    <span
      role="img"
      aria-label="warning"
      title={message}
      className="inline-flex items-center justify-center ml-1 text-amber-500 dark:text-amber-400 cursor-help"
    >
      &#9888;
    </span>
  )
}

interface CweTableRowProps {
  row: CweRow
}

function CweTableRow({ row }: CweTableRowProps) {
  return (
    <tr className="border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors">
      <td className="px-3 py-2 font-mono text-xs font-medium text-gray-800 dark:text-gray-200 whitespace-nowrap">
        {row.cwe_id}
        {row.warning && <WarningIcon message={row.warning} />}
      </td>
      <td className="px-3 py-2 font-mono text-xs text-right text-gray-700 dark:text-gray-300">{row.tp}</td>
      <td className="px-3 py-2 font-mono text-xs text-right text-gray-700 dark:text-gray-300">{row.fp}</td>
      <td className="px-3 py-2 font-mono text-xs text-right text-gray-700 dark:text-gray-300">{row.tn}</td>
      <td className="px-3 py-2 font-mono text-xs text-right text-gray-700 dark:text-gray-300">{row.fn}</td>
      <td className="px-3 py-2 font-mono text-xs text-right text-gray-700 dark:text-gray-300">{fmtNum(row.precision)}</td>
      <td className="px-3 py-2 font-mono text-xs text-right text-gray-700 dark:text-gray-300">{fmtNum(row.recall)}</td>
      <td className="px-3 py-2 font-mono text-xs text-right text-gray-700 dark:text-gray-300">{fmtNum(row.f1)}</td>
      <td className="px-3 py-2 font-mono text-xs text-right text-gray-700 dark:text-gray-300">{fmtNum(row.fp_rate)}</td>
      <td className="px-3 py-2 font-mono text-xs text-right font-semibold text-gray-900 dark:text-gray-100">{fmtPct(row.owasp_score)}</td>
    </tr>
  )
}

interface AggregateTableRowProps {
  row: AggregateRow
}

function AggregateTableRow({ row }: AggregateTableRowProps) {
  return (
    <tr className="bg-gray-50 dark:bg-gray-800/70 border-t-2 border-gray-200 dark:border-gray-600">
      <td className="px-3 py-2 font-mono text-xs font-semibold text-gray-800 dark:text-gray-200 whitespace-nowrap">
        Aggregate
        {row.warning && <WarningIcon message={row.warning} />}
      </td>
      <td className="px-3 py-2 font-mono text-xs text-right font-semibold text-gray-800 dark:text-gray-200">{row.tp}</td>
      <td className="px-3 py-2 font-mono text-xs text-right font-semibold text-gray-800 dark:text-gray-200">{row.fp}</td>
      <td className="px-3 py-2 font-mono text-xs text-right font-semibold text-gray-800 dark:text-gray-200">{row.tn}</td>
      <td className="px-3 py-2 font-mono text-xs text-right font-semibold text-gray-800 dark:text-gray-200">{row.fn}</td>
      <td className="px-3 py-2 font-mono text-xs text-right font-semibold text-gray-800 dark:text-gray-200">{fmtNum(row.precision)}</td>
      <td className="px-3 py-2 font-mono text-xs text-right font-semibold text-gray-800 dark:text-gray-200">{fmtNum(row.recall)}</td>
      <td className="px-3 py-2 font-mono text-xs text-right font-semibold text-gray-800 dark:text-gray-200">{fmtNum(row.f1)}</td>
      <td className="px-3 py-2 font-mono text-xs text-right font-semibold text-gray-800 dark:text-gray-200">{fmtNum(row.fp_rate)}</td>
      <td className="px-3 py-2 font-mono text-xs text-right font-semibold text-gray-900 dark:text-gray-100">{fmtPct(row.owasp_score)}</td>
    </tr>
  )
}

interface DatasetSectionProps {
  scorecard: BenchmarkScorecard
}

function DatasetSection({ scorecard }: DatasetSectionProps) {
  const [expanded, setExpanded] = useState(true)

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
      {/* Section header */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700/60 transition-colors text-left"
        aria-expanded={expanded}
      >
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            {scorecard.dataset_name}
          </span>
          {/* Headline OWASP score badge */}
          <span
            className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200"
            title="Aggregate OWASP Benchmark Score (TPR - FPR)"
            data-testid="owasp-headline"
          >
            OWASP {fmtPct(scorecard.aggregate.owasp_score)}
          </span>
        </div>
        <span className="text-gray-400 dark:text-gray-500 text-xs">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 dark:bg-gray-800/60 border-b border-gray-200 dark:border-gray-700">
                <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 whitespace-nowrap">CWE</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400">TP</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400">FP</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400">TN</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400">FN</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400">Precision</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400">Recall</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400">F1</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400">FP-rate</th>
                <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 dark:text-gray-400 whitespace-nowrap">OWASP Score</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
              {scorecard.per_cwe.map((row) => (
                <CweTableRow key={row.cwe_id} row={row} />
              ))}
              <AggregateTableRow row={scorecard.aggregate} />
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/**
 * Benchmark Scorecard panel — renders precision/recall/F1/FP-rate metrics
 * from OWASP Benchmark-style datasets.
 *
 * Hides entirely when no scorecards are present (non-benchmark experiments).
 * Must NOT mix these metrics with the CVE-discovery matrix: they answer
 * different questions.
 */
export default function BenchmarkScorecardPanel({ scorecards }: BenchmarkScorecardPanelProps) {
  if (!scorecards || scorecards.length === 0) {
    return null
  }

  return (
    <section
      className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6"
      data-testid="benchmark-scorecard-panel"
    >
      <h2 className="text-lg font-semibold mb-1">Benchmark Scorecard</h2>
      <p className="text-sm text-gray-500 dark:text-gray-400 mb-4">
        Precision / recall / F1 / FP-rate metrics from OWASP Benchmark-style datasets.
        These are computed against paired positive/negative ground-truth labels and are
        independent of the CVE-discovery results above.
      </p>
      <div className="space-y-4">
        {scorecards.map((sc) => (
          <DatasetSection key={sc.dataset_name} scorecard={sc} />
        ))}
      </div>
    </section>
  )
}
