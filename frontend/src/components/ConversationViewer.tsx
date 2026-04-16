import type { Message } from '../api/client'
import CodeViewer from './CodeViewer'

export interface ConversationViewerProps {
  messages: Message[]
}

const ROLE_STYLES: Record<string, string> = {
  user: 'border-l-4 border-blue-500 bg-blue-50 dark:bg-blue-950',
  assistant: 'border-l-4 border-green-500 bg-green-50 dark:bg-green-950',
  tool: 'border-l-4 border-gray-400 bg-gray-50 dark:bg-gray-900',
}

const ROLE_BADGE: Record<string, string> = {
  user: 'bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200',
  assistant: 'bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200',
  tool: 'bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300',
}

export default function ConversationViewer({ messages }: ConversationViewerProps) {
  if (messages.length === 0) {
    return (
      <p className="text-sm text-gray-400 dark:text-gray-500">No messages recorded.</p>
    )
  }

  return (
    <div className="space-y-3 max-h-[600px] overflow-y-auto pr-1">
      {messages.map((msg, i) => (
        <div key={i} className={`rounded-r-lg px-4 py-3 ${ROLE_STYLES[msg.role] ?? ROLE_STYLES.tool}`}>
          <div className="flex items-center justify-between mb-2">
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${ROLE_BADGE[msg.role] ?? ROLE_BADGE.tool}`}>
              {msg.role}
            </span>
            {msg.timestamp && (
              <span className="text-xs text-gray-400 dark:text-gray-500">
                {new Date(msg.timestamp).toLocaleTimeString()}
              </span>
            )}
          </div>
          <CodeViewer
            content={msg.content}
            language={msg.role === 'tool' ? 'json' : 'markdown'}
            maxHeight="300px"
            lineNumbers={false}
          />
        </div>
      ))}
    </div>
  )
}
