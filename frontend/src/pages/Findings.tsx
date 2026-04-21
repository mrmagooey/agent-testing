import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  searchFindingsGlobal,
  type GlobalFinding,
  type GlobalFindingsResponse,
  type FindingFacets,
} from '../api/client'
import EmptyState from '../components/EmptyState'
import FindingRow from '../components/FindingRow'
import FindingsFilterBar from '../components/FindingsFilterBar'
import Pagination from '../components/Pagination'
import { PageLoadingSpinner } from '../components/Skeleton'

const EMPTY_FACETS: FindingFacets = {
  vuln_class: {},
  severity: {},
  match_status: {},
  model_id: {},
  strategy: {},
  dataset_name: {},
}

const DEFAULT_LIMIT = 50

function parseList(params: URLSearchParams, key: string): string[] {
  return params.getAll(key)
}

export default function Findings() {
  const [searchParams, setSearchParams] = useSearchParams()

  // Derive state from URL
  const q = searchParams.get('q') ?? ''
  const vuln_class = parseList(searchParams, 'vuln_class')
  const severity = parseList(searchParams, 'severity')
  const match_status = parseList(searchParams, 'match_status')
  const model_id = parseList(searchParams, 'model_id')
  const strategy = parseList(searchParams, 'strategy')
  const experiment_id = parseList(searchParams, 'experiment_id')
  const dataset_name = parseList(searchParams, 'dataset_name')
  const created_from = searchParams.get('created_from') ?? ''
  const created_to = searchParams.get('created_to') ?? ''
  const sort = searchParams.get('sort') ?? 'created_at desc'
  const limit = Number(searchParams.get('limit') ?? DEFAULT_LIMIT)
  const offset = Number(searchParams.get('offset') ?? 0)

  const [data, setData] = useState<GlobalFindingsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Debounced search query for text input
  const [searchInput, setSearchInput] = useState(q)
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const updateParam = useCallback(
    (updates: Record<string, string | string[] | null>) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        for (const [key, val] of Object.entries(updates)) {
          next.delete(key)
          if (val === null) continue
          if (Array.isArray(val)) {
            for (const v of val) next.append(key, v)
          } else if (val !== '') {
            next.set(key, val)
          }
        }
        // Reset offset when filters change (but not when offset itself changes)
        if (!('offset' in updates)) {
          next.delete('offset')
        }
        return next
      })
    },
    [setSearchParams]
  )

  // Fetch data when URL params change
  useEffect(() => {
    if (abortRef.current) abortRef.current.abort()
    abortRef.current = new AbortController()

    setLoading(true)
    setError(null)

    searchFindingsGlobal({
      q: q || undefined,
      vuln_class: vuln_class.length ? vuln_class : undefined,
      severity: severity.length ? severity : undefined,
      match_status: match_status.length ? match_status : undefined,
      model_id: model_id.length ? model_id : undefined,
      strategy: strategy.length ? strategy : undefined,
      experiment_id: experiment_id.length ? experiment_id : undefined,
      dataset_name: dataset_name.length ? dataset_name : undefined,
      created_from: created_from || undefined,
      created_to: created_to || undefined,
      sort,
      limit,
      offset,
    })
      .then((res) => {
        setData(res)
        setLoading(false)
      })
      .catch((err) => {
        if ((err as Error).name === 'AbortError') return
        setError((err as Error).message)
        setLoading(false)
      })

    return () => abortRef.current?.abort()
  }, [searchParams]) // eslint-disable-line react-hooks/exhaustive-deps

  // Sync text input to URL with debounce
  const handleSearchInput = (value: string) => {
    setSearchInput(value)
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current)
    searchDebounceRef.current = setTimeout(() => {
      updateParam({ q: value || null, offset: null })
    }, 350)
  }

  const handleFilterChange = (key: string, values: string[]) => {
    updateParam({ [key]: values, offset: null })
  }

  const handleDateChange = (key: 'created_from' | 'created_to', value: string) => {
    updateParam({ [key]: value || null, offset: null })
  }

  const handleClearAll = () => {
    setSearchInput('')
    setSearchParams(new URLSearchParams())
  }

  const handlePageChange = (newOffset: number) => {
    updateParam({ offset: newOffset ? String(newOffset) : null })
  }

  const handleLimitChange = (newLimit: number) => {
    updateParam({ limit: String(newLimit), offset: null })
  }

  const facets: FindingFacets = data?.facets ?? EMPTY_FACETS

  const activeFilters = {
    vuln_class,
    severity,
    match_status,
    model_id,
    strategy,
    dataset_name,
    created_from,
    created_to,
  }

  const items: GlobalFinding[] = data?.items ?? []
  const total = data?.total ?? 0

  const isIndexEmpty = !loading && !error && total === 0 && !q &&
    vuln_class.length === 0 && severity.length === 0 &&
    match_status.length === 0 && model_id.length === 0 &&
    strategy.length === 0 && dataset_name.length === 0 &&
    !created_from && !created_to

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Findings</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Search across all experiments — find where a CWE was missed or which runs flagged a finding.
        </p>
      </div>

      {/* Search bar */}
      <div className="mb-4">
        <input
          type="search"
          value={searchInput}
          onChange={(e) => handleSearchInput(e.target.value)}
          placeholder="Search findings by title, description, vuln class, CWE…"
          className="w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
          aria-label="Search findings"
        />
      </div>

      <div className="flex gap-6 items-start">
        {/* Filter sidebar */}
        <FindingsFilterBar
          facets={facets}
          activeFilters={activeFilters}
          onFilterChange={handleFilterChange}
          onDateChange={handleDateChange}
          onClearAll={handleClearAll}
        />

        {/* Results */}
        <div className="flex-1 min-w-0">
          {loading ? (
            <PageLoadingSpinner />
          ) : error ? (
            <EmptyState
              title="Failed to load findings"
              subtitle={error}
            />
          ) : isIndexEmpty ? (
            <EmptyState
              title="No findings indexed yet"
              subtitle="Findings are indexed when experiment runs complete. Run an experiment to populate this view."
            />
          ) : items.length === 0 ? (
            <EmptyState
              title="No results for current filters"
              subtitle="Try broadening your search or removing some filters."
            />
          ) : (
            <>
              {/* Sort + count */}
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm text-gray-500 dark:text-gray-400">
                  {total.toLocaleString()} finding{total !== 1 ? 's' : ''}
                </span>
                <div className="flex items-center gap-2">
                  <label className="text-xs text-gray-500 dark:text-gray-400" htmlFor="sort-select">
                    Sort:
                  </label>
                  <select
                    id="sort-select"
                    value={sort}
                    onChange={(e) => updateParam({ sort: e.target.value, offset: null })}
                    className="text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1"
                  >
                    <option value="created_at desc">Newest first</option>
                    <option value="created_at asc">Oldest first</option>
                    <option value="severity desc">Severity (high→low)</option>
                    <option value="confidence desc">Confidence (high→low)</option>
                    <option value="vuln_class asc">Vuln class A→Z</option>
                  </select>
                </div>
              </div>

              <div className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400 text-xs">
                    <tr>
                      <th className="px-3 py-2 text-left">Status</th>
                      <th className="px-3 py-2 text-left">Title</th>
                      <th className="px-3 py-2 text-left">Severity</th>
                      <th className="px-3 py-2 text-left">Vuln Class</th>
                      <th className="px-3 py-2 text-left">File</th>
                      <th className="px-3 py-2 text-left">Line</th>
                      <th className="px-3 py-2 text-left">Experiment</th>
                      <th className="px-3 py-2 text-left">Model</th>
                      <th className="px-3 py-2 text-left">Strategy</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                    {items.map((finding) => {
                      const fid = finding.finding_id ?? (finding as GlobalFinding & { id?: string }).id ?? ''
                      return (
                        <FindingRow
                          key={fid}
                          scope="global"
                          finding={finding}
                          expanded={expandedId === fid}
                          onToggle={() =>
                            setExpandedId((prev) => (prev === fid ? null : fid))
                          }
                        />
                      )
                    })}
                  </tbody>
                </table>
              </div>

              <Pagination
                total={total}
                limit={limit}
                offset={offset}
                onPageChange={handlePageChange}
                onLimitChange={handleLimitChange}
              />
            </>
          )}
        </div>
      </div>
    </div>
  )
}
