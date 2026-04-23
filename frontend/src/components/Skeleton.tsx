interface SkeletonProps {
  className?: string
}

export function SkeletonLine({ className = '' }: SkeletonProps) {
  return (
    <div className={`animate-pulse bg-gray-200 dark:bg-gray-700 rounded ${className}`} />
  )
}

export function SkeletonCard({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 p-4">
      {Array.from({ length: rows }).map((_, i) => (
        <SkeletonLine key={i} className={`h-4 ${i === 0 ? 'w-2/5' : i % 2 === 0 ? 'w-3/5' : 'w-4/5'}`} />
      ))}
    </div>
  )
}

export function SkeletonTable({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="animate-pulse space-y-2">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-4">
          {Array.from({ length: cols }).map((_, c) => (
            <div
              key={c}
              className={`h-4 bg-gray-200 dark:bg-gray-700 rounded flex-1 ${c === 0 ? 'max-w-[8rem]' : ''}`}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

export function PageLoadingSpinner() {
  return (
    <div className="flex items-center justify-center h-64 text-gray-400 dark:text-gray-500">
      <span className="inline-block h-6 w-6 rounded-full border-2 border-gray-300 dark:border-gray-600 border-t-amber-500 animate-spin mr-3" />
      Loading…
    </div>
  )
}
