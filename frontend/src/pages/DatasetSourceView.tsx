import { useParams, useSearchParams, Link } from 'react-router-dom'
import Breadcrumbs from '../components/Breadcrumbs'
import DatasetSourceViewer from '../components/DatasetSourceViewer'

export default function DatasetSourceView() {
  const { name: datasetName } = useParams<{ name: string }>()
  const [searchParams] = useSearchParams()

  const filePath = searchParams.get('path') ?? ''
  const lineParam = searchParams.get('line')
  const endParam = searchParams.get('end')
  const fromExperiment = searchParams.get('from_experiment')
  const fromRun = searchParams.get('from_run')

  const highlightStart = lineParam != null ? parseInt(lineParam, 10) : undefined
  const highlightEnd = endParam != null ? parseInt(endParam, 10) : undefined

  const breadcrumbItems = [
    { label: 'Datasets', to: '/datasets' },
    { label: datasetName ?? '', to: `/datasets/${datasetName ?? ''}` },
    { label: filePath || 'Source' },
  ]

  return (
    <div className="space-y-4">
      <Breadcrumbs items={breadcrumbItems} />

      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-lg font-semibold font-mono text-gray-900 dark:text-gray-100 break-all">
          {filePath || '(no path)'}
        </h1>
        {fromExperiment && fromRun && (
          <Link
            to={`/experiments/${fromExperiment}/runs/${fromRun}`}
            className="text-xs px-3 py-1.5 rounded border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
          >
            Back to run
          </Link>
        )}
      </div>

      {datasetName && filePath ? (
        <DatasetSourceViewer
          datasetName={datasetName}
          filePath={filePath}
          highlightStart={highlightStart}
          highlightEnd={highlightEnd}
        />
      ) : (
        <div className="rounded border border-gray-200 dark:border-gray-700 px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
          No file path specified.
        </div>
      )}
    </div>
  )
}
