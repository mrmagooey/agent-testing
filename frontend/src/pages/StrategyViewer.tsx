import { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  getStrategy,
  deleteStrategy,
  type UserStrategy,
  type OrchestrationShape,
  type OverrideRule,
} from '../api/client'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '../components/ui/dialog'

const SHAPE_LABELS: Record<OrchestrationShape, string> = {
  single_agent: 'Single Agent',
  per_file: 'Per File',
  per_vuln_class: 'Per Vuln Class',
  sast_first: 'SAST First',
  diff_review: 'Diff Review',
}

function BundleField({ label, value }: { label: string; value: string | number | string[] }) {
  if (Array.isArray(value)) {
    return (
      <div>
        <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">{label}</p>
        <div className="flex flex-wrap gap-1">
          {value.length === 0 ? (
            <span className="text-xs text-gray-400 italic">none</span>
          ) : (
            value.map((v) => (
              <span
                key={v}
                className="inline-block px-2 py-0.5 rounded-full text-xs bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 font-mono"
              >
                {v}
              </span>
            ))
          )}
        </div>
      </div>
    )
  }
  if (typeof value === 'string' && (value.includes('\n') || value.length > 80)) {
    return (
      <div>
        <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">{label}</p>
        <pre className="text-xs font-mono bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded p-3 whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
          {value}
        </pre>
      </div>
    )
  }
  return (
    <div>
      <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">{label}</p>
      <p className="text-sm font-mono text-gray-800 dark:text-gray-200">{String(value)}</p>
    </div>
  )
}

function DefaultBlock({ strategy }: { strategy: UserStrategy }) {
  const d = strategy.default
  return (
    <div className="space-y-4">
      <BundleField label="Model" value={d.model_id} />
      <BundleField label="Verification" value={d.verification} />
      <BundleField label="Max Turns" value={d.max_turns} />
      <BundleField label="Tools" value={d.tools} />
      <BundleField label="Tool Extensions" value={d.tool_extensions} />
      {d.profile_modifier && <BundleField label="Profile Modifier" value={d.profile_modifier} />}
      <BundleField label="System Prompt" value={d.system_prompt} />
      <BundleField label="User Prompt Template" value={d.user_prompt_template} />
    </div>
  )
}

