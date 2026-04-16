import { useState, useRef, useCallback } from 'react'
import { searchFindings, type Finding } from '../api/client'

const DEBOUNCE_MS = 300

export function useSearch(batchId: string): {
  results: Finding[]
  loading: boolean
  search: (q: string) => void
} {
  const [results, setResults] = useState<Finding[]>([])
  const [loading, setLoading] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const search = useCallback(
    (q: string) => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
      }

      if (!q.trim()) {
        setResults([])
        setLoading(false)
        return
      }

      setLoading(true)

      timerRef.current = setTimeout(async () => {
        try {
          const data = await searchFindings(batchId, q)
          setResults(data)
        } catch {
          setResults([])
        } finally {
          setLoading(false)
        }
      }, DEBOUNCE_MS)
    },
    [batchId]
  )

  return { results, loading, search }
}
