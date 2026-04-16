import { useState, useEffect, useRef } from 'react'
import { getBatch, type Batch } from '../api/client'

const POLL_INTERVAL_MS = 10_000
const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])

export function useBatch(batchId: string | undefined): {
  batch: Batch | null
  loading: boolean
  error: string | null
} {
  const [batch, setBatch] = useState<Batch | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const clearPoll = () => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }

  const fetchBatch = async () => {
    if (!batchId) return
    try {
      const data = await getBatch(batchId)
      setBatch(data)
      setError(null)
      if (TERMINAL_STATUSES.has(data.status)) {
        clearPoll()
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch batch')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!batchId) {
      setLoading(false)
      return
    }

    setLoading(true)
    fetchBatch()

    intervalRef.current = setInterval(() => {
      // Stop polling if already in terminal state
      if (batch && TERMINAL_STATUSES.has(batch.status)) {
        clearPoll()
        return
      }
      fetchBatch()
    }, POLL_INTERVAL_MS)

    return () => {
      clearPoll()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId])

  return { batch, loading, error }
}
