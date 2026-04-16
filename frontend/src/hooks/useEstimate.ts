import { useState, useEffect, useRef } from 'react'
import { estimateBatch, type CostEstimate, type BatchConfig } from '../api/client'

const DEBOUNCE_MS = 300

export function useEstimate(config: Partial<BatchConfig>): {
  estimate: CostEstimate | null
  loading: boolean
} {
  const [estimate, setEstimate] = useState<CostEstimate | null>(null)
  const [loading, setLoading] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    // Clear any pending debounce
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
    }

    // Don't estimate if nothing is configured
    const hasModels = config.models && config.models.length > 0
    const hasStrategies = config.strategies && config.strategies.length > 0
    if (!hasModels && !hasStrategies) {
      setLoading(false)
      return
    }

    setLoading(true)

    timerRef.current = setTimeout(async () => {
      try {
        const result = await estimateBatch(config)
        setEstimate(result)
      } catch {
        // Silently swallow — estimate is best-effort
        setEstimate(null)
      } finally {
        setLoading(false)
      }
    }, DEBOUNCE_MS)

    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current)
      }
    }
  // Stringify config to detect deep changes
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(config)])

  return { estimate, loading }
}
