import { useEffect, useRef } from 'react'
import { EditorView, lineNumbers, ViewPlugin } from '@codemirror/view'
import type { ViewUpdate } from '@codemirror/view'
import { EditorState, RangeSetBuilder } from '@codemirror/state'
import { Decoration } from '@codemirror/view'
import { javascript } from '@codemirror/lang-javascript'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { useTheme } from '../hooks/useTheme'

export interface Annotation {
  line: number
  className: string
  tooltip?: string
}

export interface CodeViewerProps {
  content: string
  language?: string
  readOnly?: boolean
  lineNumbers?: boolean
  annotations?: Annotation[]
  scrollToLine?: number
  maxHeight?: string
}

function getLanguageExtension(language?: string) {
  if (!language) return []
  const lang = language.toLowerCase()
  if (lang === 'python') return [python()]
  if (['javascript', 'js', 'typescript', 'ts', 'tsx', 'jsx'].includes(lang)) {
    return [javascript({ typescript: lang.includes('ts') || lang.includes('tsx') })]
  }
  return []
}

function buildAnnotationPlugin(annotations: Annotation[]) {
  const byLine = new Map<number, string[]>()
  for (const a of annotations) {
    const existing = byLine.get(a.line) ?? []
    existing.push(a.className)
    byLine.set(a.line, existing)
  }

  return ViewPlugin.fromClass(
    class {
      decorations

      constructor(view: EditorView) {
        this.decorations = this._build(view)
      }

      update(update: ViewUpdate) {
        if (update.docChanged || update.viewportChanged) {
          this.decorations = this._build(update.view)
        }
      }

      _build(view: EditorView) {
        const builder = new RangeSetBuilder<Decoration>()
        for (let i = 1; i <= view.state.doc.lines; i++) {
          const classes = byLine.get(i)
          if (classes) {
            const line = view.state.doc.line(i)
            builder.add(
              line.from,
              line.from,
              Decoration.line({ attributes: { class: classes.join(' ') } }),
            )
          }
        }
        return builder.finish()
      }
    },
    { decorations: (v) => v.decorations },
  )
}

export default function CodeViewer({
  content,
  language,
  readOnly = true,
  lineNumbers: showLineNumbers = true,
  annotations,
  scrollToLine,
  maxHeight = '400px',
}: CodeViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  const { isDark } = useTheme()

  useEffect(() => {
    if (!containerRef.current) return

    const extensions = [
      ...getLanguageExtension(language),
      ...(showLineNumbers ? [lineNumbers()] : []),
      ...(isDark ? [oneDark] : []),
      EditorView.editable.of(!readOnly),
      EditorView.lineWrapping,
      ...(annotations && annotations.length > 0 ? [buildAnnotationPlugin(annotations)] : []),
    ]

    const state = EditorState.create({
      doc: content,
      extensions,
    })

    const view = new EditorView({
      state,
      parent: containerRef.current,
    })

    viewRef.current = view

    if (scrollToLine != null && scrollToLine >= 1) {
      const lineCount = view.state.doc.lines
      const targetLine = Math.min(scrollToLine, lineCount)
      const pos = view.state.doc.line(targetLine).from
      view.dispatch({
        effects: EditorView.scrollIntoView(pos, { y: 'start', yMargin: 48 }),
      })
    }

    return () => {
      view.destroy()
      viewRef.current = null
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content, language, isDark, readOnly, showLineNumbers, annotations, scrollToLine])

  return (
    <div
      ref={containerRef}
      className="text-sm rounded border border-gray-200 dark:border-gray-700 overflow-auto font-mono"
      style={{ maxHeight }}
    />
  )
}
