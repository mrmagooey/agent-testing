import { useRef, useState } from 'react'
import { downloadReports, exportBundleUrl } from '../api/client'

export interface ExportMenuProps {
  experimentId: string
}

type DatasetMode = 'descriptor' | 'reference'

const MODE_LABELS: Record<DatasetMode, string> = {
  descriptor: 'Descriptor (recommended)',
  reference: 'Reference only',
}

const MODE_DESCRIPTIONS: Record<DatasetMode, string> = {
  descriptor:
    "Bundle records each dataset's origin (e.g. git URL + commit). Target deployment will re-clone or re-derive on demand.",
  reference: 'Bundle records dataset names only. Target must already have them.',
}

export default function ExportMenu({ experimentId }: ExportMenuProps) {
  const detailsRef = useRef<HTMLDetailsElement>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [datasetMode, setDatasetMode] = useState<DatasetMode>('descriptor')

  const closeMenu = () => {
    detailsRef.current?.removeAttribute('open')
  }

  const handleDownloadReports = () => {
    const url = downloadReports(experimentId)
    const a = document.createElement('a')
    a.href = url
    a.download = `experiment-${experimentId}-reports.zip`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    closeMenu()
  }

  const handleOpenBundleDialog = () => {
    setDatasetMode('descriptor')
    setDialogOpen(true)
    closeMenu()
  }

  const handleExportBundle = () => {
    const url = exportBundleUrl(experimentId, datasetMode)
    const a = document.createElement('a')
    a.href = url
    a.download = `${experimentId}.secrev.zip`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setDialogOpen(false)
  }

  return (
    <>
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
            onClick={handleOpenBundleDialog}
            className="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
            data-testid="export-bundle-btn"
          >
            Export full bundle (.secrev.zip)
          </button>
        </div>
      </details>

      {/* Export bundle dialog */}
      {dialogOpen && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="export-dialog-title"
        >
          <div className="bg-white dark:bg-gray-800 rounded-xl w-full max-w-md shadow-2xl p-6 space-y-5">
            <h2 id="export-dialog-title" className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              Export Full Bundle
            </h2>

            <fieldset className="space-y-3">
              <legend className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
                Dataset mode
              </legend>
              {(['descriptor', 'reference'] as DatasetMode[]).map((mode) => (
                <label
                  key={mode}
                  className="flex items-start gap-3 cursor-pointer"
                  data-testid={`dataset-mode-label-${mode}`}
                >
                  <input
                    type="radio"
                    name="dataset_mode"
                    value={mode}
                    checked={datasetMode === mode}
                    onChange={() => setDatasetMode(mode)}
                    className="mt-0.5 accent-amber-600"
                    data-testid={`dataset-mode-radio-${mode}`}
                  />
                  <div>
                    <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
                      {MODE_LABELS[mode]}
                    </p>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                      {MODE_DESCRIPTIONS[mode]}
                    </p>
                  </div>
                </label>
              ))}
            </fieldset>

            <div className="flex justify-end gap-3 pt-2">
              <button
                onClick={() => setDialogOpen(false)}
                className="px-4 py-2 rounded-lg text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
                data-testid="export-dialog-cancel"
              >
                Cancel
              </button>
              <button
                onClick={handleExportBundle}
                className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-amber-600 hover:bg-amber-700 transition-colors"
                data-testid="export-dialog-confirm"
              >
                Export
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
