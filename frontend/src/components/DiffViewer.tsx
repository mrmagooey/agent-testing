import CodeViewer from './CodeViewer'

export interface DiffViewerProps {
  before: string
  after: string
  language?: string
}

export default function DiffViewer({ before, after, language }: DiffViewerProps) {
  return (
    <div className="grid grid-cols-2 gap-0 rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
      <div className="border-r border-gray-200 dark:border-gray-700">
        <div className="px-4 py-2 bg-red-50 dark:bg-red-950 border-b border-gray-200 dark:border-gray-700">
          <span className="text-xs font-semibold text-red-700 dark:text-red-300">Before</span>
        </div>
        <CodeViewer content={before} language={language} maxHeight="500px" />
      </div>
      <div>
        <div className="px-4 py-2 bg-green-50 dark:bg-green-950 border-b border-gray-200 dark:border-gray-700">
          <span className="text-xs font-semibold text-green-700 dark:text-green-300">After</span>
        </div>
        <CodeViewer content={after} language={language} maxHeight="500px" />
      </div>
    </div>
  )
}
