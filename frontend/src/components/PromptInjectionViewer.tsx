import type { PromptSnapshot } from '../api/client'

interface PromptPaneProps {
  label: string
  content: string
  testId?: string
}

function PromptPane({ label, content, testId }: PromptPaneProps) {
  return (
    <div className="flex flex-col min-w-0" data-testid={testId}>
      <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-2 uppercase tracking-wide">
        {label}
      </p>
      <pre className="flex-1 text-xs font-mono text-gray-800 dark:text-gray-200 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg p-4 overflow-auto whitespace-pre-wrap break-words max-h-96">
        {content || <span className="italic text-gray-400 dark:text-gray-600">empty</span>}
      </pre>
    </div>
  )
}

type DiffLine = { type: 'common' | 'added' | 'removed'; text: string }

function lcs(a: string[], b: string[]): number[][] {
  const m = a.length
  const n = b.length
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1])
    }
  }
  return dp
}

function computeDiff(cleanText: string, injectedText: string): DiffLine[] {
  const a = cleanText.split('\n')
  const b = injectedText.split('\n')
  const dp = lcs(a, b)
  const result: DiffLine[] = []

  let i = a.length
  let j = b.length
  const ops: DiffLine[] = []

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      ops.push({ type: 'common', text: a[i - 1] })
      i--
      j--
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.push({ type: 'added', text: b[j - 1] })
      j--
    } else {
      ops.push({ type: 'removed', text: a[i - 1] })
      i--
    }
  }

  ops.reverse()
  result.push(...ops)
  return result
}

interface DiffPaneProps {
  label: string
  lines: DiffLine[]
  side: 'clean' | 'injected'
  testId?: string
}

function DiffPane({ label, lines, side, testId }: DiffPaneProps) {
  return (
    <div className="flex flex-col min-w-0" data-testid={testId}>
      <p className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-2 uppercase tracking-wide">
        {label}
      </p>
      <pre className="flex-1 text-xs font-mono bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg p-4 overflow-auto whitespace-pre-wrap break-words max-h-96">
        {lines.map((line, idx) => {
          if (line.type === 'common') {
            return (
              <span key={idx} className="text-gray-800 dark:text-gray-200 block">
                {line.text}
              </span>
            )
          }
          if (side === 'clean' && line.type === 'removed') {
            return (
              <span key={idx} className="block bg-red-100 dark:bg-red-950 text-red-800 dark:text-red-300">
                {line.text}
              </span>
            )
          }
          if (side === 'injected' && line.type === 'added') {
            return (
              <span key={idx} className="block bg-green-100 dark:bg-green-950 text-green-800 dark:text-green-300">
                {line.text}
              </span>
            )
          }
          return null
        })}
      </pre>
    </div>
  )
}

interface PromptInjectionViewerProps {
  promptSnapshot: PromptSnapshot
}

export default function PromptInjectionViewer({ promptSnapshot }: PromptInjectionViewerProps) {
  const { system_prompt, user_message_template, review_profile_modifier, clean_prompt, injected_prompt, injection_template_id } = promptSnapshot

  if (injected_prompt && clean_prompt) {
    const diffLines = computeDiff(clean_prompt, injected_prompt)
    return (
      <div className="space-y-4">
        {injection_template_id && (
          <div className="flex items-start gap-2 rounded-lg bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
            <span className="font-semibold shrink-0">Injection applied.</span>
            <span>Template: <code className="font-mono">{injection_template_id}</code>. Lines highlighted in red were removed; green lines were inserted.</span>
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4" data-testid="prompt-panes">
          <DiffPane label="Clean Prompt" lines={diffLines} side="clean" testId="clean-prompt-pane" />
          <DiffPane label="Injected Prompt" lines={diffLines} side="injected" testId="injected-prompt-pane" />
        </div>
      </div>
    )
  }

  const effectiveSystemPrompt = review_profile_modifier
    ? `${system_prompt}\n\n[Profile modifier injected]\n${review_profile_modifier}`
    : system_prompt

  return (
    <div className="space-y-4">
      {review_profile_modifier && (
        <div className="flex items-start gap-2 rounded-lg bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
          <span className="font-semibold shrink-0">Profile modifier active.</span>
          <span>The review profile has injected additional instructions into the system prompt (highlighted below).</span>
        </div>
      )}
      {clean_prompt !== undefined && clean_prompt !== null && !injected_prompt && (
        <p className="text-xs text-gray-500 dark:text-gray-400 italic">
          No injection applied for this run.
        </p>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4" data-testid="prompt-panes">
        <PromptPane
          label="System Prompt"
          content={effectiveSystemPrompt}
          testId="system-prompt-pane"
        />
        <PromptPane
          label="User Message Template"
          content={user_message_template}
          testId="user-message-pane"
        />
      </div>
    </div>
  )
}
