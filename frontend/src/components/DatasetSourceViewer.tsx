import { useState, useEffect } from 'react'
import type { Label } from '../api/client'
import type { Annotation } from './CodeViewer'
import CodeViewer from './CodeViewer'

export interface DatasetSourceViewerProps {
  datasetName: string
  filePath: string
  highlightStart?: number
  highlightEnd?: number
  groundTruthLabels?: Label[]
}

interface FileContentResponse {
  path: string
  content: string
  language: string
  line_count: number
  size_bytes: number
  labels: Label[]
  binary?: boolean
  truncated?: boolean
  highlight_start?: number
  highlight_end?: number
}

const FINDING_LINE_CLASS = 'bg-amber-100 dark:bg-amber-900/40 border-l-2 border-amber-500'
const GT_LINE_CLASS = 'bg-emerald-100 dark:bg-emerald-900/40 border-l-2 border-emerald-500'
const OVERLAP_LINE_CLASS =
  'bg-amber-100 dark:bg-amber-900/40 border-l-2 border-amber-500 outline outline-1 outline-emerald-500'

function buildAnnotations(
  highlightStart: number | undefined,
  highlightEnd: number | undefined,
  labels: Label[],
): Annotation[] {
  const annotations: Annotation[] = []

  const findingLines = new Set<number>()
  if (highlightStart != null && highlightEnd != null) {
    for (let ln = highlightStart; ln <= highlightEnd; ln++) {
      findingLines.add(ln)
    }
  } else if (highlightStart != null) {
    findingLines.add(highlightStart)
  }

  const gtLines = new Set<number>()
  for (const label of labels) {
    for (let ln = label.line_start; ln <= label.line_end; ln++) {
      gtLines.add(ln)
    }
  }

  const allLines = new Set([...findingLines, ...gtLines])
  for (const ln of allLines) {
    const isFinding = findingLines.has(ln)
    const isGt = gtLines.has(ln)
    let className: string
    if (isFinding && isGt) {
      className = OVERLAP_LINE_CLASS
    } else if (isFinding) {
      className = FINDING_LINE_CLASS
    } else {
      className = GT_LINE_CLASS
    }
    annotations.push({ line: ln, className })
  }

  return annotations.sort((a, b) => a.line - b.line)
}

export default function DatasetSourceViewer({
  datasetName,
  filePath,
  highlightStart,
  highlightEnd,
  groundTruthLabels,
}: DatasetSourceViewerProps) {
  const [data, setData] = useState<FileContentResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [errorStatus, setErrorStatus] = useState<number | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [forceLoad, setForceLoad] = useState(false)

  useEffect(() => {
    setLoading(true)
    setErrorStatus(null)
    setErrorMessage(null)
    setData(null)

    const params = new URLSearchParams({ path: filePath })
    if (highlightStart != null) params.set('start', String(highlightStart))
    if (highlightEnd != null) params.set('end', String(highlightEnd))

    fetch(`/api/datasets/${encodeURIComponent(datasetName)}/file?${params}`)
      .then(async (res) => {
        if (!res.ok) {
          let detail = `HTTP ${res.status}`
          try {
            const body = await res.json()
            detail = body.detail ?? detail
          } catch {
            // ignore
          }
          setErrorStatus(res.status)
          setErrorMessage(detail)
          return
        }
        const json = (await res.json()) as FileContentResponse
        setData(json)
      })
      .catch((err: unknown) => {
        setErrorStatus(-1)
        setErrorMessage(err instanceof Error ? err.message : 'Network error')
      })
      .finally(() => setLoading(false))
  }, [datasetName, filePath, highlightStart, highlightEnd])

  if (loading) {
    return (
      <div className="space-y-2 animate-pulse" aria-label="Loading source file">
        <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-3/4" />
        <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-full" />
        <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-5/6" />
        <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-full" />
        <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-2/3" />
      </div>
    )
  }

  if (errorStatus === 404) {
    return (
      <div className="rounded border border-gray-200 dark:border-gray-700 px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
        File no longer in dataset: <code className="font-mono">{filePath}</code>
      </div>
    )
  }

  if (errorStatus === 400) {
    return (
      <div className="rounded border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 px-4 py-4 text-sm text-red-700 dark:text-red-300">
        {errorMessage ?? 'path escapes dataset'}
      </div>
    )
  }

  if (errorStatus != null) {
    return (
      <div className="rounded border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 px-4 py-4 text-sm text-red-700 dark:text-red-300">
        {errorMessage}
      </div>
    )
  }

  if (!data) return null

  if (data.binary) {
    return (
      <div className="rounded border border-gray-200 dark:border-gray-700 px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
        Binary file — cannot display content.
      </div>
    )
  }

  const effectiveLabels = groundTruthLabels ?? data.labels ?? []
  const annotations = buildAnnotations(highlightStart, highlightEnd, effectiveLabels)

  return (
    <div className="space-y-2">
      {data.truncated && !forceLoad && (
        <div className="flex items-center justify-between rounded border border-yellow-200 dark:border-yellow-700 bg-yellow-50 dark:bg-yellow-950 px-3 py-2 text-xs text-yellow-800 dark:text-yellow-300">
          <span>
            File is large ({(data.size_bytes / (1024 * 1024)).toFixed(1)} MiB) — showing head and
            tail snippet. Full content is not available.
          </span>
          <button
            onClick={() => setForceLoad(true)}
            className="ml-3 underline hover:no-underline focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none"
          >
            Dismiss
          </button>
        </div>
      )}
      <div className="flex gap-3 text-xs text-gray-500 dark:text-gray-400 flex-wrap">
        {annotations.some((a) => a.className.includes('amber')) && (
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-3 rounded-sm bg-amber-400 border border-amber-500" />
            Finding range
          </span>
        )}
        {annotations.some((a) => a.className.includes('emerald')) && (
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-3 rounded-sm bg-emerald-400 border border-emerald-500" />
            Ground-truth label
          </span>
        )}
      </div>
      <CodeViewer
        content={data.content}
        language={data.language}
        annotations={annotations}
        scrollToLine={highlightStart}
        maxHeight="600px"
      />
    </div>
  )
}
