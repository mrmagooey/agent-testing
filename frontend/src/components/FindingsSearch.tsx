import { useState, useRef } from 'react'
import { searchFindings, type Finding } from '../api/client'

export interface FindingsSearchProps {
  experimentId: string
  onResults: (findings: Finding[]) => void
}

const DEBOUNCE_MS = 300

export default function FindingsSearch({ experimentId, onResults }: FindingsSearchProps) {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleChange = (value: string) => {
    setQuery(value)

    if (timerRef.current) clearTimeout(timerRef.current)

    if (!value.trim()) {
      onResults([])
      setLoading(false)
      return
    }

    setLoading(true)
    timerRef.current = setTimeout(async () => {
      try {
        const results = await searchFindings(experimentId, value)
        onResults(results)
      } catch {
        onResults([])
      } finally {
        setLoading(false)
      }
    }, DEBOUNCE_MS)
  }

  const clear = () => {
    setQuery('')
    onResults([])
    setLoading(false)
    if (timerRef.current) clearTimeout(timerRef.current)
  }

  return (
    <div className="relative">
      <input
        type="text"
        value={query}
        onChange={(e) => handleChange(e.target.value)}
        placeholder="Search findings (title, description, recommendation)..."
        className="w-full pl-4 pr-10 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500"
      />
      <div className="absolute right-3 top-1/2 -translate-y-1/2">
        {loading ? (
          <svg className="animate-spin h-4 w-4 text-gray-400" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
        ) : query ? (
          <button
            onClick={clear}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg leading-none"
            aria-label="Clear search"
          >
            ×
          </button>
        ) : (
          <svg className="h-4 w-4 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        )}
      </div>
    </div>
  )
}
