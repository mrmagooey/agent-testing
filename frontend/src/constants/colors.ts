// Single source of truth for severity / match-status / metric colors.
// Severity uses red→orange→amber→sky→slate (NOT yellow, which is reserved for warnings).
// Match status uses distinct hues so tp/fp/fn/unlabeled don't collide with severity.
// metricTone uses indigo/emerald/amber/red scale for metric thresholds.

export const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-red-600 text-white',
  high: 'bg-orange-500 text-white',
  medium: 'bg-amber-400 text-gray-900',
  low: 'bg-sky-400 text-white',
  info: 'bg-slate-400 text-white',
}

export const MATCH_STATUS_COLORS: Record<string, string> = {
  tp: 'bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200',
  fp: 'bg-rose-100 dark:bg-rose-900 text-rose-800 dark:text-rose-200',
  fn: 'bg-orange-100 dark:bg-orange-900 text-orange-800 dark:text-orange-200',
  unlabeled_real: 'bg-violet-100 dark:bg-violet-900 text-violet-800 dark:text-violet-200',
}

// Returns a Tailwind bg+text class pair for a 0–1 metric value.
// kind='higher-is-better' (precision/recall/f1) vs 'lower-is-better' (fpr).
export function metricTone(
  value: number | undefined,
  kind: 'higher-is-better' | 'lower-is-better' = 'higher-is-better'
): { cls: string; label: string } {
  if (value === undefined || value === null) return { cls: '', label: '' }
  const v = kind === 'lower-is-better' ? 1 - value : value
  if (v >= 0.8) return { cls: 'bg-emerald-100 dark:bg-emerald-900 text-emerald-800 dark:text-emerald-200', label: 'PASS' }
  if (v >= 0.6) return { cls: 'bg-amber-100 dark:bg-amber-900 text-amber-800 dark:text-amber-200', label: 'WARN' }
  return { cls: 'bg-rose-100 dark:bg-rose-900 text-rose-800 dark:text-rose-200', label: 'FAIL' }
}