function OverrideFields({ rule, defaultBundle }: { rule: OverrideRule; defaultBundle: UserStrategy['default'] }) {
  const o = rule.override
  const overriddenFields = Object.entries(o).filter(([, v]) => v != null)
  const inheritedFields = [
    'system_prompt', 'user_prompt_template', 'profile_modifier',
    'model_id', 'tools', 'verification', 'max_turns', 'tool_extensions',
  ].filter((f) => o[f as keyof typeof o] == null)

  return (
    <div className="space-y-3">
      {overriddenFields.length === 0 ? (
        <p className="text-xs text-amber-600 dark:text-amber-400 italic">No overrides — all fields inherit from default.</p>
      ) : (
        overriddenFields.map(([field, val]) => (
          <BundleField
            key={field}
            label={field}
            value={val as string | number | string[]}
          />
        ))
      )}
      {inheritedFields.length > 0 && (
        <div>
          <p className="text-xs font-medium text-gray-400 dark:text-gray-500 mb-1">Inherits from default:</p>
          <div className="flex flex-wrap gap-1">
            {inheritedFields.map((f) => (
              <span
                key={f}
                className="inline-block px-2 py-0.5 rounded-full text-xs bg-amber-50 dark:bg-amber-950 text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-800"
              >
                {f}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function OverridesBlock({ strategy }: { strategy: UserStrategy }) {
  const shape = strategy.orchestration_shape

  if (shape === 'single_agent' || shape === 'diff_review') {
    return null
  }

  if (strategy.overrides.length === 0) {
    return (
      <div className="text-sm text-gray-400 italic">No overrides defined.</div>
    )
  }

  if (shape === 'per_vuln_class') {
    const defaultTab = strategy.overrides[0]?.key ?? ''
    return (
      <Tabs defaultValue={defaultTab}>
        <TabsList className="flex-wrap h-auto gap-1">
          {strategy.overrides.map((rule) => (
            <TabsTrigger key={rule.key} value={rule.key} className="text-xs">
              {rule.key}
            </TabsTrigger>
          ))}
        </TabsList>
        {strategy.overrides.map((rule) => (
          <TabsContent key={rule.key} value={rule.key} className="mt-4">
            <OverrideFields rule={rule} defaultBundle={strategy.default} />
          </TabsContent>
        ))}
      </Tabs>
    )
  }

  // per_file / sast_first — ordered numbered list, first-match-wins
  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-500 dark:text-gray-400">
        Rules are evaluated in order — first match wins. Unmatched files use the default bundle.
      </p>
      {strategy.overrides.map((rule, idx) => (
        <div
          key={idx}
          className="border border-gray-200 dark:border-gray-700 rounded-lg p-4"
        >
          <div className="flex items-center gap-2 mb-3">
            <span className="flex-shrink-0 w-6 h-6 flex items-center justify-center rounded-full bg-gray-100 dark:bg-gray-700 text-xs font-bold text-gray-600 dark:text-gray-300">
              {idx + 1}
            </span>
            <code className="text-sm font-mono bg-gray-50 dark:bg-gray-900 px-2 py-0.5 rounded border border-gray-200 dark:border-gray-700">
              {rule.key}
            </code>
          </div>
          <OverrideFields rule={rule} defaultBundle={strategy.default} />
        </div>
      ))}
    </div>
  )
}

export default function StrategyViewer() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [strategy, setStrategy] = useState<UserStrategy | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    getStrategy(id)
      .then(setStrategy)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  const handleDelete = async () => {
    if (!id) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteStrategy(id)
      navigate('/strategies')
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setDeleting(false)
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-gray-400">Loading…</div>
  }

  if (error || !strategy) {
    return (
      <div className="max-w-3xl mx-auto">
        <div className="p-4 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm">
          {error ?? 'Strategy not found.'}
        </div>
      </div>
    )
  }

  const showOverrides =
    strategy.orchestration_shape !== 'single_agent' &&
    strategy.orchestration_shape !== 'diff_review'

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <button
              onClick={() => navigate('/strategies')}
              className="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
            >
              Strategies
            </button>
            <span className="text-gray-400">/</span>
            <span className="text-sm text-gray-700 dark:text-gray-300 font-mono">{strategy.id}</span>
          </div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">{strategy.name}</h1>
          <div className="flex items-center gap-2 mt-2">
            <span className="font-mono text-xs bg-gray-100 dark:bg-gray-700 px-2 py-0.5 rounded text-gray-600 dark:text-gray-300">
              {strategy.orchestration_shape}
            </span>
            {strategy.is_builtin ? (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300">
                builtin
              </span>
            ) : (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300">
                user
              </span>
            )}
          </div>
          {strategy.parent_strategy_id && (
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
              Forked from:{' '}
              <span className="font-mono">{strategy.parent_strategy_id}</span>
            </p>
          )}
        </div>

        <div className="flex gap-2 flex-shrink-0">
          <button
            onClick={() => navigate(`/strategies/${encodeURIComponent(strategy.id)}/fork`)}
            className="px-4 py-2 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-sm font-semibold transition-colors"
            data-testid="fork-btn"
          >
            Fork
          </button>
          {!strategy.is_builtin && (
            <button
              onClick={() => setDeleteOpen(true)}
              className="px-4 py-2 rounded-lg border border-red-300 dark:border-red-700 text-red-700 dark:text-red-400 text-sm font-semibold hover:bg-red-50 dark:hover:bg-red-950 transition-colors"
              data-testid="delete-btn"
            >
              Delete
            </button>
          )}
        </div>
      </div>

      {/* Default Bundle */}
      <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5 mb-6">
        <h2 className="font-semibold text-gray-900 dark:text-gray-100 mb-4">Default Bundle</h2>
        <DefaultBlock strategy={strategy} />
      </div>

      {/* Overrides */}
      {showOverrides && (
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-5">
          <h2 className="font-semibold text-gray-900 dark:text-gray-100 mb-4">
            Overrides
            <span className="ml-2 text-xs font-normal text-gray-400">
              ({SHAPE_LABELS[strategy.orchestration_shape]})
            </span>
          </h2>
          <OverridesBlock strategy={strategy} />
        </div>
      )}

      {/* Delete dialog */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Strategy</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete "{strategy.name}"? This action cannot be undone.
              Strategies referenced by existing runs cannot be deleted.
            </DialogDescription>
          </DialogHeader>
          {deleteError && (
            <div className="p-3 rounded-lg bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm">
              {deleteError}
            </div>
          )}
          <DialogFooter showCloseButton>
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="px-4 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white text-sm font-semibold transition-colors disabled:opacity-50"
              data-testid="confirm-delete-btn"
            >
              {deleting ? 'Deleting…' : 'Delete'}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
