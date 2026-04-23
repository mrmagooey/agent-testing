import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { Finding } from '../api/client'
import type { GlobalFinding } from '../api/client'
import CodeViewer from './CodeViewer'
import { SEVERITY_COLORS, MATCH_STATUS_COLORS } from '../constants/colors'

export type FindingScope = 'experiment' | 'global'

interface BaseProps {
  scope: FindingScope
  expanded: boolean
  onToggle: () => void
}

interface ExperimentFindingProps extends BaseProps {
  scope: 'experiment'
  finding: Finding & { finding_id?: string }
  experimentId: string
}

interface GlobalFindingProps extends BaseProps {
  scope: 'global'
  finding: GlobalFinding & { finding_id?: string }
}

type FindingRowProps = ExperimentFindingProps | GlobalFindingProps

// Derive a stable ID from a finding object
function findingId(f: Finding & { finding_id?: string }): string {
  return f.finding_id ?? (f as Finding & { id?: string }).id ?? ''
}

export default function FindingRow(props: FindingRowProps) {
  const { scope, expanded, onToggle } = props
  const finding = props.finding
  const fid = findingId(finding)

  const colSpan = scope === 'global' ? 9 : 6

  return (
    <>
      <tr
        id={`finding-${fid}`}
        onClick={onToggle}
        className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors"
      >
        <td className="px-3 py-2">
          <span
            className={`px-2 py-0.5 rounded-full text-xs font-medium ${
              MATCH_STATUS_COLORS[finding.match_status] ?? 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400'
            }`}
          >
            {finding.match_status ?? '—'}
          </span>
        </td>
        <td className="px-3 py-2 text-gray-900 dark:text-gray-100 font-medium max-w-xs truncate" title={finding.title}>
          {finding.title}
        </td>
        <td className="px-3 py-2">
          <span
            className={`px-2 py-0.5 rounded text-xs font-medium ${
              SEVERITY_COLORS[finding.severity] ?? ''
            }`}
          >
            {finding.severity}
          </span>
        </td>
        <td className="px-3 py-2 text-gray-600 dark:text-gray-400 font-mono text-xs">
          {finding.vuln_class}
        </td>
        <td
          className="px-3 py-2 text-gray-500 dark:text-gray-400 font-mono text-xs truncate max-w-xs"
          title={finding.file_path ?? ''}
        >
          {finding.file_path ?? '—'}
        </td>
        <td className="px-3 py-2 text-gray-500 dark:text-gray-400 font-mono text-xs">
          {finding.line_start ?? '—'}
        </td>

        {scope === 'global' && (
          <>
            <td className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
              {(props as GlobalFindingProps).finding.experiment_id ? (
                <Link
                  to={`/experiments/${(props as GlobalFindingProps).finding.experiment_id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="text-amber-600 dark:text-amber-400 hover:underline"
                >
                  {(props as GlobalFindingProps).finding.experiment_name ||
                    (props as GlobalFindingProps).finding.experiment_id}
                </Link>
              ) : (
                '—'
              )}
            </td>
            <td className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400 font-mono">
              {(props as GlobalFindingProps).finding.model_id ?? '—'}
            </td>
            <td className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
              {(props as GlobalFindingProps).finding.strategy ?? '—'}
            </td>
          </>
        )}
      </tr>

      {expanded && (
        <tr key={`${fid}-expanded`}>
          <td colSpan={colSpan} className="px-4 py-4 bg-gray-50 dark:bg-gray-900">
            <div className="space-y-3">
              <CodeViewer
                content={finding.description}
                language="markdown"
                maxHeight="200px"
              />
              {(finding as GlobalFinding).cwe_ids && (finding as GlobalFinding).cwe_ids!.length > 0 && (
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  CWEs:{' '}
                  {(finding as GlobalFinding).cwe_ids!.map((cwe) => (
                    <code key={cwe} className="font-mono mr-1">
                      {cwe}
                    </code>
                  ))}
                </p>
              )}
              {(finding as Finding & { matched_label_id?: string }).matched_label_id && (
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  Matched label:{' '}
                  <code className="font-mono">
                    {(finding as Finding & { matched_label_id?: string }).matched_label_id}
                  </code>
                </p>
              )}
              {scope === 'global' && (props as GlobalFindingProps).finding.run_id && (
                <div className="flex gap-2 flex-wrap">
                  <Link
                    to={`/experiments/${(props as GlobalFindingProps).finding.experiment_id}/runs/${
                      (props as GlobalFindingProps).finding.run_id
                    }#finding-${fid}`}
                    className="text-xs px-3 py-1 rounded bg-amber-100 dark:bg-amber-900 text-amber-800 dark:text-amber-200 hover:bg-amber-200 dark:hover:bg-amber-800 transition-colors"
                  >
                    Open run
                  </Link>
                  {(props as GlobalFindingProps).finding.dataset_name &&
                    (props as GlobalFindingProps).finding.file_path && (
                      <Link
                        to={`/datasets/${encodeURIComponent(
                          (props as GlobalFindingProps).finding.dataset_name
                        )}?file=${encodeURIComponent(
                          (props as GlobalFindingProps).finding.file_path ?? ''
                        )}&line=${(props as GlobalFindingProps).finding.line_start ?? ''}`}
                        className="text-xs px-3 py-1 rounded bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
                      >
                        View source
                      </Link>
                    )}
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
