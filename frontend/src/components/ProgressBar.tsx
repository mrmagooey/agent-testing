export interface ProgressBarProps {
  completed: number
  running: number
  pending: number
  failed: number
  total: number
}

export default function ProgressBar({ completed, running, pending, failed, total }: ProgressBarProps) {
  if (total === 0) return null

  const pct = (n: number) => `${((n / total) * 100).toFixed(1)}%`
  const overallPct = Math.round((completed / total) * 100)

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <div className="flex-1 h-4 rounded-full overflow-hidden bg-gray-200 dark:bg-gray-700 flex">
          {completed > 0 && (
            <div
              className="bg-green-500 h-full transition-all"
              style={{ width: pct(completed) }}
              title={`${completed} completed`}
            />
          )}
          {running > 0 && (
            <div
              className="bg-blue-500 h-full animate-pulse transition-all"
              style={{ width: pct(running) }}
              title={`${running} running`}
            />
          )}
          {failed > 0 && (
            <div
              className="bg-red-500 h-full transition-all"
              style={{ width: pct(failed) }}
              title={`${failed} failed`}
            />
          )}
          {/* pending fills remaining */}
        </div>
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300 w-12 text-right">
          {overallPct}%
        </span>
      </div>
      <div className="flex gap-4 text-xs text-gray-500 dark:text-gray-400">
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
          {completed} completed
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-blue-500 inline-block" />
          {running} running
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-gray-400 inline-block" />
          {pending} pending
        </span>
        {failed > 0 && (
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-red-500 inline-block" />
            {failed} failed
          </span>
        )}
        <span className="ml-auto">{total} total</span>
      </div>
    </div>
  )
}
