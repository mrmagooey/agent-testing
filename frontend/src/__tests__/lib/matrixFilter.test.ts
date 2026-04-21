import { describe, it, expect } from 'vitest'
import {
  parseMatrixFilter,
  serializeMatrixFilter,
  applyMatrixFilter,
  clearMatrixFilter,
  isEmpty,
  type MatrixFilter,
} from '../../lib/matrixFilter'
import type { Run } from '../../api/client'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    run_id: 'r1',
    experiment_id: 'exp1',
    model: 'gpt-4o',
    strategy: 'zero_shot',
    tool_variant: 'with_tools',
    profile: 'default',
    verification: 'none',
    status: 'completed',
    ...overrides,
  }
}

// ─── clearMatrixFilter / isEmpty ─────────────────────────────────────────────

describe('clearMatrixFilter', () => {
  it('returns an object with all empty arrays', () => {
    const f = clearMatrixFilter()
    expect(f).toEqual({ model: [], strategy: [], tool: [], ext: [], profile: [] })
  })
})

describe('isEmpty', () => {
  it('returns true for a fresh clear filter', () => {
    expect(isEmpty(clearMatrixFilter())).toBe(true)
  })

  it('returns false when any dimension has a value', () => {
    expect(isEmpty({ ...clearMatrixFilter(), model: ['gpt-4o'] })).toBe(false)
    expect(isEmpty({ ...clearMatrixFilter(), strategy: ['zero_shot'] })).toBe(false)
    expect(isEmpty({ ...clearMatrixFilter(), tool: ['with_tools'] })).toBe(false)
    expect(isEmpty({ ...clearMatrixFilter(), ext: ['lsp'] })).toBe(false)
    expect(isEmpty({ ...clearMatrixFilter(), profile: ['strict'] })).toBe(false)
  })
})

// ─── parseMatrixFilter ───────────────────────────────────────────────────────

describe('parseMatrixFilter', () => {
  it('returns empty filter for empty URLSearchParams', () => {
    expect(parseMatrixFilter(new URLSearchParams())).toEqual(clearMatrixFilter())
  })

  it('parses a single model value', () => {
    const p = new URLSearchParams('model=gpt-4o')
    expect(parseMatrixFilter(p).model).toEqual(['gpt-4o'])
  })

  it('splits comma-separated values', () => {
    const p = new URLSearchParams('model=gpt-4o,claude-3-5-sonnet-20241022')
    expect(parseMatrixFilter(p).model).toEqual(['gpt-4o', 'claude-3-5-sonnet-20241022'])
  })

  it('trims whitespace around values', () => {
    const p = new URLSearchParams('strategy=zero_shot , chain_of_thought')
    expect(parseMatrixFilter(p).strategy).toEqual(['zero_shot', 'chain_of_thought'])
  })

  it('drops empty string values after split', () => {
    const p = new URLSearchParams('tool=with_tools,,')
    expect(parseMatrixFilter(p).tool).toEqual(['with_tools'])
  })

  it('decodes URL-encoded values', () => {
    const p = new URLSearchParams('model=claude-3-5-sonnet-20241022')
    expect(parseMatrixFilter(p).model).toEqual(['claude-3-5-sonnet-20241022'])
  })

  it('omitted keys produce empty arrays', () => {
    const p = new URLSearchParams('model=gpt-4o')
    const f = parseMatrixFilter(p)
    expect(f.strategy).toEqual([])
    expect(f.tool).toEqual([])
    expect(f.ext).toEqual([])
    expect(f.profile).toEqual([])
  })

  it('parses all dimensions together', () => {
    const p = new URLSearchParams('model=gpt-4o&strategy=react&tool=tree-sitter&ext=rs,py&profile=deep')
    const f = parseMatrixFilter(p)
    expect(f.model).toEqual(['gpt-4o'])
    expect(f.strategy).toEqual(['react'])
    expect(f.tool).toEqual(['tree-sitter'])
    expect(f.ext).toEqual(['rs', 'py'])
    expect(f.profile).toEqual(['deep'])
  })
})

// ─── serializeMatrixFilter ───────────────────────────────────────────────────

