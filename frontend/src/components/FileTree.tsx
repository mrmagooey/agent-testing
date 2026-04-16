import { useState } from 'react'

export interface FileTreeProps {
  tree: Record<string, unknown>
  onSelect: (path: string) => void
  labelCounts?: Record<string, number>
  selectedPath?: string
}

interface NodeProps {
  name: string
  node: unknown
  path: string
  onSelect: (path: string) => void
  labelCounts: Record<string, number>
  selectedPath?: string
  depth: number
}

function TreeNode({ name, node, path, onSelect, labelCounts, selectedPath, depth }: NodeProps) {
  const isDir = node !== null && typeof node === 'object'
  const [expanded, setExpanded] = useState(depth === 0)

  const labelCount = labelCounts[path]

  if (isDir) {
    const children = node as Record<string, unknown>
    return (
      <div>
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1 w-full text-left px-2 py-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-sm text-gray-700 dark:text-gray-300"
          style={{ paddingLeft: `${depth * 12 + 8}px` }}
        >
          <span className="text-gray-400 text-xs w-3">{expanded ? '▼' : '▶'}</span>
          <span>📁</span>
          <span className="ml-1">{name}</span>
        </button>
        {expanded && (
          <div>
            {Object.entries(children)
              .sort(([, av], [, bv]) => {
                // directories first
                const aDir = av !== null && typeof av === 'object'
                const bDir = bv !== null && typeof bv === 'object'
                if (aDir && !bDir) return -1
                if (!aDir && bDir) return 1
                return 0
              })
              .map(([childName, childNode]) => (
                <TreeNode
                  key={childName}
                  name={childName}
                  node={childNode}
                  path={path ? `${path}/${childName}` : childName}
                  onSelect={onSelect}
                  labelCounts={labelCounts}
                  selectedPath={selectedPath}
                  depth={depth + 1}
                />
              ))}
          </div>
        )}
      </div>
    )
  }

  // File node
  const isSelected = selectedPath === path
  return (
    <button
      onClick={() => onSelect(path)}
      className={`flex items-center gap-1 w-full text-left px-2 py-0.5 rounded text-sm transition-colors ${
        isSelected
          ? 'bg-blue-100 dark:bg-blue-900 text-blue-900 dark:text-blue-100'
          : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
      }`}
      style={{ paddingLeft: `${depth * 12 + 8}px` }}
    >
      <span className="w-3" />
      <span>📄</span>
      <span className="ml-1 flex-1 truncate">{name}</span>
      {labelCount ? (
        <span className="ml-1 px-1 rounded text-xs bg-red-100 dark:bg-red-900 text-red-700 dark:text-red-300">
          {labelCount}
        </span>
      ) : null}
    </button>
  )
}

export default function FileTree({ tree, onSelect, labelCounts = {}, selectedPath }: FileTreeProps) {
  return (
    <div className="text-sm font-mono overflow-auto">
      {Object.entries(tree).map(([name, node]) => (
        <TreeNode
          key={name}
          name={name}
          node={node}
          path={name}
          onSelect={onSelect}
          labelCounts={labelCounts}
          selectedPath={selectedPath}
          depth={0}
        />
      ))}
    </div>
  )
}
