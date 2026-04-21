import type { FindingFacets } from '../api/client'

interface FindingsFilterBarProps {
  facets: FindingFacets
  activeFilters: {
    vuln_class: string[]
    severity: string[]
    match_status: string[]
    model_id: string[]
    strategy: string[]
    dataset_name: string[]
    created_from: string
    created_to: string
  }
  onFilterChange: (key: string, values: string[]) => void
  onDateChange: (key: 'created_from' | 'created_to', value: string) => void
  onClearAll: () => void
}

const FACET_LABELS: Record<string, string> = {
  vuln_class: 'Vuln Class',
  severity: 'Severity',
  match_status: 'Status',
  model_id: 'Model',
  strategy: 'Strategy',
  dataset_name: 'Dataset',
}

function FacetGroup({
  label,
  facetKey,
  options,
  selected,
  onToggle,
}: {
  label: string
  facetKey: string
  options: Record<string, number>
  selected: string[]
  onToggle: (value: string) => void
}) {
  const entries = Object.entries(options)
  if (entries.length === 0) return null

  return (
    <div className="mb-4">
      <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1">
        {label}
      </p>
      <div className="flex flex-wrap gap-1">
        {entries.map(([value, count]) => {
          const active = selected.includes(value)
          return (
            <button
              key={value}
              onClick={() => onToggle(value)}
              data-testid={`filter-chip-${facetKey}-${value}`}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border transition-colors ${
                active
                  ? 'bg-indigo-600 text-white border-indigo-600'
                  : 'bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 border-gray-200 dark:border-gray-700 hover:border-indigo-400 dark:hover:border-indigo-500'
              }`}
            >
              {value}
              <span
                className={`${
                  active ? 'text-indigo-200' : 'text-gray-400 dark:text-gray-500'
                }`}
              >
                {count}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default function FindingsFilterBar({
  facets,
  activeFilters,
  onFilterChange,
  onDateChange,
  onClearAll,
}: FindingsFilterBarProps) {
  const hasAnyFilter =
    Object.values(activeFilters).some((v) =>
      Array.isArray(v) ? v.length > 0 : v !== ''
    )

  function toggleValue(key: string, value: string) {
    const current = (activeFilters as unknown as Record<string, string[]>)[key] ?? []
    if (current.includes(value)) {
      onFilterChange(key, current.filter((v) => v !== value))
    } else {
      onFilterChange(key, [...current, value])
    }
  }

  return (
    <aside className="w-56 shrink-0">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">Filters</h2>
        {hasAnyFilter && (
          <button
            onClick={onClearAll}
            className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
          >
            Clear all
          </button>
        )}
      </div>

      {(Object.entries(FACET_LABELS) as [keyof typeof FACET_LABELS, string][]).map(
        ([key, label]) => (
          <FacetGroup
            key={key}
            label={label}
            facetKey={key}
            options={facets[key as keyof FindingFacets] ?? {}}
            selected={(activeFilters as unknown as Record<string, string[]>)[key] ?? []}
            onToggle={(value) => toggleValue(key, value)}
          />
        )
      )}

      <div className="mb-4">
        <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1">
          Date Range
        </p>
        <div className="space-y-1">
          <input
            type="date"
            value={activeFilters.created_from}
            onChange={(e) => onDateChange('created_from', e.target.value)}
            className="w-full text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1"
            placeholder="From"
            aria-label="Created from"
          />
          <input
            type="date"
            value={activeFilters.created_to}
            onChange={(e) => onDateChange('created_to', e.target.value)}
            className="w-full text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-2 py-1"
            placeholder="To"
            aria-label="Created to"
          />
        </div>
      </div>
    </aside>
  )
}
