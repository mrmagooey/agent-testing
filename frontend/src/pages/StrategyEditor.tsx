import { useState, useEffect, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  getStrategy,
  createStrategy,
  validateStrategy,
  listToolExtensions,
  type UserStrategy,
  type OrchestrationShape,
  type StrategyBundleDefault,
  type StrategyBundleOverride,
  type OverrideRule,
  type ToolExtension,
} from '../api/client'
import { VULN_CLASSES, GLOB_PREVIEW_SAMPLE_FILES } from '../api/strategies'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs'

// ─── Constants ──────────────────────────────────────────────────────────────

const ORCHESTRATION_SHAPES: OrchestrationShape[] = [
  'single_agent',
  'per_file',
  'per_vuln_class',
  'sast_first',
  'diff_review',
]

const SHAPE_LABELS: Record<OrchestrationShape, string> = {
  single_agent: 'Single Agent',
  per_file: 'Per File',
  per_vuln_class: 'Per Vuln Class',
  sast_first: 'SAST First',
  diff_review: 'Diff Review',
}

const COMMON_TOOLS = [
  'read_file',
  'list_directory',
  'search_files',
  'run_command',
  'write_file',
]

const VERIFICATION_OPTIONS = ['none', 'with_verification']

const REQUIRED_PLACEHOLDERS = ['{repo_summary}', '{finding_output_format}']

function extractPlaceholders(template: string): string[] {
  const matches = template.match(/\{(\w+)\}/g) ?? []
  return [...new Set(matches)]
}

// Simple fnmatch-like glob matching for preview
function globMatches(pattern: string, path: string): boolean {
  try {
    // Convert glob to regex
    const regexStr = pattern
      .replace(/[.+^${}()|[\]\\]/g, (c) => (c === '*' || c === '?' ? c : `\\${c}`))
      .replace(/\*\*/g, '<<<DOUBLE_STAR>>>')
      .replace(/\*/g, '[^/]*')
      .replace(/<<<DOUBLE_STAR>>>/g, '.*')
      .replace(/\?/g, '[^/]')
    const regex = new RegExp(`^${regexStr}$`)
    return regex.test(path)
  } catch {
    return false
  }
}

// ─── Placeholder Linter ──────────────────────────────────────────────────────

function PlaceholderLinter({ template }: { template: string }) {
  const found = extractPlaceholders(template)
  const missing = REQUIRED_PLACEHOLDERS.filter((p) => !found.includes(p))
  const unknown = found.filter(
    (p) => !REQUIRED_PLACEHOLDERS.includes(p) && !['repo_summary', 'finding_output_format'].includes(p.slice(1, -1))
  )

  if (found.length === 0 && template.trim().length === 0) return null

  return (
    <div className="mt-1.5 text-xs space-y-1">
      {missing.map((p) => (
        <span
          key={p}
          className="inline-block mr-1.5 px-2 py-0.5 rounded bg-red-100 dark:bg-red-950 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-800"
        >
          missing: {p}
        </span>
      ))}
      {found
        .filter((p) => !missing.includes(p))
        .map((p) => (
          <span
            key={p}
            className="inline-block mr-1.5 px-2 py-0.5 rounded bg-green-100 dark:bg-green-950 text-green-700 dark:text-green-300 border border-green-200 dark:border-green-800"
          >
            {p}
          </span>
        ))}
    </div>
  )
}

// ─── GlobPreview ─────────────────────────────────────────────────────────────

function GlobPreview({ pattern }: { pattern: string }) {
  if (!pattern.trim()) return null
  const matches = GLOB_PREVIEW_SAMPLE_FILES.filter((f) => globMatches(pattern, f))
  return (
    <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
      Matches {matches.length} sample file{matches.length !== 1 ? 's' : ''}
      {matches.length > 0 && (
        <>: {matches.slice(0, 3).join(', ')}{matches.length > 3 ? `, +${matches.length - 3} more` : ''}</>
      )}
    </p>
  )
}

