import { useState, useEffect, useCallback } from 'react'
import { RefreshCwIcon, PlusIcon, PencilIcon, Trash2Icon, ZapIcon } from 'lucide-react'
import {
  listLlmProviders,
  createLlmProvider,
  patchLlmProvider,
  deleteLlmProvider,
  probeLlmProvider,
  getSettingsDefaults,
  patchSettingsDefaults,
  listModels,
  listToolExtensions,
  type ProviderDTO,
  type ProviderListResponse,
  type ProviderCreateRequest,
  type ProviderPatchRequest,
  type ProviderAdapter,
  type ProviderAuthType,
  type AppSettingsDefaults,
  type ToolExtension,
  type ModelProviderGroup,
  ApiError,
} from '../api/client'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'

// ─── Helpers ──────────────────────────────────────────────────────────────

function formatRelative(iso: string | null): string {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

const PROBE_PILL: Record<string, { cls: string; label: string }> = {
  fresh: { cls: 'bg-signal-success/15 text-signal-success border-signal-success/30', label: 'fresh' },
  stale: { cls: 'bg-signal-warning/15 text-signal-warning border-signal-warning/30', label: 'stale' },
  failed: { cls: 'bg-signal-danger/15 text-signal-danger border-signal-danger/30', label: 'failed' },
  disabled: { cls: 'bg-muted/50 text-muted-foreground border-border', label: 'disabled' },
}

function ProbeStatusPill({ status }: { status: ProviderDTO['last_probe_status'] }) {
  if (!status) {
    return (
      <span className="inline-flex items-center border rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider bg-muted/50 text-muted-foreground border-border">
        unknown
      </span>
    )
  }
  const { cls, label } = PROBE_PILL[status] ?? PROBE_PILL.disabled
  return (
    <span className={`inline-flex items-center border rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider ${cls}`}>
      {label}
    </span>
  )
}

function SectionHeader({ label }: { label: string }) {
  return (
    <h2 className="font-mono text-[11px] tracking-[0.2em] uppercase text-muted-foreground mb-3">
      // {label}
    </h2>
  )
}

function ErrorCard({ message }: { message: string }) {
  return (
    <div className="rounded-sm border border-signal-danger/40 bg-signal-danger/5 p-4 text-signal-danger font-mono text-sm">
      {message}
    </div>
  )
}

function LoadingSkeletons({ count = 2 }: { count?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className="h-24 w-full rounded-sm" />
      ))}
    </div>
  )
}

// ─── Slug validation ───────────────────────────────────────────────────────

const SLUG_RE = /^[a-z0-9][a-z0-9_-]*$/

function validateSlug(v: string): string | null {
  if (!v) return 'Required'
  if (!SLUG_RE.test(v)) return 'Slug: lowercase letters, digits, hyphens, underscores only'
  return null
}

// ─── Provider Form ────────────────────────────────────────────────────────

interface ProviderFormState {
  name: string
  display_name: string
  adapter: ProviderAdapter | ''
  model_id: string
  api_base: string
  auth_type: ProviderAuthType | ''
  api_key: string
  region: string
}

const EMPTY_FORM: ProviderFormState = {
  name: '',
  display_name: '',
  adapter: '',
  model_id: '',
  api_base: '',
  auth_type: '',
  api_key: '',
  region: '',
}

function formFromProvider(p: ProviderDTO): ProviderFormState {
  return {
    name: p.name,
    display_name: p.display_name,
    adapter: p.adapter,
    model_id: p.model_id,
    api_base: p.api_base ?? '',
    auth_type: p.auth_type,
    api_key: '',
    region: p.region ?? '',
  }
}

interface ProviderFormErrors {
  name?: string
  display_name?: string
  adapter?: string
  model_id?: string
  api_base?: string
  auth_type?: string
  api_key?: string
  region?: string
  _form?: string
}

