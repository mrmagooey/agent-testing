import { describe, it, expect } from 'vitest'
import { generatePowerSet } from '../../pages/BatchNew'

describe('generatePowerSet', () => {
  it('generates empty set when given empty array', () => {
    const result = generatePowerSet([])
    expect(result).toEqual([[]])
  })

  it('generates power set for single element', () => {
    const result = generatePowerSet(['a'])
    expect(result).toEqual([[], ['a']])
  })

  it('generates power set for two elements', () => {
    const result = generatePowerSet(['a', 'b'])
    expect(result).toHaveLength(4)
    expect(result).toContainEqual([])
    expect(result).toContainEqual(['a'])
    expect(result).toContainEqual(['b'])
    expect(result).toContainEqual(['a', 'b'])
  })

  it('generates power set for three elements (2^3 = 8 subsets)', () => {
    const result = generatePowerSet(['lsp', 'tree_sitter', 'devdocs'])
    expect(result).toHaveLength(8)
    expect(result).toContainEqual([])
    expect(result).toContainEqual(['lsp'])
    expect(result).toContainEqual(['tree_sitter'])
    expect(result).toContainEqual(['devdocs'])
    expect(result).toContainEqual(['lsp', 'tree_sitter'])
    expect(result).toContainEqual(['lsp', 'devdocs'])
    expect(result).toContainEqual(['tree_sitter', 'devdocs'])
    expect(result).toContainEqual(['lsp', 'tree_sitter', 'devdocs'])
  })

  it('generates correct power set length for n elements', () => {
    const testCases = [
      { n: 1, expected: 2 },
      { n: 2, expected: 4 },
      { n: 3, expected: 8 },
      { n: 4, expected: 16 },
      { n: 5, expected: 32 },
    ]

    testCases.forEach(({ n, expected }) => {
      const items = Array.from({ length: n }, (_, i) => `item${i}`)
      const result = generatePowerSet(items)
      expect(result).toHaveLength(expected)
    })
  })

  it('preserves order of elements within subsets', () => {
    const result = generatePowerSet(['a', 'b', 'c'])
    // Check that 'a' always comes before 'b' if both are present
    const withBoth = result.filter((s) => s.includes('a') && s.includes('b'))
    withBoth.forEach((subset) => {
      const aIdx = subset.indexOf('a')
      const bIdx = subset.indexOf('b')
      expect(aIdx).toBeLessThan(bIdx)
    })
  })
})
