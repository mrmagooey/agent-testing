import { downloadReports } from '../api/client'

export interface DownloadButtonProps {
  batchId: string
  label?: string
}

export default function DownloadButton({ batchId, label = 'Download Reports' }: DownloadButtonProps) {
  const handleDownload = () => {
    const url = downloadReports(batchId)
    const a = document.createElement('a')
    a.href = url
    a.download = `batch-${batchId}-reports.zip`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }

  return (
    <button
      onClick={handleDownload}
      className="bg-indigo-600 hover:bg-indigo-700 text-white rounded px-4 py-2 text-sm font-medium transition-colors"
    >
      {label}
    </button>
  )
}
