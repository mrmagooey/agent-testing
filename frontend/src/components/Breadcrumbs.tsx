import { Link } from 'react-router-dom'

export interface BreadcrumbItem {
  label: string
  to?: string
}

export interface BreadcrumbsProps {
  items: BreadcrumbItem[]
}

export default function Breadcrumbs({ items }: BreadcrumbsProps) {
  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm text-gray-500 dark:text-gray-300 mb-4">
      {items.map((item, i) => {
        const isLast = i === items.length - 1
        return (
          <span key={i} className="flex items-center gap-1.5">
            {i > 0 && <span className="text-gray-300 dark:text-gray-500">/</span>}
            {item.to && !isLast ? (
              <Link
                to={item.to}
                className="hover:text-gray-900 dark:hover:text-gray-100 transition-colors focus-visible:ring-2 focus-visible:ring-amber-500 focus-visible:outline-none rounded"
              >
                {item.label}
              </Link>
            ) : (
              <span className={isLast ? 'text-gray-900 dark:text-gray-100 font-medium' : ''}>
                {item.label}
              </span>
            )}
          </span>
        )
      })}
    </nav>
  )
}
