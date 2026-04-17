interface EmptyStateProps {
  icon?: React.ReactNode
  title: string
  subtitle?: string
}

const DEFAULT_ICON = (
  <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
      d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
  </svg>
)

export default function EmptyState({ icon = DEFAULT_ICON, title, subtitle }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-10 text-gray-400 dark:text-gray-500">
      <div className="mb-3 opacity-40">{icon}</div>
      <p className="text-sm font-medium">{title}</p>
      {subtitle && <p className="text-xs mt-1">{subtitle}</p>}
    </div>
  )
}
