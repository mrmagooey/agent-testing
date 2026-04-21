interface PaginationProps {
  total: number
  limit: number
  offset: number
  onPageChange: (newOffset: number) => void
  onLimitChange: (newLimit: number) => void
  pageSizes?: number[]
}

export default function Pagination({
  total,
  limit,
  offset,
  onPageChange,
  onLimitChange,
  pageSizes = [25, 50, 100],
}: PaginationProps) {
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + limit, total)
  const hasPrev = offset > 0
  const hasNext = offset + limit < total

  return (
    <div className="flex items-center justify-between py-3 border-t border-gray-200 dark:border-gray-700">
      <p className="text-sm text-gray-500 dark:text-gray-400">
        {total === 0
          ? 'No results'
          : `Showing ${from}–${to} of ${total.toLocaleString()}`}
      </p>

      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1">
          <label className="text-xs text-gray-500 dark:text-gray-400" htmlFor="page-size-select">
            Per page:
          </label>
          <select
            id="page-size-select"
            value={limit}
            onChange={(e) => {
              onLimitChange(Number(e.target.value))
              onPageChange(0)
            }}
            className="text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1"
          >
            {pageSizes.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-1">
          <button
            onClick={() => onPageChange(Math.max(0, offset - limit))}
            disabled={!hasPrev}
            aria-label="Previous page"
            className="px-2 py-1 text-xs rounded border border-gray-200 dark:border-gray-700 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
          >
            ← Prev
          </button>
          <button
            onClick={() => onPageChange(offset + limit)}
            disabled={!hasNext}
            aria-label="Next page"
            className="px-2 py-1 text-xs rounded border border-gray-200 dark:border-gray-700 disabled:opacity-40 disabled:cursor-not-allowed hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
          >
            Next →
          </button>
        </div>
      </div>
    </div>
  )
}