function validateForm(form: ProviderFormState, isEdit: boolean): ProviderFormErrors {
  const errs: ProviderFormErrors = {}
  if (!isEdit) {
    const slugErr = validateSlug(form.name)
    if (slugErr) errs.name = slugErr
  }
  if (!form.display_name.trim()) errs.display_name = 'Required'
  if (!form.adapter) errs.adapter = 'Required'
  if (!form.model_id.trim()) errs.model_id = 'Required'
  if (!form.auth_type) errs.auth_type = 'Required'
  const needsBase = form.adapter === 'openai_compat' || form.adapter === 'litellm'
  if (needsBase && form.api_base) {
    try { new URL(form.api_base) } catch { errs.api_base = 'Must be a valid URL' }
  }
  return errs
}

interface ProviderFormProps {
  form: ProviderFormState
  errors: ProviderFormErrors
  isEdit: boolean
  onChange: (field: keyof ProviderFormState, value: string) => void
}

function ProviderFormFields({ form, errors, isEdit, onChange }: ProviderFormProps) {
  const needsBase = form.adapter === 'openai_compat' || form.adapter === 'litellm'

  return (
    <div className="space-y-4">
      {!isEdit && (
        <div>
          <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground block mb-1">
            Name (slug)
          </label>
          <Input
            value={form.name}
            onChange={(e) => onChange('name', e.target.value)}
            placeholder="my-provider"
            className="font-mono text-sm rounded-sm"
            aria-invalid={!!errors.name}
          />
          {errors.name && <p className="text-xs text-signal-danger mt-1 font-mono">{errors.name}</p>}
        </div>
      )}

      <div>
        <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground block mb-1">
          Display Name
        </label>
        <Input
          value={form.display_name}
          onChange={(e) => onChange('display_name', e.target.value)}
          placeholder="My Provider"
          className="rounded-sm text-sm"
          aria-invalid={!!errors.display_name}
        />
        {errors.display_name && <p className="text-xs text-signal-danger mt-1 font-mono">{errors.display_name}</p>}
      </div>

      <div>
        <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground block mb-1">
          Adapter
        </label>
        <Select value={form.adapter} onValueChange={(v) => onChange('adapter', v)}>
          <SelectTrigger className="w-full rounded-sm" aria-invalid={!!errors.adapter}>
            <SelectValue placeholder="Select adapter…" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="openai_compat">openai_compat</SelectItem>
            <SelectItem value="anthropic_compat">anthropic_compat</SelectItem>
            <SelectItem value="bedrock">bedrock</SelectItem>
            <SelectItem value="litellm">litellm</SelectItem>
          </SelectContent>
        </Select>
        {errors.adapter && <p className="text-xs text-signal-danger mt-1 font-mono">{errors.adapter}</p>}
      </div>

      <div>
        <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground block mb-1">
          Model ID
        </label>
        <Input
          value={form.model_id}
          onChange={(e) => onChange('model_id', e.target.value)}
          placeholder="gpt-4o"
          className="font-mono text-sm rounded-sm"
          aria-invalid={!!errors.model_id}
        />
        {errors.model_id && <p className="text-xs text-signal-danger mt-1 font-mono">{errors.model_id}</p>}
      </div>

      {needsBase && (
        <div>
          <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground block mb-1">
            API Base URL
          </label>
          <Input
            value={form.api_base}
            onChange={(e) => onChange('api_base', e.target.value)}
            placeholder="https://api.example.com/v1"
            className="font-mono text-sm rounded-sm"
            type="url"
            aria-invalid={!!errors.api_base}
          />
          {errors.api_base && <p className="text-xs text-signal-danger mt-1 font-mono">{errors.api_base}</p>}
        </div>
      )}

      <div>
        <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground block mb-1">
          Auth Type
        </label>
        <Select value={form.auth_type} onValueChange={(v) => onChange('auth_type', v)}>
          <SelectTrigger className="w-full rounded-sm" aria-invalid={!!errors.auth_type}>
            <SelectValue placeholder="Select auth type…" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="api_key">api_key</SelectItem>
            <SelectItem value="aws">aws</SelectItem>
            <SelectItem value="none">none</SelectItem>
          </SelectContent>
        </Select>
        {errors.auth_type && <p className="text-xs text-signal-danger mt-1 font-mono">{errors.auth_type}</p>}
      </div>

      {form.auth_type === 'api_key' && (
        <div>
          <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground block mb-1">
            API Key
          </label>
          <Input
            value={form.api_key}
            onChange={(e) => onChange('api_key', e.target.value)}
            type="password"
            placeholder={isEdit ? 'Leave blank to keep current key' : ''}
            className="font-mono text-sm rounded-sm"
            autoComplete="new-password"
          />
          {errors.api_key && <p className="text-xs text-signal-danger mt-1 font-mono">{errors.api_key}</p>}
        </div>
      )}

      {form.auth_type === 'aws' && (
        <div>
          <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground block mb-1">
            Region
          </label>
          <Input
            value={form.region}
            onChange={(e) => onChange('region', e.target.value)}
            placeholder="us-east-1"
            className="font-mono text-sm rounded-sm"
          />
        </div>
      )}
    </div>
  )
}