// ─── Bundle Field Editors ────────────────────────────────────────────────────

interface BundleFormProps {
  bundle: StrategyBundleDefault
  onChange: (b: StrategyBundleDefault) => void
  toolExtensions: ToolExtension[]
  showPlaceholderLinter?: boolean
}

function BundleDefaultForm({ bundle, onChange, toolExtensions, showPlaceholderLinter = true }: BundleFormProps) {
  const set = <K extends keyof StrategyBundleDefault>(key: K, val: StrategyBundleDefault[K]) =>
    onChange({ ...bundle, [key]: val })

  const toggleTool = (t: string) => {
    const next = bundle.tools.includes(t)
      ? bundle.tools.filter((x) => x !== t)
      : [...bundle.tools, t]
    set('tools', next)
  }

  const toggleExt = (k: string) => {
    const next = bundle.tool_extensions.includes(k)
      ? bundle.tool_extensions.filter((x) => x !== k)
      : [...bundle.tool_extensions, k]
    set('tool_extensions', next)
  }

  return (
    <div className="space-y-4">
      {/* Model ID */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Model ID</label>
        <input
          type="text"
          value={bundle.model_id}
          onChange={(e) => set('model_id', e.target.value)}
          className="w-full text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2"
          placeholder="e.g. claude-sonnet-4-5"
        />
      </div>

      {/* Verification */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Verification</label>
        <select
          value={bundle.verification}
          onChange={(e) => set('verification', e.target.value)}
          className="w-full text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2"
        >
          {VERIFICATION_OPTIONS.map((v) => (
            <option key={v} value={v}>{v}</option>
          ))}
        </select>
      </div>

      {/* Max Turns */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max Turns</label>
        <input
          type="number"
          min={1}
          max={200}
          value={bundle.max_turns}
          onChange={(e) => set('max_turns', parseInt(e.target.value, 10) || 1)}
          className="w-32 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2"
        />
      </div>

      {/* Tools */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Tools</label>
        <div className="flex flex-wrap gap-2">
          {COMMON_TOOLS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => toggleTool(t)}
              className={`px-2.5 py-1 rounded-full text-xs font-mono transition-colors border ${
                bundle.tools.includes(t)
                  ? 'bg-amber-100 dark:bg-amber-900 border-amber-400 dark:border-amber-600 text-amber-800 dark:text-amber-200'
                  : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {/* Tool Extensions */}
      {toolExtensions.length > 0 && (
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Tool Extensions</label>
          <div className="flex flex-wrap gap-3">
            {toolExtensions.map((te) => (
              <label key={te.key} className={`flex items-center gap-2 cursor-pointer ${te.available ? '' : 'opacity-50'}`}>
                <input
                  type="checkbox"
                  checked={bundle.tool_extensions.includes(te.key)}
                  onChange={() => toggleExt(te.key)}
                  disabled={!te.available}
                  className="rounded"
                />
                <span className="text-sm text-gray-700 dark:text-gray-300">{te.label}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Profile Modifier */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Profile Modifier</label>
        <textarea
          value={bundle.profile_modifier}
          onChange={(e) => set('profile_modifier', e.target.value)}
          rows={3}
          className="w-full text-sm font-mono rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 resize-y"
          placeholder="Optional profile modifier text…"
        />
      </div>

      {/* System Prompt */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">System Prompt</label>
        <textarea
          value={bundle.system_prompt}
          onChange={(e) => set('system_prompt', e.target.value)}
          rows={6}
          className="w-full text-sm font-mono rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 resize-y"
          placeholder="System prompt…"
        />
      </div>

      {/* User Prompt Template */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">User Prompt Template</label>
        <textarea
          value={bundle.user_prompt_template}
          onChange={(e) => set('user_prompt_template', e.target.value)}
          rows={6}
          className="w-full text-sm font-mono rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 resize-y"
          placeholder="User prompt template with {repo_summary} and {finding_output_format}…"
        />
        {showPlaceholderLinter && <PlaceholderLinter template={bundle.user_prompt_template} />}
      </div>
    </div>
  )
}

// ─── Override field editor (with inherit toggle) ──────────────────────────

interface OverrideFieldEditorProps {
  override: StrategyBundleOverride
  onChange: (o: StrategyBundleOverride) => void
  toolExtensions: ToolExtension[]
}

function OverrideFieldEditor({ override, onChange, toolExtensions }: OverrideFieldEditorProps) {
  const set = <K extends keyof StrategyBundleOverride>(key: K, val: StrategyBundleOverride[K]) =>
    onChange({ ...override, [key]: val })

  const clear = (key: keyof StrategyBundleOverride) =>
    onChange({ ...override, [key]: null })

  const isInheriting = (key: keyof StrategyBundleOverride) => override[key] == null

  function InheritToggle({ field }: { field: keyof StrategyBundleOverride }) {
    return (
      <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer">
        <input
          type="checkbox"
          checked={isInheriting(field)}
          onChange={(e) => {
            if (e.target.checked) {
              clear(field)
            } else {
              // Set a default empty value to start editing
              if (field === 'max_turns') set(field, 10)
              else if (field === 'tools' || field === 'tool_extensions') set(field as 'tools', [])
              else set(field as 'system_prompt', '')
            }
          }}
          className="rounded"
        />
        <span>Inherit from default</span>
      </label>
    )
  }

  return (
    <div className="space-y-4">
      {/* system_prompt */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-sm font-medium text-gray-700 dark:text-gray-300">System Prompt</label>
          <InheritToggle field="system_prompt" />
        </div>
        {!isInheriting('system_prompt') && (
          <textarea
            value={override.system_prompt ?? ''}
            onChange={(e) => set('system_prompt', e.target.value)}
            rows={4}
            className="w-full text-sm font-mono rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 resize-y"
          />
        )}
      </div>

      {/* user_prompt_template */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-sm font-medium text-gray-700 dark:text-gray-300">User Prompt Template</label>
          <InheritToggle field="user_prompt_template" />
        </div>
        {!isInheriting('user_prompt_template') && (
          <>
            <textarea
              value={override.user_prompt_template ?? ''}
              onChange={(e) => set('user_prompt_template', e.target.value)}
              rows={4}
              className="w-full text-sm font-mono rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2 resize-y"
            />
            <PlaceholderLinter template={override.user_prompt_template ?? ''} />
          </>
        )}
      </div>

      {/* model_id */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Model ID</label>
          <InheritToggle field="model_id" />
        </div>
        {!isInheriting('model_id') && (
          <input
            type="text"
            value={override.model_id ?? ''}
            onChange={(e) => set('model_id', e.target.value)}
            className="w-full text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2"
          />
        )}
      </div>

      {/* verification */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Verification</label>
          <InheritToggle field="verification" />
        </div>
        {!isInheriting('verification') && (
          <select
            value={override.verification ?? 'none'}
            onChange={(e) => set('verification', e.target.value)}
            className="w-full text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2"
          >
            {VERIFICATION_OPTIONS.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        )}
      </div>

      {/* max_turns */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Max Turns</label>
          <InheritToggle field="max_turns" />
        </div>
        {!isInheriting('max_turns') && (
          <input
            type="number"
            min={1}
            max={200}
            value={override.max_turns ?? 10}
            onChange={(e) => set('max_turns', parseInt(e.target.value, 10) || 1)}
            className="w-32 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2"
          />
        )}
      </div>
    </div>
  )
}

// ─── Ordered override rules list (per_file / sast_first) ─────────────────

interface RuleListProps {
  rules: OverrideRule[]
  onChange: (rules: OverrideRule[]) => void
  toolExtensions: ToolExtension[]
}

function RuleList({ rules, onChange, toolExtensions }: RuleListProps) {
  const addRule = () =>
    onChange([...rules, { key: '', override: {} }])

  const removeRule = (idx: number) =>
    onChange(rules.filter((_, i) => i !== idx))

  const moveUp = (idx: number) => {
    if (idx === 0) return
    const next = [...rules]
    ;[next[idx - 1], next[idx]] = [next[idx], next[idx - 1]]
    onChange(next)
  }

  const moveDown = (idx: number) => {
    if (idx === rules.length - 1) return
    const next = [...rules]
    ;[next[idx], next[idx + 1]] = [next[idx + 1], next[idx]]
    onChange(next)
  }

  const setKey = (idx: number, key: string) => {
    const next = [...rules]
    next[idx] = { ...next[idx], key }
    onChange(next)
  }

  const setOverride = (idx: number, override: StrategyBundleOverride) => {
    const next = [...rules]
    next[idx] = { ...next[idx], override }
    onChange(next)
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Rules are evaluated top-to-bottom. First match wins. Unmatched files use the default bundle.
      </p>
      {rules.map((rule, idx) => (
        <div
          key={idx}
          className="border border-gray-200 dark:border-gray-700 rounded-lg p-4"
          data-testid="override-rule"
        >
          <div className="flex items-center gap-2 mb-3">
            <span className="flex-shrink-0 w-6 h-6 flex items-center justify-center rounded-full bg-gray-100 dark:bg-gray-700 text-xs font-bold text-gray-600 dark:text-gray-300">
              {idx + 1}
            </span>
            <input
              type="text"
              value={rule.key}
              onChange={(e) => setKey(idx, e.target.value)}
              placeholder="Glob pattern, e.g. src/auth/**"
              className="flex-1 text-sm font-mono rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-1.5"
              data-testid="rule-key-input"
            />
            <div className="flex gap-1">
              <button
                type="button"
                onClick={() => moveUp(idx)}
                disabled={idx === 0}
                className="px-2 py-1 rounded border border-gray-200 dark:border-gray-700 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                aria-label="Move rule up"
                data-testid="move-up-btn"
              >
                ↑
              </button>
              <button
                type="button"
                onClick={() => moveDown(idx)}
                disabled={idx === rules.length - 1}
                className="px-2 py-1 rounded border border-gray-200 dark:border-gray-700 text-xs text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                aria-label="Move rule down"
                data-testid="move-down-btn"
              >
                ↓
              </button>
              <button
                type="button"
                onClick={() => removeRule(idx)}
                className="px-2 py-1 rounded border border-red-200 dark:border-red-800 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950 transition-colors"
                aria-label="Remove rule"
                data-testid="remove-rule-btn"
              >
                ✕
              </button>
            </div>
          </div>
          <GlobPreview pattern={rule.key} />
          <div className="mt-3">
            <OverrideFieldEditor
              override={rule.override}
              onChange={(o) => setOverride(idx, o)}
              toolExtensions={toolExtensions}
            />
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={addRule}
        className="w-full py-2 rounded-lg border border-dashed border-gray-300 dark:border-gray-600 text-sm text-gray-500 dark:text-gray-400 hover:border-amber-400 dark:hover:border-amber-600 hover:text-amber-600 dark:hover:text-amber-400 transition-colors"
        data-testid="add-rule-btn"
      >
        + Add rule
      </button>
    </div>
  )
}

// ─── Per-vuln-class tabbed overrides ─────────────────────────────────────

interface VulnClassOverridesProps {
  rules: OverrideRule[]
  onChange: (rules: OverrideRule[]) => void
  toolExtensions: ToolExtension[]
}

function VulnClassOverrides({ rules, onChange, toolExtensions }: VulnClassOverridesProps) {
  // Build a map for easy lookup
  const ruleMap = Object.fromEntries(rules.map((r) => [r.key, r.override]))

  const getOverride = (vc: string): StrategyBundleOverride =>
    ruleMap[vc] ?? {}

  const setOverride = (vc: string, override: StrategyBundleOverride) => {
    const existing = rules.findIndex((r) => r.key === vc)
    if (existing >= 0) {
      const next = [...rules]
      next[existing] = { key: vc, override }
      onChange(next)
    } else {
      onChange([...rules, { key: vc, override }])
    }
  }

  return (
    <Tabs defaultValue={VULN_CLASSES[0]}>
      <div className="overflow-x-auto pb-1">
        <TabsList className="flex-wrap h-auto gap-1 w-auto">
          {VULN_CLASSES.map((vc) => {
            const hasOverride = rules.some((r) => r.key === vc)
            return (
              <TabsTrigger key={vc} value={vc} className="text-xs">
                {vc}
                {hasOverride && (
                  <span className="ml-1 w-1.5 h-1.5 rounded-full bg-amber-500 inline-block" />
                )}
              </TabsTrigger>
            )
          })}
        </TabsList>
      </div>
      {VULN_CLASSES.map((vc) => (
        <TabsContent key={vc} value={vc} className="mt-4">
          <OverrideFieldEditor
            override={getOverride(vc)}
            onChange={(o) => setOverride(vc, o)}
            toolExtensions={toolExtensions}
          />
        </TabsContent>
      ))}
    </Tabs>
  )
}

// ─── Empty defaults ──────────────────────────────────────────────────────────

const emptyDefault: StrategyBundleDefault = {
  system_prompt: '',
  user_prompt_template: '',
  profile_modifier: '',
  model_id: '',
  tools: [],
  verification: 'none',
  max_turns: 10,
  tool_extensions: [],
}

// ─── Main Editor Component ───────────────────────────────────────────────────

export default function StrategyEditor() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const isFork = id != null

  const [loading, setLoading] = useState(isFork)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveErrors, setSaveErrors] = useState<string[]>([])

  // Form fields
  const [name, setName] = useState('')
  const [shape, setShape] = useState<OrchestrationShape>('single_agent')
  const [parentId, setParentId] = useState<string | null>(null)
  const [defaultBundle, setDefaultBundle] = useState<StrategyBundleDefault>(emptyDefault)
  const [overrides, setOverrides] = useState<OverrideRule[]>([])
  const [toolExtensions, setToolExtensions] = useState<ToolExtension[]>([])

  useEffect(() => {
    // Load tool extensions
    listToolExtensions().then(setToolExtensions).catch(() => {})

    // If fork mode, load parent strategy
    if (isFork && id) {
      setLoading(true)
      getStrategy(id)
        .then((parent: UserStrategy) => {
          setName(`Fork of ${parent.name}`)
          setShape(parent.orchestration_shape)
          setParentId(parent.id)
          setDefaultBundle({
            ...parent.default,
            tools: [...parent.default.tools],
            tool_extensions: [...parent.default.tool_extensions],
          })
          setOverrides(parent.overrides.map((r) => ({
            key: r.key,
            override: { ...r.override },
          })))
        })
        .catch((e: Error) => setLoadError(e.message))
        .finally(() => setLoading(false))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  // Clear overrides when shape changes to single_agent / diff_review
  const handleShapeChange = useCallback((newShape: OrchestrationShape) => {
    setShape(newShape)
    if (newShape === 'single_agent' || newShape === 'diff_review') {
      setOverrides([])
    }
  }, [])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setSaveErrors([])

    // Determine effective overrides (filter out empty keys for glob shapes)
    const effectiveOverrides: OverrideRule[] =
      shape === 'single_agent' || shape === 'diff_review'
        ? []
        : overrides.filter((r) => r.key.trim() !== '')

    const body = {
      parent_strategy_id: parentId,
      name: name.trim(),
      default: defaultBundle,
      overrides: effectiveOverrides,
      orchestration_shape: shape,
    }

    try {
      // Validate first using the endpoint (pass empty string id for new strategies)
      const validResult = await validateStrategy('__new__', {
        default: defaultBundle,
        overrides: effectiveOverrides,
        orchestration_shape: shape,
      }).catch(() => null) // Validation endpoint may not accept __new__, ignore errors

      if (validResult && !validResult.valid) {
        setSaveErrors(validResult.errors)
        setSaving(false)
        return
      }

      const created = await createStrategy(body)
      navigate(`/strategies/${encodeURIComponent(created.id)}`)
    } catch (err) {
      setSaveErrors([err instanceof Error ? err.message : 'Save failed'])
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading…</div>
  }

  if (loadError) {
    return (
      <div className="max-w-3xl mx-auto">
        <div className="p-4 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm">
          {loadError}
        </div>
      </div>
    )
  }

  const showOverrides = shape !== 'single_agent' && shape !== 'diff_review'

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-1">
          <button
            onClick={() => navigate('/strategies')}
            className="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
          >
            Strategies
          </button>
          <span className="text-gray-400">/</span>
          <span className="text-sm text-gray-700 dark:text-gray-300">
            {isFork ? `Fork ${id}` : 'New Strategy'}
          </span>
        </div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">
          {isFork ? 'Fork Strategy' : 'New Strategy'}
        </h1>
        {parentId && (
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            Parent: <span className="font-mono">{parentId}</span>
          </p>
        )}
      </div>

      {saveErrors.length > 0 && (
        <div className="mb-4 p-4 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800">
          <p className="text-sm font-medium text-red-700 dark:text-red-300 mb-2">Errors:</p>
          <ul className="list-disc list-inside space-y-1">
            {saveErrors.map((e, i) => (
              <li key={i} className="text-sm text-red-600 dark:text-red-400">{e}</li>
            ))}
          </ul>
        </div>
      )}

      <form onSubmit={handleSave}>
        <div className="space-y-6">
          {/* Metadata */}
          <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
            <h2 className="font-semibold text-gray-900 dark:text-gray-100 mb-4">Metadata</h2>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  className="w-full text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2"
                  placeholder="e.g. My SQLi Hunter"
                  data-testid="name-input"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Orchestration Shape</label>
                <select
                  value={shape}
                  onChange={(e) => handleShapeChange(e.target.value as OrchestrationShape)}
                  className="w-full text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-3 py-2"
                  data-testid="shape-select"
                >
                  {ORCHESTRATION_SHAPES.map((s) => (
                    <option key={s} value={s}>{SHAPE_LABELS[s]}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Default Bundle */}
          <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
            <h2 className="font-semibold text-gray-900 dark:text-gray-100 mb-4">Default Bundle</h2>
            <BundleDefaultForm
              bundle={defaultBundle}
              onChange={setDefaultBundle}
              toolExtensions={toolExtensions}
            />
          </div>

          {/* Overrides */}
          {showOverrides && (
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
              <h2 className="font-semibold text-gray-900 dark:text-gray-100 mb-4">
                Overrides
                <span className="ml-2 text-xs font-normal text-gray-400">
                  ({SHAPE_LABELS[shape]})
                </span>
              </h2>
              {shape === 'per_vuln_class' ? (
                <VulnClassOverrides
                  rules={overrides}
                  onChange={setOverrides}
                  toolExtensions={toolExtensions}
                />
              ) : (
                <RuleList
                  rules={overrides}
                  onChange={setOverrides}
                  toolExtensions={toolExtensions}
                />
              )}
            </div>
          )}

          {/* Save button */}
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={saving || !name.trim()}
              className="px-6 py-2.5 rounded-lg bg-amber-600 hover:bg-amber-700 text-white font-semibold text-sm transition-colors disabled:opacity-50"
              data-testid="save-btn"
            >
              {saving ? 'Saving…' : 'Save Strategy'}
            </button>
          </div>
        </div>
      </form>
    </div>
  )
}
