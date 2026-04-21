import type { Run } from '../api/client'

export interface MatrixFilter {
  model: string[]
  strategy: string[]
  tool: string[]
  ext: string[]
  profile: string[]
}

export function clearMatrixFilter(): MatrixFilter {
  return { model: [], strategy: [], tool: [], ext: [], profile: [] }
}

export function isEmpty(f: MatrixFilter): boolean {
  return (
    f.model.length === 0 &&
    f.strategy.length === 0 &&
    f.tool.length === 0 &&
    f.ext.length === 0 &&
    f.profile.length === 0
  )
}

function splitParam(raw: string | null): string[] {
  if (!raw) return []
  // URLSearchParams.get() already decodes percent-encoded characters; no need to decode again
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

export function parseMatrixFilter(params: URLSearchParams): MatrixFilter {
  return {
    model: splitParam(params.get('model')),
    strategy: splitParam(params.get('strategy')),
    tool: splitParam(params.get('tool')),
    ext: splitParam(params.get('ext')),
    profile: splitParam(params.get('profile')),
  }
}

export function serializeMatrixFilter(f: MatrixFilter): URLSearchParams {
  const p = new URLSearchParams()
  const keys: (keyof MatrixFilter)[] = ['ext', 'model', 'profile', 'strategy', 'tool']
  for (const key of keys) {
    const vals = f[key]
    if (vals.length > 0) {
      p.set(key, [...vals].sort().join(','))
    }
  }
  return p
}

export function applyMatrixFilter(runs: Run[], f: MatrixFilter): Run[] {
  if (isEmpty(f)) return runs
  return runs.filter((run) => {
    if (f.model.length > 0 && !f.model.includes(run.model)) return false
    if (f.strategy.length > 0 && !f.strategy.includes(run.strategy)) return false
    if (f.tool.length > 0 && !f.tool.includes(run.tool_variant)) return false
    if (f.ext.length > 0) {
      const runExts = run.tool_extensions ?? []
      // ext filter uses intersection: run must have at least one selected extension
      const hasMatch = f.ext.some((e) => runExts.includes(e))
      if (!hasMatch) return false
    }
    if (f.profile.length > 0 && !f.profile.includes(run.profile)) return false
    return true
  })
}
