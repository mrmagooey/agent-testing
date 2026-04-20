import type { ReactNode } from 'react'

export default function PageDescription({ children }: { children: ReactNode }) {
  return (
    <p
      data-testid="page-description"
      className="text-sm text-gray-600 dark:text-gray-400 max-w-3xl leading-relaxed"
    >
      {children}
    </p>
  )
}
