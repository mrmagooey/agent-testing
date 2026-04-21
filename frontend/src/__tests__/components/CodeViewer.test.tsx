import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

// ─── Mock CodeMirror ──────────────────────────────────────────────────────────
// CodeMirror cannot run in jsdom; intercept the EditorView constructor so we
// can assert on the extensions list without importing any CM internals.

const mockDispatch = vi.fn()
const mockDestroy = vi.fn()
let capturedExtensions: unknown[] = []

vi.mock('@codemirror/view', () => {
  const EditorView = vi.fn(({ state }: { state: { extensions: unknown[]; doc?: unknown } }) => {
    capturedExtensions = state?.extensions ?? []
    return {
      dispatch: mockDispatch,
      destroy: mockDestroy,
      state: {
        doc: {
          lines: 10,
          line: (_n: number) => ({ from: 0 }),
        },
      },
    }
  }) as unknown as {
    new (opts: unknown): {
      dispatch: typeof mockDispatch
      destroy: typeof mockDestroy
      state: { doc: { lines: number; line: (n: number) => { from: number } } }
    }
    editable: { of: (v: boolean) => unknown }
    lineWrapping: unknown
    scrollIntoView: (pos: number, opts?: unknown) => unknown
  }
  EditorView.editable = { of: vi.fn((v) => `editable:${v}`) }
  EditorView.lineWrapping = 'lineWrapping'
  EditorView.scrollIntoView = vi.fn(() => 'scrollEffect')

  const lineNumbers = vi.fn(() => 'lineNumbers')

  const ViewPlugin = {
    fromClass: vi.fn(() => 'annotationPlugin'),
  }

  return { EditorView, lineNumbers, ViewPlugin }
})

vi.mock('@codemirror/state', () => {
  const EditorState = {
    create: vi.fn(({ extensions }: { extensions: unknown[] }) => ({ extensions })),
  }
  const RangeSetBuilder = vi.fn(function RangeSetBuilder(this: { add: ReturnType<typeof vi.fn>; finish: ReturnType<typeof vi.fn> }) {
    this.add = vi.fn()
    this.finish = vi.fn(() => 'rangeSet')
  })
  return { EditorState, RangeSetBuilder }
})

vi.mock('@codemirror/lang-javascript', () => ({
  javascript: vi.fn(() => 'jsExtension'),
}))
vi.mock('@codemirror/lang-python', () => ({
  python: vi.fn(() => 'pyExtension'),
}))
vi.mock('@codemirror/theme-one-dark', () => ({
  oneDark: 'oneDarkTheme',
}))
vi.mock('../../hooks/useTheme', () => ({
  useTheme: vi.fn(() => ({ isDark: false })),
}))

import CodeViewer from '../../components/CodeViewer'
import type { Annotation } from '../../components/CodeViewer'
import { EditorView, ViewPlugin } from '@codemirror/view'
import { EditorState } from '@codemirror/state'

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
  capturedExtensions = []
})

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('CodeViewer', () => {
  it('renders a container div', () => {
    render(<CodeViewer content="hello" />)
    // The container div should be in the document.
    expect(document.querySelector('.font-mono')).toBeTruthy()
  })

  it('creates an EditorView with the provided content', () => {
    render(<CodeViewer content="print('hi')" language="python" />)
    const MockEditorView = EditorView as unknown as ReturnType<typeof vi.fn>
    expect(MockEditorView).toHaveBeenCalledOnce()
    expect(EditorState.create).toHaveBeenCalledWith(
      expect.objectContaining({ doc: "print('hi')" }),
    )
  })

  it('builds annotation plugin when annotations are provided', () => {
    const annotations: Annotation[] = [
      { line: 1, className: 'bg-amber-100' },
      { line: 3, className: 'bg-emerald-100' },
    ]
    render(<CodeViewer content="a\nb\nc" annotations={annotations} />)
    expect(ViewPlugin.fromClass).toHaveBeenCalledOnce()
  })

  it('does not build annotation plugin when annotations is empty', () => {
    render(<CodeViewer content="a\nb" annotations={[]} />)
    expect(ViewPlugin.fromClass).not.toHaveBeenCalled()
  })

  it('does not build annotation plugin when annotations is undefined', () => {
    render(<CodeViewer content="x" />)
    expect(ViewPlugin.fromClass).not.toHaveBeenCalled()
  })

  it('dispatches scrollIntoView effect when scrollToLine is provided', () => {
    render(<CodeViewer content="line1\nline2\nline3\n" scrollToLine={2} />)
    expect(EditorView.scrollIntoView).toHaveBeenCalled()
    expect(mockDispatch).toHaveBeenCalledWith(
      expect.objectContaining({ effects: 'scrollEffect' }),
    )
  })

  it('does not dispatch scrollIntoView when scrollToLine is undefined', () => {
    render(<CodeViewer content="line1\nline2\n" />)
    expect(mockDispatch).not.toHaveBeenCalled()
  })

  it('destroys editor on unmount', () => {
    const { unmount } = render(<CodeViewer content="x" />)
    unmount()
    expect(mockDestroy).toHaveBeenCalledOnce()
  })
})
