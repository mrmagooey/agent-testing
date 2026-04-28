import { Link, useLocation } from 'react-router-dom'

export default function NotFound() {
  const location = useLocation()

  return (
    <div className="max-w-screen-xl mx-auto px-4 py-12">
      <h1 className="text-2xl font-bold text-foreground mb-4">Page not found</h1>
      <p className="text-muted-foreground mb-6">
        The page at{' '}
        <code className="font-mono text-sm bg-muted px-1.5 py-0.5 rounded">
          {location.pathname}
        </code>{' '}
        doesn&apos;t exist. It may have been moved or never existed.
      </p>
      <div className="flex items-center gap-4">
        <Link
          to="/"
          className="text-primary hover:underline font-medium"
        >
          Back to dashboard
        </Link>
        <span className="text-muted-foreground/40" aria-hidden="true">│</span>
        <Link
          to="/findings"
          className="text-muted-foreground hover:text-foreground"
        >
          Findings
        </Link>
      </div>
    </div>
  )
}
