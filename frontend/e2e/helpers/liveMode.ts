export const isLive = process.env.E2E_LIVE === '1'

export const LIVE_MODEL_ID = 'openrouter/meta-llama/llama-3.1-8b-instruct'
export const LIVE_DATASET_NAME = 'live-e2e'
export const LIVE_DATASET_VERSION = '1.0.0'

export function uniqueBatchId(prefix: string): string {
  const rand = Math.random().toString(36).slice(2, 8)
  return `${prefix}-${Date.now()}-${rand}`
}