// ─── Add / Edit Modal ─────────────────────────────────────────────────────

interface ProviderModalProps {
  open: boolean
  editTarget: ProviderDTO | null
  onClose: () => void
  onSaved: () => void
}

function ProviderModal({ open, editTarget, onClose, onSaved }: ProviderModalProps) {
  const isEdit = editTarget !== null
  const [form, setForm] = useState<ProviderFormState>(EMPTY_FORM)
  const [errors, setErrors] = useState<ProviderFormErrors>({})
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (open) {
      setForm(editTarget ? formFromProvider(editTarget) : EMPTY_FORM)
      setErrors({})
    }
  }, [open, editTarget])

  const handleChange = (field: keyof ProviderFormState, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }))
    if (errors[field as keyof ProviderFormErrors]) {
      setErrors((prev) => ({ ...prev, [field]: undefined }))
    }
  }

  const handleSubmit = async () => {
    const errs = validateForm(form, isEdit)
    if (Object.keys(errs).length > 0) {
      setErrors(errs)
      return
    }

    setSubmitting(true)
    setErrors({})

    try {
      if (isEdit && editTarget) {
        const patch: ProviderPatchRequest = {
          display_name: form.display_name,
          adapter: form.adapter as ProviderAdapter,
          model_id: form.model_id,
          auth_type: form.auth_type as ProviderAuthType,
        }
        // Conditionally include optional fields
        if (form.adapter === 'openai_compat' || form.adapter === 'litellm') {
          patch.api_base = form.api_base || null
        }
        if (form.auth_type === 'api_key' && form.api_key) {
          patch.api_key = form.api_key
        }
        if (form.auth_type === 'aws') {
          patch.region = form.region || null
        }
        await patchLlmProvider(editTarget.id, patch)
      } else {
        const create: ProviderCreateRequest = {
          name: form.name,
          display_name: form.display_name,
          adapter: form.adapter as ProviderAdapter,
          model_id: form.model_id,
          auth_type: form.auth_type as ProviderAuthType,
        }
        if (form.api_base) create.api_base = form.api_base
        if (form.api_key) create.api_key = form.api_key
        if (form.region) create.region = form.region
        await createLlmProvider(create)
      }
      onSaved()
      onClose()
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setErrors({ name: "A provider with this name already exists" })
      } else if (err instanceof ApiError && err.status === 422) {
        const body = err.body as { detail?: Array<{ loc: string[]; msg: string }> } | null
        const fieldErrors: ProviderFormErrors = {}
        if (Array.isArray(body?.detail)) {
          for (const d of body.detail) {
            const fieldName = d.loc[d.loc.length - 1] as keyof ProviderFormErrors
            fieldErrors[fieldName] = d.msg
          }
        }
        if (Object.keys(fieldErrors).length === 0) {
          fieldErrors._form = (err as Error).message
        }
        setErrors(fieldErrors)
      } else {
        setErrors({ _form: (err as Error).message })
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <DialogContent className="sm:max-w-lg rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm uppercase tracking-wider">
            {isEdit ? `Edit — ${editTarget?.name}` : 'Add Custom Provider'}
          </DialogTitle>
        </DialogHeader>

        <div className="max-h-[60vh] overflow-y-auto pr-1">
          <ProviderFormFields
            form={form}
            errors={errors}
            isEdit={isEdit}
            onChange={handleChange}
          />
          {errors._form && (
            <p className="mt-3 text-xs text-signal-danger font-mono border border-signal-danger/30 px-3 py-2 rounded-sm">
              {errors._form}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="font-mono text-xs uppercase tracking-wider rounded-sm">
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={submitting}
            className="bg-primary text-primary-foreground hover:bg-primary/90 font-mono text-xs uppercase tracking-wider rounded-sm"
          >
            {submitting && (
              <span className="inline-block h-3 w-3 rounded-full border-2 border-current border-t-transparent animate-spin mr-2" />
            )}
            {isEdit ? 'Save Changes' : 'Add Provider'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ─── Delete Confirm Dialog ────────────────────────────────────────────────

interface DeleteConfirmProps {
  provider: ProviderDTO | null
  onClose: () => void
  onDeleted: () => void
}

function DeleteConfirmDialog({ provider, onClose, onDeleted }: DeleteConfirmProps) {
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleDelete = async () => {
    if (!provider) return
    setDeleting(true)
    setError(null)
    try {
      await deleteLlmProvider(provider.id)
      onDeleted()
      onClose()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Dialog open={provider !== null} onOpenChange={(v) => { if (!v) onClose() }}>
      <DialogContent className="sm:max-w-sm rounded-sm">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm uppercase tracking-wider text-signal-danger">
            Delete Provider
          </DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground font-mono">
          Delete provider <span className="text-foreground font-bold">{provider?.name}</span>? This cannot be undone.
        </p>
        {error && (
          <p className="text-xs text-signal-danger font-mono border border-signal-danger/30 px-3 py-2 rounded-sm">
            {error}
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="font-mono text-xs uppercase tracking-wider rounded-sm">
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleDelete}
            disabled={deleting}
            className="font-mono text-xs uppercase tracking-wider rounded-sm"
          >
            {deleting && (
              <span className="inline-block h-3 w-3 rounded-full border-2 border-current border-t-transparent animate-spin mr-2" />
            )}
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ─── Provider Card ────────────────────────────────────────────────────────

interface ProviderCardProps {
  provider: ProviderDTO
  onEdit?: () => void
  onDelete?: () => void
  onProbe?: () => void
  probing?: boolean
}

function ProviderCard({ provider, onEdit, onDelete, onProbe, probing }: ProviderCardProps) {
  const isCustom = provider.source === 'custom'

  return (
    <Card className="shadow-none rounded-sm border-border bg-card">
      <CardContent className="pt-5 pb-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              <span className="font-mono text-sm font-semibold text-foreground">{provider.display_name}</span>
              <span className="font-mono text-xs text-muted-foreground">({provider.name})</span>
              <ProbeStatusPill status={provider.last_probe_status} />
            </div>

            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs font-mono text-muted-foreground mt-2">
              <span>adapter: <span className="text-foreground">{provider.adapter}</span></span>
              <span>model: <span className="text-foreground">{provider.model_id}</span></span>
              {provider.api_base && (
                <span>base: <span className="text-foreground truncate max-w-[200px] inline-block align-bottom">{provider.api_base}</span></span>
              )}
              <span>auth: <span className="text-foreground">{provider.auth_type}</span></span>
              {provider.api_key_masked && (
                <span>key: <span className="text-foreground font-mono">{provider.api_key_masked}</span></span>
              )}
              {provider.last_probe_at && (
                <span>probed: <span className="text-foreground">{formatRelative(provider.last_probe_at)}</span></span>
              )}
            </div>

            {provider.last_probe_error && (
              <p className="mt-2 text-xs text-signal-danger font-mono truncate max-w-lg">
                {provider.last_probe_error}
              </p>
            )}

            {!isCustom && (
              <p className="mt-2 text-[10px] font-mono text-muted-foreground/60 uppercase tracking-wider">
                Managed by ops — edit via Helm
              </p>
            )}
          </div>

          {isCustom && (
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                onClick={onProbe}
                disabled={probing}
                title="Probe now"
                className="p-1.5 rounded-sm text-muted-foreground hover:text-foreground transition-colors focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none disabled:opacity-50"
              >
                <ZapIcon className={`h-3.5 w-3.5 ${probing ? 'animate-pulse' : ''}`} />
              </button>
              <button
                onClick={onEdit}
                title="Edit"
                className="p-1.5 rounded-sm text-muted-foreground hover:text-foreground transition-colors focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none"
              >
                <PencilIcon className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={onDelete}
                title="Delete"
                className="p-1.5 rounded-sm text-muted-foreground hover:text-signal-danger transition-colors focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none"
              >
                <Trash2Icon className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

// ─── Providers Panel ──────────────────────────────────────────────────────

function ProvidersPanel() {
  const [data, setData] = useState<ProviderListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<ProviderDTO | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<ProviderDTO | null>(null)
  const [probingIds, setProbingIds] = useState<Set<string>>(new Set())

  const fetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await listLlmProviders()
      setData(result)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetch() }, [fetch])

  const handleProbe = async (provider: ProviderDTO) => {
    setProbingIds((prev) => new Set(prev).add(provider.id))
    try {
      const updated = await probeLlmProvider(provider.id)
      setData((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          custom: prev.custom.map((p) => (p.id === updated.id ? updated : p)),
        }
      })
    } catch {
      // Probe error is shown on the card via last_probe_error; silently ignore here
    } finally {
      setProbingIds((prev) => {
        const next = new Set(prev)
        next.delete(provider.id)
        return next
      })
    }
  }

  if (loading) return <LoadingSkeletons count={3} />
  if (error) return <ErrorCard message={error} />

  const builtin = data?.builtin ?? []
  const custom = data?.custom ?? []

  return (
    <div className="space-y-10">
      {/* Built-in */}
      {builtin.length > 0 && (
        <section>
          <SectionHeader label="Built-in Providers" />
          <div className="space-y-3">
            {builtin.map((p) => (
              <ProviderCard key={p.id} provider={p} />
            ))}
          </div>
        </section>
      )}

      {/* Custom */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <SectionHeader label="Custom Providers" />
          <Button
            onClick={() => { setEditTarget(null); setModalOpen(true) }}
            className="bg-primary text-primary-foreground hover:bg-primary/90 font-mono text-xs uppercase tracking-wider rounded-sm h-7 px-3"
          >
            <PlusIcon className="h-3 w-3 mr-1" />
            Add Custom Provider
          </Button>
        </div>

        {custom.length === 0 ? (
          <div className="border border-dashed border-border rounded-sm px-6 py-8 text-center">
            <p className="text-sm text-muted-foreground mb-3">No custom providers yet.</p>
            <Button
              onClick={() => { setEditTarget(null); setModalOpen(true) }}
              variant="outline"
              className="font-mono text-xs uppercase tracking-wider rounded-sm"
            >
              <PlusIcon className="h-3 w-3 mr-1" />
              Add custom provider
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            {custom.map((p) => (
              <ProviderCard
                key={p.id}
                provider={p}
                onEdit={() => { setEditTarget(p); setModalOpen(true) }}
                onDelete={() => setDeleteTarget(p)}
                onProbe={() => handleProbe(p)}
                probing={probingIds.has(p.id)}
              />
            ))}
          </div>
        )}
      </section>

      <ProviderModal
        open={modalOpen}
        editTarget={editTarget}
        onClose={() => setModalOpen(false)}
        onSaved={fetch}
      />

      <DeleteConfirmDialog
        provider={deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onDeleted={fetch}
      />
    </div>
  )
}

// ─── Defaults Panel ───────────────────────────────────────────────────────

function DefaultsPanel() {
  const [saved, setSaved] = useState<AppSettingsDefaults | null>(null)
  const [form, setForm] = useState<AppSettingsDefaults | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [models, setModels] = useState<ModelProviderGroup[]>([])
  const [modelsLoading, setModelsLoading] = useState(false)

  useEffect(() => {
    getSettingsDefaults()
      .then((d) => {
        setSaved(d)
        setForm(d)
        setLoading(false)
      })
      .catch((err) => {
        setError((err as Error).message)
        setLoading(false)
      })
  }, [])

  // Fetch models lazily when evidence_assessor switches to llm_judge
  useEffect(() => {
    if (form?.evidence_assessor === 'llm_judge' && models.length === 0) {
      setModelsLoading(true)
      listModels()
        .then(setModels)
        .catch(() => {})
        .finally(() => setModelsLoading(false))
    }
  }, [form?.evidence_assessor, models.length])

  const isDirty = saved !== null && form !== null && (
    saved.allow_unavailable_models !== form.allow_unavailable_models ||
    saved.evidence_assessor !== form.evidence_assessor ||
    saved.evidence_judge_model !== form.evidence_judge_model
  )

  const handleSave = async () => {
    if (!form || !saved) return
    setSaving(true)
    setSaveError(null)

    // Only send changed fields
    const patch: Partial<AppSettingsDefaults> = {}
    if (form.allow_unavailable_models !== saved.allow_unavailable_models) {
      patch.allow_unavailable_models = form.allow_unavailable_models
    }
    if (form.evidence_assessor !== saved.evidence_assessor) {
      patch.evidence_assessor = form.evidence_assessor
    }
    if (form.evidence_judge_model !== saved.evidence_judge_model) {
      patch.evidence_judge_model = form.evidence_judge_model
    }

    try {
      const updated = await patchSettingsDefaults(patch)
      setSaved(updated)
      setForm(updated)
    } catch (err) {
      setSaveError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const allModelIds = models.flatMap((g) => g.models.map((m) => m.id))

  if (loading) return <LoadingSkeletons count={2} />
  if (error) return <ErrorCard message={error} />
  if (!form) return null

  return (
    <div className="space-y-8">
      <Card className="shadow-none rounded-sm border-border bg-card">
        <CardContent className="pt-6 space-y-6">
          {/* allow_unavailable_models */}
          <div className="flex items-center justify-between gap-8">
            <div>
              <p className="text-sm font-medium">Allow unavailable models</p>
              <p className="text-xs text-muted-foreground font-mono mt-0.5">
                Permit experiment submission even when selected models are not currently reachable.
              </p>
            </div>
            <button
              role="switch"
              aria-checked={form.allow_unavailable_models}
              onClick={() => setForm((prev) => prev ? { ...prev, allow_unavailable_models: !prev.allow_unavailable_models } : prev)}
              className={[
                'relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent',
                'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2',
                form.allow_unavailable_models ? 'bg-primary' : 'bg-muted',
              ].join(' ')}
            >
              <span
                className={[
                  'pointer-events-none inline-block h-4 w-4 rounded-full bg-background shadow-lg ring-0 transition-transform',
                  form.allow_unavailable_models ? 'translate-x-4' : 'translate-x-0',
                ].join(' ')}
              />
            </button>
          </div>

          <hr className="border-border" />

          {/* evidence_assessor */}
          <div>
            <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground block mb-2">
              Evidence Assessor
            </label>
            <Select
              value={form.evidence_assessor}
              onValueChange={(v) => setForm((prev) => prev ? { ...prev, evidence_assessor: v as AppSettingsDefaults['evidence_assessor'] } : prev)}
            >
              <SelectTrigger className="w-48 rounded-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="heuristic">heuristic</SelectItem>
                <SelectItem value="llm_judge">llm_judge</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* evidence_judge_model — only when llm_judge */}
          {form.evidence_assessor === 'llm_judge' && (
            <div>
              <label className="text-xs font-mono uppercase tracking-wider text-muted-foreground block mb-2">
                Evidence Judge Model
              </label>
              {modelsLoading ? (
                <Skeleton className="h-9 w-64 rounded-sm" />
              ) : (
                <Select
                  value={form.evidence_judge_model ?? ''}
                  onValueChange={(v) => setForm((prev) => prev ? { ...prev, evidence_judge_model: v || null } : prev)}
                >
                  <SelectTrigger className="w-64 rounded-sm">
                    <SelectValue placeholder="Select model…" />
                  </SelectTrigger>
                  <SelectContent>
                    {allModelIds.map((id) => (
                      <SelectItem key={id} value={id}>{id}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {saveError && <ErrorCard message={saveError} />}

      <div className="flex justify-end">
        <Button
          onClick={handleSave}
          disabled={!isDirty || saving}
          className="bg-primary text-primary-foreground hover:bg-primary/90 font-mono text-xs uppercase tracking-wider rounded-sm disabled:opacity-50"
        >
          {saving && (
            <span className="inline-block h-3 w-3 rounded-full border-2 border-current border-t-transparent animate-spin mr-2" />
          )}
          Save
        </Button>
      </div>
    </div>
  )
}

// ─── Tool Extensions Panel ────────────────────────────────────────────────

function ToolExtensionsPanel() {
  const [extensions, setExtensions] = useState<ToolExtension[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listToolExtensions()
      .then(setExtensions)
      .catch((err) => setError((err as Error).message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <LoadingSkeletons count={3} />
  if (error) return <ErrorCard message={error} />

  return (
    <div className="space-y-4">
      <p className="text-xs font-mono text-muted-foreground uppercase tracking-wider">
        Configured via Helm — see docs.
      </p>
      <div className="space-y-3">
        {extensions.map((ext) => (
          <Card key={ext.key} className="shadow-none rounded-sm border-border bg-card">
            <CardContent className="pt-4 pb-4">
              <div className="flex items-center justify-between">
                <div>
                  <span className="font-mono text-sm font-medium">{ext.label}</span>
                  <span className="ml-3 font-mono text-xs text-muted-foreground">{ext.key}</span>
                </div>
                <span className={[
                  'inline-flex items-center border rounded-full px-2 py-0.5 text-[10px] font-mono uppercase tracking-wider',
                  ext.available
                    ? 'bg-signal-success/15 text-signal-success border-signal-success/30'
                    : 'bg-muted/50 text-muted-foreground border-border',
                ].join(' ')}>
                  {ext.available ? 'available' : 'unavailable'}
                </span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}

// ─── Settings Page ────────────────────────────────────────────────────────

export default function Settings() {
  return (
    <div className="space-y-8">
      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="font-display font-bold text-3xl tracking-tight">Settings</h1>
          <p className="mt-1 text-xs text-muted-foreground font-mono uppercase tracking-wider">
            // LLM PROVIDERS · DEFAULTS · EXTENSIONS
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => window.location.reload()}
          className="font-mono text-xs uppercase tracking-wider rounded-sm"
        >
          <RefreshCwIcon className="h-3 w-3 mr-1.5" />
          Refresh
        </Button>
      </div>

      <Tabs defaultValue="providers">
        <TabsList className="rounded-sm">
          <TabsTrigger value="providers" className="font-mono text-xs uppercase tracking-wider rounded-sm">
            LLM Providers
          </TabsTrigger>
          <TabsTrigger value="defaults" className="font-mono text-xs uppercase tracking-wider rounded-sm">
            Experiment Defaults
          </TabsTrigger>
          <TabsTrigger value="extensions" className="font-mono text-xs uppercase tracking-wider rounded-sm">
            Tool Extensions
          </TabsTrigger>
        </TabsList>

        <TabsContent value="providers" className="mt-6">
          <ProvidersPanel />
        </TabsContent>

        <TabsContent value="defaults" className="mt-6">
          <DefaultsPanel />
        </TabsContent>

        <TabsContent value="extensions" className="mt-6">
          <ToolExtensionsPanel />
        </TabsContent>
      </Tabs>
    </div>
  )
}
