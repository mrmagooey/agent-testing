import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { listStrategiesFull, type StrategySummary, type OrchestrationShape } from '../api/client'
import PageDescription from '../components/PageDescription'

const SHAPE_LABELS: Record<OrchestrationShape, string> = {
  single_agent: 'Single Agent',
  per_file: 'Per File',
  per_vuln_class: 'Per Vuln Class',
  sast_first: 'SAST First',
  diff_review: 'Diff Review',
}

const ALL_SHAPES: OrchestrationShape[] = [
  'single_agent',
  'per_file',
  'per_vuln_class',
  'sast_first',
  'diff_review',
]

export default function StrategiesList() {
  const navigate = useNavigate()
  const [strategies, setStrategies] = useState<StrategySummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Filters
  const [shapeFilter, setShapeFilter] = useState<OrchestrationShape | ''>('')
  const [builtinFilter, setBuiltinFilter] = useState<'all' | 'builtin' | 'user'>('all')

  useEffect(() => {
    listStrategiesFull()
      .then(setStrategies)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const filtered = strategies.filter((s) => {
    if (shapeFilter && s.orchestration_shape !== shapeFilter) return false
    if (builtinFilter === 'builtin' && !s.is_builtin) return false
    if (builtinFilter === 'user' && s.is_builtin) return false
    return true
  })

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading…</div>
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Strategies</h1>
        <button
          onClick={() => navigate('/strategies/new')}
          className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-sm font-semibold transition-colors"
        >
          New Strategy
        </button>
      </div>
      <PageDescription>
        Strategies bundle orchestration shape, prompts, model config, and per-subagent overrides into
        an immutable, versioned unit. Fork a builtin to customize it.
      </PageDescription>
      <div className="h-4" />

      {error && (
        <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-4">
        <select
          value={shapeFilter}
          onChange={(e) => setShapeFilter(e.target.value as OrchestrationShape | '')}
          className="text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-1.5"
          aria-label="Filter by shape"
          data-testid="shape-filter"
        >
          <option value="">All shapes</option>
          {ALL_SHAPES.map((s) => (
            <option key={s} value={s}>
              {SHAPE_LABELS[s]}
            </option>
          ))}
        </select>

        <div className="flex rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden text-sm">
          {(['all', 'builtin', 'user'] as const).map((v) => (
            <button
              key={v}
              onClick={() => setBuiltinFilter(v)}
              className={`px-3 py-1.5 capitalize transition-colors ${
                builtinFilter === v
                  ? 'bg-amber-600 text-white'
                  : 'bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800'
              }`}
              data-testid={`filter-${v}`}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
              <th className="text-left px-4 py-3 font-medium text-gray-600 dark:text-gray-400">Name</th>
              <th className="text-left px-4 py-3 font-medium text-gray-600 dark:text-gray-400">Shape</th>
              <th className="text-left px-4 py-3 font-medium text-gray-600 dark:text-gray-400">Type</th>
              <th className="text-left px-4 py-3 font-medium text-gray-600 dark:text-gray-400">Parent</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-gray-400 dark:text-gray-500">
                  No strategies match the current filter.
                </td>
              </tr>
            ) : (
              filtered.map((s) => (
                <tr
                  key={s.id}
                  className="border-b border-gray-100 dark:border-gray-700 last:border-0 hover:bg-gray-50 dark:hover:bg-gray-750 transition-colors"
                  data-testid="strategy-row"
                >
                  <td className="px-4 py-3 font-medium text-gray-900 dark:text-gray-100">
                    <span className="font-mono text-xs text-gray-400 dark:text-gray-500 block">
                      {s.id}
                    </span>
                    {s.name}
                  </td>
                  <td className="px-4 py-3 text-gray-700 dark:text-gray-300">
                    <span className="font-mono text-xs bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded">
                      {s.orchestration_shape}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {s.is_builtin ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300">
                        builtin
                      </span>
                    ) : (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300">
                        user
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-500 dark:text-gray-400 font-mono text-xs">
                    {s.parent_strategy_id ? (
                      <span title={`Forked from: ${s.parent_strategy_id}`}>
                        {s.parent_strategy_id.length > 24
                          ? `…${s.parent_strategy_id.slice(-20)}`
                          : s.parent_strategy_id}
                      </span>
                    ) : (
                      <span className="text-gray-300 dark:text-gray-600">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => navigate(`/strategies/${encodeURIComponent(s.id)}`)}
                      className="px-3 py-1 rounded-lg border border-gray-200 dark:border-gray-600 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                      data-testid="view-btn"
                    >
                      View
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
