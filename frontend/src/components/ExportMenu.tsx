import { useRef } from 'react'
import { downloadReports, exportBundleUrl } from '../api/client'

export interface ExportMenuProps {
  experimentId: string
}

export default function ExportMenu({ experimentId }: ExportMenuProps) {
  const detailsRef = useRef<HTMLDetailsElement>(null)

  const handleDownloadReports = () => {
    const url = downloadReports(experimentId)
    const a = document.createElement('a')
    a.href = url
    a.download = `experiment-${experimentId}-reports.zip`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    detailsRef.current?.removeAttribute('open')
  }

  const handleExportBundle = () => {
    const url = exportBundleUrl(experimentId, false)
    const a = document.createElement('a')
    a.href = url
    a.download = `${experimentId}.secrev.zip`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    detailsRef.current?.removeAttribute('open')
  }

  return (
    <details ref={detailsRef} className="relative inline-block">
      <summary className="cursor-pointer list-none bg-amber-600 hover:bg-amber-700 text-white rounded px-4 py-2 text-sm font-medium transition-colors select-none">
        Download ▾
      </summary>
      <div className="absolute right-0 mt-1 z-10 min-w-48 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg overflow-hidden">
        <button
          onClick={handleDownloadReports}
          className="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
        >
          Download reports (.zip)
        </button>
        <button
          onClick={handleExportBundle}
          className="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
          data-testid="export-bundle-btn"
        >
          Export full bundle (.secrev.zip)
        </button>
      </div>
    </details>
  )
}
