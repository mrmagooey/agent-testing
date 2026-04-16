import { useEffect, useRef } from 'react'
import { EditorView, lineNumbers } from '@codemirror/view'
import { EditorState } from '@codemirror/state'
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

export default function CodeViewer({
  content,
  language,
  readOnly = true,
  lineNumbers: showLineNumbers = true,
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

    return () => {
      view.destroy()
      viewRef.current = null
    }
  // Recreate editor when content, language, or theme changes
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content, language, isDark, readOnly, showLineNumbers])

  return (
    <div
      ref={containerRef}
      className="text-sm rounded border border-gray-200 dark:border-gray-700 overflow-auto font-mono"
      style={{ maxHeight }}
    />
  )
}