describe('serializeMatrixFilter', () => {
  it('produces empty URLSearchParams for empty filter', () => {
    const p = serializeMatrixFilter(clearMatrixFilter())
    expect(p.toString()).toBe('')
  })

  it('emits comma-joined value for a single dimension', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), model: ['gpt-4o', 'claude-3-5-sonnet'] }
    const p = serializeMatrixFilter(f)
    const models = p.get('model')?.split(',').sort()
    expect(models).toEqual(['claude-3-5-sonnet', 'gpt-4o'])
  })

  it('sorts values within each key for stable URLs', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), model: ['z-model', 'a-model'] }
    const p = serializeMatrixFilter(f)
    expect(p.get('model')).toBe('a-model,z-model')
  })

  it('omits empty arrays', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), model: ['gpt-4o'] }
    const p = serializeMatrixFilter(f)
    expect(p.has('strategy')).toBe(false)
    expect(p.has('tool')).toBe(false)
    expect(p.has('ext')).toBe(false)
    expect(p.has('profile')).toBe(false)
  })

  it('round-trips through parse → serialize', () => {
    const original: MatrixFilter = {
      model: ['gpt-4o'],
      strategy: ['zero_shot'],
      tool: ['with_tools'],
      ext: ['lsp'],
      profile: ['default'],
    }
    const serialized = serializeMatrixFilter(original)
    const reparsed = parseMatrixFilter(serialized)
    expect(reparsed).toEqual(original)
  })
})

// ─── applyMatrixFilter ───────────────────────────────────────────────────────

describe('applyMatrixFilter', () => {
  const runs = [
    makeRun({ run_id: 'r1', model: 'gpt-4o', strategy: 'zero_shot', tool_variant: 'with_tools', profile: 'default', tool_extensions: ['lsp'] }),
    makeRun({ run_id: 'r2', model: 'gpt-4o', strategy: 'chain_of_thought', tool_variant: 'without_tools', profile: 'strict', tool_extensions: [] }),
    makeRun({ run_id: 'r3', model: 'claude-3-5-sonnet', strategy: 'zero_shot', tool_variant: 'with_tools', profile: 'default', tool_extensions: ['tree_sitter', 'lsp'] }),
    makeRun({ run_id: 'r4', model: 'claude-3-5-sonnet', strategy: 'chain_of_thought', tool_variant: 'without_tools', profile: 'strict', tool_extensions: undefined }),
  ]

  it('returns all runs when filter is empty', () => {
    expect(applyMatrixFilter(runs, clearMatrixFilter())).toHaveLength(4)
  })

  it('filters by model (OR within dimension)', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), model: ['gpt-4o'] }
    const result = applyMatrixFilter(runs, f)
    expect(result.map((r) => r.run_id)).toEqual(['r1', 'r2'])
  })

  it('filters by multiple models (OR within dimension)', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), model: ['gpt-4o', 'claude-3-5-sonnet'] }
    expect(applyMatrixFilter(runs, f)).toHaveLength(4)
  })

  it('filters by strategy', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), strategy: ['zero_shot'] }
    const result = applyMatrixFilter(runs, f)
    expect(result.map((r) => r.run_id)).toEqual(['r1', 'r3'])
  })

  it('filters by tool_variant', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), tool: ['without_tools'] }
    const result = applyMatrixFilter(runs, f)
    expect(result.map((r) => r.run_id)).toEqual(['r2', 'r4'])
  })

  it('filters by profile', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), profile: ['strict'] }
    const result = applyMatrixFilter(runs, f)
    expect(result.map((r) => r.run_id)).toEqual(['r2', 'r4'])
  })

  it('filters by ext using intersection (run must have at least one selected ext)', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), ext: ['lsp'] }
    const result = applyMatrixFilter(runs, f)
    // r1 has lsp, r3 has lsp; r2 has empty, r4 has undefined
    expect(result.map((r) => r.run_id)).toEqual(['r1', 'r3'])
  })

  it('ext filter: run matches if it has ANY of the selected extensions', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), ext: ['tree_sitter'] }
    const result = applyMatrixFilter(runs, f)
    expect(result.map((r) => r.run_id)).toEqual(['r3'])
  })

  it('runs with undefined tool_extensions are excluded when ext filter is active', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), ext: ['lsp'] }
    const result = applyMatrixFilter(runs, f)
    expect(result.find((r) => r.run_id === 'r4')).toBeUndefined()
  })

  it('applies AND across dimensions', () => {
    const f: MatrixFilter = {
      ...clearMatrixFilter(),
      model: ['gpt-4o'],
      strategy: ['zero_shot'],
    }
    const result = applyMatrixFilter(runs, f)
    expect(result.map((r) => r.run_id)).toEqual(['r1'])
  })

  it('returns empty array when no runs match', () => {
    const f: MatrixFilter = { ...clearMatrixFilter(), model: ['nonexistent-model'] }
    expect(applyMatrixFilter(runs, f)).toHaveLength(0)
  })

  it('returns all runs unchanged when filter is empty passthrough', () => {
    const result = applyMatrixFilter(runs, clearMatrixFilter())
    expect(result).toBe(runs)
  })
})
