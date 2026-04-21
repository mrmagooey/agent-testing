import { useState, useEffect, useRef } from 'react'
import { getExperiment, type Experiment } from '../api/client'

const POLL_INTERVAL_MS = 10_000
const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])

export function useExperiment(experimentId: string | undefined): {
  experiment: Experiment | null
  loading: boolean
  error: string | null
} {
  const [experiment, setExperiment] = useState<Experiment | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const clearPoll = () => {
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }

  const fetchExperiment = async () => {
    if (!experimentId) return
    try {
      const data = await getExperiment(experimentId)
      setExperiment(data)
      setError(null)
      if (TERMINAL_STATUSES.has(data.status)) {
        clearPoll()
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch experiment')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!experimentId) {
      setLoading(false)
      return
    }

    setLoading(true)
    fetchExperiment()

    intervalRef.current = setInterval(() => {
      // Stop polling if already in terminal state
      if (experiment && TERMINAL_STATUSES.has(experiment.status)) {
        clearPoll()
        return
      }
      fetchExperiment()
    }, POLL_INTERVAL_MS)

    return () => {
      clearPoll()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experimentId])

  return { experiment, loading, error }
}
