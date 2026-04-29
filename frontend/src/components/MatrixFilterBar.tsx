import { useMemo, useState, useRef, useEffect } from 'react'
import type { Run } from '../api/client'
import type { MatrixFilter } from '../lib/matrixFilter'
import { clearMatrixFilter, isEmpty, applyMatrixFilter } from '../lib/matrixFilter'

interface MatrixFilterBarProps {
  runs: Run[]
  value: MatrixFilter
  onChange: (next: MatrixFilter) => void
}

interface DimensionConfig {
  key: keyof MatrixFilter
  label: string
  values: string[]
}

function PopoverFilter({
  label,
  dimKey,
  options,
  selected,
  onToggle,
}: {
  label: string
  dimKey: keyof MatrixFilter
  options: string[]
  selected: string[]
  onToggle: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    if (open) {
      document.addEventListener('mousedown', handleClick)
    }
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  const hasActive = selected.length > 0
  const buttonId = `filter-btn-${dimKey}`
  const popoverId = `filter-pop-${dimKey}`

  return (
    <div ref={ref} className="relative">
      <button
        id={buttonId}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={popoverId}
        onClick={() => setOpen((o) => !o)}
        className={[
          'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-sm transition-colors',
          'focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none',
          hasActive
            ? 'border-amber-400 bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-300 font-medium'
            : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800',
        ].join(' ')}
      >
        {label}
        {hasActive && (
          <span className="inline-flex items-center justify-center w-4 h-4 text-[10px] font-bold bg-amber-600 text-white rounded-full">
            {selected.length}
          </span>
        )}
        <span className="text-gray-400" aria-hidden="true">
          {open ? '▲' : '▼'}
        </span>
      </button>

      {open && (
        <div
          id={popoverId}
          role="listbox"
          aria-multiselectable="true"
          aria-labelledby={buttonId}
          className="absolute left-0 top-full mt-1 z-30 min-w-[140px] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg py-1"
        >
          {options.map((opt) => {
            const checked = selected.includes(opt)
            return (
              <button
                key={opt}
                role="option"
                aria-selected={checked}
                aria-pressed={checked}
                onClick={() => onToggle(opt)}
                className={[
                  'w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left transition-colors',
                  'hover:bg-gray-50 dark:hover:bg-gray-700',
                  checked ? 'text-amber-700 dark:text-amber-300 font-medium' : 'text-gray-700 dark:text-gray-200',
                ].join(' ')}
              >
                <span
                  className={[
                    'flex-shrink-0 w-4 h-4 rounded border transition-colors',
                    checked
                      ? 'bg-amber-600 border-amber-600'
                      : 'border-gray-300 dark:border-gray-600',
                  ].join(' ')}
                  aria-hidden="true"
                >
                  {checked && (
                    <svg viewBox="0 0 12 12" fill="none" className="w-full h-full text-white">
                      <path d="M2 6l3 3 5-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </span>
                <span className="font-mono text-xs">{opt}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default function MatrixFilterBar({ runs, value, onChange }: MatrixFilterBarProps) {
  const dimensions = useMemo<DimensionConfig[]>(() => {
    const unique = (vals: string[]) => [...new Set(vals)].sort()

    const allDims: DimensionConfig[] = [
      {
        key: 'model',
        label: 'Model',
        values: unique(runs.map((r) => r.model)),
      },
      {
        key: 'strategy',
        label: 'Strategy',
        values: unique(runs.map((r) => r.strategy)),
      },
      {
        key: 'tool',
        label: 'Tools',
        values: unique(runs.map((r) => r.tool_variant)),
      },
      {
        key: 'ext',
        label: 'Extensions',
        values: unique(runs.flatMap((r) => r.tool_extensions ?? [])),
      },
      {
        key: 'profile',
        label: 'Profile',
        values: unique(runs.map((r) => r.profile)),
      },
      {
        key: 'status',
        label: 'Status',
        values: unique(runs.map((r) => r.status)),
      },
    ]

    // Hide dimensions with ≤1 unique value
    return allDims.filter((d) => d.values.length > 1)
  }, [runs])

  const filteredCount = useMemo(
    () => applyMatrixFilter(runs, value).length,
    [runs, value]
  )

  const handleToggle = (dimKey: keyof MatrixFilter, val: string) => {
    const current = value[dimKey]
    const next = current.includes(val)
      ? current.filter((v) => v !== val)
      : [...current, val]
    onChange({ ...value, [dimKey]: next })
  }

  if (dimensions.length === 0) return null

  const isActive = !isEmpty(value)

  return (
    <div className="flex flex-wrap items-center gap-2 mb-4">
      {dimensions.map((dim) => (
        <PopoverFilter
          key={dim.key}
          label={dim.label}
          dimKey={dim.key}
          options={dim.values}
          selected={value[dim.key]}
          onToggle={(val) => handleToggle(dim.key, val)}
        />
      ))}

      <span className="text-sm text-gray-500 dark:text-gray-400 ml-1">
        {filteredCount} of {runs.length} runs
      </span>

      <button
        onClick={() => onChange(clearMatrixFilter())}
        disabled={!isActive}
        className={[
          'ml-auto px-3 py-1.5 rounded-lg border text-sm transition-colors',
          'focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none',
          isActive
            ? 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800'
            : 'border-transparent text-gray-400 dark:text-gray-600 cursor-default',
        ].join(' ')}
      >
        Clear filters
      </button>
    </div>
  )
}
