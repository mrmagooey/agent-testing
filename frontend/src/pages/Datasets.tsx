import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { listDatasets, type Dataset } from '../api/client'

function humanBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
  })
}

const SOURCE_BADGE: Record<string, string> = {
  cve: 'bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200',
  injected: 'bg-orange-100 dark:bg-orange-900 text-orange-800 dark:text-orange-200',
  manual: 'bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200',
}

export default function Datasets() {
  const navigate = useNavigate()
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listDatasets()
      .then(setDatasets)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading...</div>
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
        {error}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Datasets</h1>
        <button
          onClick={() => navigate('/datasets/discover')}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-sm font-medium transition-colors"
        >
          Discover CVEs
        </button>
      </div>

      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
            <tr>
              <th className="px-4 py-3 text-left">Name</th>
              <th className="px-4 py-3 text-left">Source</th>
              <th className="px-4 py-3 text-left">Labels</th>
              <th className="px-4 py-3 text-left">Files</th>
              <th className="px-4 py-3 text-left">Size</th>
              <th className="px-4 py-3 text-left">Languages</th>
              <th className="px-4 py-3 text-left">Created</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {datasets.map((d) => (
              <tr
                key={d.name}
                onClick={() => navigate(`/datasets/${encodeURIComponent(d.name)}`)}
                className="cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
              >
                <td className="px-4 py-3 font-medium text-indigo-600 dark:text-indigo-400 font-mono">
                  {d.name}
                </td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${SOURCE_BADGE[d.source] ?? 'bg-gray-100 dark:bg-gray-800 text-gray-600'}`}>
                    {d.source}
                  </span>
                </td>
                <td className="px-4 py-3 text-gray-700 dark:text-gray-300">{d.label_count}</td>
                <td className="px-4 py-3 text-gray-700 dark:text-gray-300">{d.file_count}</td>
                <td className="px-4 py-3 text-gray-500 dark:text-gray-400 font-mono text-xs">
                  {humanBytes(d.size_bytes)}
                </td>
                <td className="px-4 py-3 text-gray-500 dark:text-gray-400 font-mono text-xs">
                  {d.languages.join(', ')}
                </td>
                <td className="px-4 py-3 text-gray-500 dark:text-gray-400 text-xs">
                  {formatDate(d.created_at)}
                </td>
              </tr>
            ))}
            {datasets.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center text-gray-400">
                  No datasets found. Use CVE Discovery to import one.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
