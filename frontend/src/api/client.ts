// Typed API client for the Security Review Framework coordinator

const BASE_URL = '/api'

// ─── Data Types ────────────────────────────────────────────────────────────

export interface Experiment {
  experiment_id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  dataset: string
  created_at: string
  completed_at?: string
  total_runs: number
  completed_runs: number
  running_runs: number
  pending_runs: number
  failed_runs: number
  total_cost_usd: number
  spend_cap_usd?: number
}

export interface Run {
  run_id: string
  experiment_id: string
  model: string
  strategy: string
  tool_variant: string
  tool_extensions?: string[]
  profile: string
  verification: string
  status: string
  precision?: number
  recall?: number
  f1?: number
  fpr?: number
  tp_count?: number
  fp_count?: number
  fn_count?: number
  cost_usd?: number
  duration_seconds?: number
  started_at?: string
  completed_at?: string
  error?: string | null
}

export interface Finding {
  finding_id: string
  run_id: string
  experiment_id: string
  title: string
  description: string
  vuln_class: string
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info'
  match_status: 'tp' | 'fp' | 'fn' | 'unlabeled_real'
  file_path?: string
  line_start?: number
  line_end?: number
  recommendation?: string
  evidence_quality?: 'strong' | 'adequate' | 'weak'
  matched_label_id?: string
}

export interface Dataset {
  name: string
  source: string
  label_count: number
  file_count: number
  size_bytes: number
  created_at: string
  languages: string[]
}

export interface Label {
  label_id: string
  dataset: string
  file_path: string
  line_start: number
  line_end: number
  vuln_class: string
  cwe?: string
  severity: string
  description: string
  source: string
}

export interface CostEstimate {
  total_runs: number
  estimated_cost_usd: number
  by_model: Record<string, number>
}

export interface CVECandidate {
  score: number
  cve_id: string
  vuln_class: string
  severity: string
  language: string
  repo: string
  files_changed: number
  lines_changed: number
  importable: boolean
  description?: string
  advisory_url?: string
  fix_commit?: string
}

export interface DiscoveryIssue {
  level: 'error' | 'warning' | 'info'
  message: string
  detail?: string | null
}

export interface DiscoveryStats {
  scanned: number
  resolved: number
  rejected: number
  returned: number
}

export interface DiscoverCVEsResponse {
  candidates: CVECandidate[]
  page: number
  page_size: number
  total: number
  stats: DiscoveryStats
  issues: DiscoveryIssue[]
}

export interface ExperimentConfig {
  dataset: string
  models: string[]
  strategies: string[]
  profiles: string[]
  tool_variants: string[]
  tool_extension_sets?: string[][]
  verification: string[]
  repetitions: number
  spend_cap_usd?: number
  allow_unavailable_models?: boolean
}

// ─── Model Availability Types ──────────────────────────────────────────────

export type ModelStatus = 'available' | 'key_missing' | 'not_listed' | 'probe_failed'
export type ProviderProbeStatus = 'fresh' | 'stale' | 'failed' | 'disabled'

export interface Model {
  id: string
  display_name: string | null
  status: ModelStatus
  context_length?: number | null
  region?: string | null
}

export interface ModelProviderGroup {
  provider: string
  probe_status: ProviderProbeStatus
  fetched_at: string | null
  last_error: string | null
  models: Model[]
}

// ─── Unavailable Models Error ──────────────────────────────────────────────

export interface UnavailableModelsError {
  error: 'unavailable_models'
  models: Array<{ id: string; status: ModelStatus; reason?: string }>
}

/**
 * Inspect a thrown error for the Phase 2 unavailable_models error shape.
 * Returns the structured payload when matched, or null otherwise.
 */
export function parseUnavailableModelsError(err: unknown): UnavailableModelsError | null {
  if (!(err instanceof ApiError)) return null
  const body = err.body as Record<string, unknown> | null | undefined
  if (!body) return null
  const detail = body.detail as Record<string, unknown> | null | undefined
  if (!detail || detail.error !== 'unavailable_models') return null
  return detail as unknown as UnavailableModelsError
}

export interface ToolCall {
  tool_name: string
  input: Record<string, unknown>
  timestamp: string
  flagged?: boolean
}

export interface Message {
  role: 'user' | 'assistant' | 'tool'
  content: string
  timestamp?: string
}

export type FileTree = Record<string, unknown>

export interface ComparisonRun extends Run {
  experiment_id: string
  experiment_name: string
  dataset: string
}

export interface RunComparison {
  run_a: ComparisonRun
  run_b: ComparisonRun
  found_by_both: Finding[]
  only_in_a: Finding[]
  only_in_b: Finding[]
  dataset_mismatch: boolean
  warnings: string[]
}

export interface FPPattern {
  model: string
  vuln_class: string
  pattern: string
  count: number
  suggested_action: string
}

export interface InjectionTemplate {
  template_id: string
  language: string
  cwe: string
  severity: string
  description: string
  vuln_class: string
  anchor_pattern: string
}

export interface AccuracyMatrixCell {
  model: string
  strategy: string
  accuracy: number
  run_count: number
}

export interface AccuracyMatrix {
  models: string[]
  strategies: string[]
  cells: AccuracyMatrixCell[]
}

export interface PromptSnapshot {
  system_prompt: string
  user_message_template: string
  review_profile_modifier?: string
  finding_output_format?: string
  clean_prompt?: string | null
  injected_prompt?: string | null
  injection_template_id?: string | null
}

// ─── Fetch Helper ──────────────────────────────────────────────────────────

// Extended Error that carries the parsed API response body for structured
// error handling (e.g. parseUnavailableModelsError).
export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(message: string, status: number, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    let message = `API error ${res.status}`
    let body: unknown
    try {
      body = await res.json()
      const b = body as Record<string, unknown>
      const detail = b.detail ?? b.message
      if (typeof detail === 'string') {
        message = detail
      }
    } catch {
      // ignore parse failure
    }
    throw new ApiError(message, res.status, body)
  }
  // 204 No Content
  if (res.status === 204) return undefined as unknown as T
  return res.json() as Promise<T>
}

// ─── Tool Extensions ──────────────────────────────────────────────────────

export interface ToolExtension {
  key: string
  label: string
  available: boolean
}

/**
 * Fetch available tool extensions from the backend.
 * Falls back to all three extensions as available if the route returns 404
 * (graceful degradation for older coordinators without MCP support).
 */
export async function listToolExtensions(): Promise<ToolExtension[]> {
  try {
    return await apiFetch<ToolExtension[]>('/tool-extensions')
  } catch (err) {
    // If 404 or other error, assume all three are available (fallback for legacy)
    if ((err as Error).message.includes('404') || (err as Error).message.includes('not found')) {
      return [
        { key: 'tree_sitter', label: 'Tree-sitter', available: true },
        { key: 'lsp', label: 'LSP', available: true },
        { key: 'devdocs', label: 'DevDocs', available: true },
      ]
    }
    // For other errors, still provide the fallback
    return [
      { key: 'tree_sitter', label: 'Tree-sitter', available: true },
      { key: 'lsp', label: 'LSP', available: true },
      { key: 'devdocs', label: 'DevDocs', available: true },
    ]
  }
}

// ─── Experiment Endpoints ──────────────────────────────────────────────────

export function submitExperiment(config: ExperimentConfig): Promise<Experiment> {
  return apiFetch<Experiment>('/experiments', { method: 'POST', body: JSON.stringify(config) })
}

export function listExperiments(): Promise<Experiment[]> {
  return apiFetch<Experiment[]>('/experiments')
}

export function getExperiment(experimentId: string): Promise<Experiment> {
  return apiFetch<Experiment>(`/experiments/${experimentId}`)
}

// ─── Benchmark Scorecard Types ─────────────────────────────────────────────

export interface CweRow {
  cwe_id: string
  tp: number
  fp: number
  tn: number
  fn: number
  precision: number | null
  recall: number | null
  f1: number | null
  fp_rate: number | null
  owasp_score: number | null
  warning: string | null
}

export type AggregateRow = Omit<CweRow, 'cwe_id'>

export interface BenchmarkScorecard {
  dataset_name: string
  per_cwe: CweRow[]
  aggregate: AggregateRow
}

export interface ExperimentResults {
  runs: Run[]
  findings: Finding[]
  benchmark_scorecards?: BenchmarkScorecard[]
}

export function getExperimentResults(experimentId: string): Promise<ExperimentResults> {
  return apiFetch(`/experiments/${experimentId}/results`)
}

export function listRuns(experimentId: string): Promise<Run[]> {
  return apiFetch<Run[]>(`/experiments/${experimentId}/runs`)
}

export function getRun(
  experimentId: string,
  runId: string
): Promise<Run & { findings: Finding[]; tool_calls: ToolCall[]; messages: Message[]; prompt_snapshot?: PromptSnapshot }> {
  return apiFetch(`/experiments/${experimentId}/runs/${runId}`)
}

export function getAccuracyMatrix(): Promise<AccuracyMatrix> {
  return apiFetch<AccuracyMatrix>('/matrix/accuracy')
}

export function cancelExperiment(experimentId: string): Promise<void> {
  return apiFetch<void>(`/experiments/${experimentId}/cancel`, { method: 'POST' })
}

/** Returns the download URL (not a fetch — open in browser directly) */
export function downloadReports(experimentId: string): string {
  return `${BASE_URL}/experiments/${experimentId}/results/download`
}

export function compareRuns(
  experimentId: string,
  runAId: string,
  runBId: string
): Promise<RunComparison> {
  return compareRunsCross({
    aExperiment: experimentId,
    aRun: runAId,
    bExperiment: experimentId,
    bRun: runBId,
  })
}

export function compareRunsCross({
  aExperiment,
  aRun,
  bExperiment,
  bRun,
}: {
  aExperiment: string
  aRun: string
  bExperiment: string
  bRun: string
}): Promise<RunComparison> {
  const params = new URLSearchParams({
    a_experiment: aExperiment,
    a_run: aRun,
    b_experiment: bExperiment,
    b_run: bRun,
  })
  return apiFetch<RunComparison>(`/compare-runs?${params}`)
}

export function searchFindings(experimentId: string, q: string): Promise<Finding[]> {
  return apiFetch<Finding[]>(
    `/experiments/${experimentId}/findings/search?q=${encodeURIComponent(q)}`
  )
}

export function reclassifyFinding(
  experimentId: string,
  runId: string,
  findingId: string,
  newStatus: string,
  note: string
): Promise<void> {
  return apiFetch<void>(`/experiments/${experimentId}/runs/${runId}/reclassify`, {
    method: 'POST',
    body: JSON.stringify({ finding_id: findingId, new_status: newStatus, note }),
  })
}

export function toolAudit(experimentId: string, runId: string): Promise<ToolCall[]> {
  return apiFetch<ToolCall[]>(`/experiments/${experimentId}/runs/${runId}/tool-audit`)
}

export function compareExperiments(
  experimentAId: string,
  experimentBId: string
): Promise<{
  metric_deltas: Record<string, unknown>[]
  fp_patterns: FPPattern[]
  stability: Record<string, unknown>
}> {
  return apiFetch(`/experiments/compare?a=${encodeURIComponent(experimentAId)}&b=${encodeURIComponent(experimentBId)}`)
}

export function getFPPatterns(experimentId: string): Promise<FPPattern[]> {
  return apiFetch<FPPattern[]>(`/experiments/${experimentId}/fp-patterns`)
}

export function estimateExperiment(config: Partial<ExperimentConfig>): Promise<CostEstimate> {
  return apiFetch<CostEstimate>('/experiments/estimate', {
    method: 'POST',
    body: JSON.stringify(config),
  })
}

// ─── Dataset Endpoints ─────────────────────────────────────────────────────

export async function listDatasets(): Promise<Dataset[]> {
  // Coordinator may omit `languages`; default it so .join() in the UI is safe.
  const raw = await apiFetch<Partial<Dataset>[]>('/datasets')
  return raw.map((d) => ({
    name: d.name ?? '',
    source: d.source ?? '',
    label_count: d.label_count ?? 0,
    file_count: d.file_count ?? 0,
    size_bytes: d.size_bytes ?? 0,
    created_at: d.created_at ?? '',
    languages: d.languages ?? [],
  }))
}

export function discoverCVEs(criteria: Record<string, unknown>): Promise<DiscoverCVEsResponse> {
  return apiFetch<DiscoverCVEsResponse>('/datasets/discover-cves', {
    method: 'POST',
    body: JSON.stringify(criteria),
  })
}

export function resolveCVE(cveId: string): Promise<CVECandidate> {
  return apiFetch<CVECandidate>(`/datasets/resolve-cve?id=${encodeURIComponent(cveId)}`)
}

export function importCVE(cveId: string, datasetName?: string): Promise<Dataset> {
  return apiFetch<Dataset>('/datasets/import-cve', {
    method: 'POST',
    body: JSON.stringify({ cve_id: cveId, dataset_name: datasetName }),
  })
}

export function previewInjection(
  datasetName: string,
  templateId: string,
  filePath: string,
  substitutions: Record<string, string>
): Promise<{ before: string; after: string; language: string; label: Label; warnings: string[] }> {
  return apiFetch(`/datasets/${encodeURIComponent(datasetName)}/inject/preview`, {
    method: 'POST',
    body: JSON.stringify({ template_id: templateId, file_path: filePath, substitutions }),
  })
}

export function injectVuln(
  datasetName: string,
  templateId: string,
  filePath: string,
  substitutions: Record<string, string>
): Promise<{ label_id: string }> {
  return apiFetch(`/datasets/${encodeURIComponent(datasetName)}/inject`, {
    method: 'POST',
    body: JSON.stringify({ template_id: templateId, file_path: filePath, substitutions }),
  })
}

export function getLabels(
  datasetName: string,
  filters?: { cwe?: string; severity?: string; source?: string },
): Promise<Label[]> {
  const qs = new URLSearchParams()
  if (filters?.cwe) qs.set('cwe', filters.cwe)
  if (filters?.severity) qs.set('severity', filters.severity)
  if (filters?.source) qs.set('source', filters.source)
  const query = qs.toString()
  return apiFetch<Label[]>(
    `/datasets/${encodeURIComponent(datasetName)}/labels${query ? `?${query}` : ''}`,
  )
}

// ─── Dataset Detail & Rematerialization ───────────────────────────────────

export interface DatasetRow {
  name: string
  kind: 'git' | 'derived' | 'archive'
  origin_url: string | null
  origin_commit: string | null
  origin_ref: string | null
  cve_id: string | null
  base_dataset: string | null
  recipe_json: string | null
  metadata: Record<string, unknown>
  created_at: string
  materialized_at: string | null
  archive_url?: string | null
  archive_sha256?: string | null
  archive_format?: string | null
}

export function getDataset(name: string): Promise<DatasetRow> {
  return apiFetch<DatasetRow>(`/datasets/${encodeURIComponent(name)}`)
}

export function rematerializeDataset(name: string): Promise<{ materialized_at: string }> {
  return apiFetch<{ materialized_at: string }>(
    `/datasets/${encodeURIComponent(name)}/rematerialize`,
    { method: 'POST' },
  )
}

export function getFileTree(datasetName: string): Promise<FileTree> {
  return apiFetch<FileTree>(`/datasets/${encodeURIComponent(datasetName)}/tree`)
}

export function getFileContent(
  datasetName: string,
  filePath: string,
  options?: { start?: number; end?: number }
): Promise<{
  content: string
  language: string
  line_count?: number
  size_bytes?: number
  labels?: Label[]
  binary?: boolean
  truncated?: boolean
  highlight_start?: number
  highlight_end?: number
}> {
  const params = new URLSearchParams({ path: filePath })
  if (options?.start != null) params.set('start', String(options.start))
  if (options?.end != null) params.set('end', String(options.end))
  return apiFetch(
    `/datasets/${encodeURIComponent(datasetName)}/file?${params}`
  )
}

// ─── Config Endpoints ──────────────────────────────────────────────────────

// The coordinator's /models, /strategies, /profiles endpoints return
// list[dict] (e.g. {"id": "gpt-4o", ...} or {"name": "default", ...}).
// The UI only needs the identifier string; rendering the raw object triggers
// React error #31. Normalize here so every caller sees a plain string[].
// Legacy list[str] responses pass through unchanged.
function normalizeConfigItem(item: unknown): string | null {
  if (typeof item === 'string') return item
  if (item && typeof item === 'object') {
    const obj = item as Record<string, unknown>
    const id = obj.id ?? obj.name
    if (typeof id === 'string') return id
  }
  return null
}

async function fetchConfigList(path: string): Promise<string[]> {
  const raw = await apiFetch<unknown[]>(path)
  return raw.map(normalizeConfigItem).filter((s): s is string => s !== null)
}

/**
 * Fetch the grouped-by-provider model availability list (Phase 2 shape).
 */
export function listModels(): Promise<ModelProviderGroup[]> {
  return apiFetch<ModelProviderGroup[]>('/models')
}

/**
 * Legacy helper: returns the flat list of IDs for models whose status is
 * 'available'. Use this wherever the old Promise<string[]> return was consumed
 * and the caller only needs usable model IDs.
 */
export async function listAvailableModelIds(): Promise<string[]> {
  const groups = await listModels()
  return groups.flatMap((g) => g.models.filter((m) => m.status === 'available').map((m) => m.id))
}

/**
 * Legacy helper: returns strategy names as plain strings for old axis-based
 * experiment configuration. New code should use listStrategiesFull().
 */
export function listStrategies(): Promise<string[]> {
  return fetchConfigList('/strategies')
}

// ─── Strategy Endpoints ────────────────────────────────────────────────────

export type {
  OrchestrationShape,
  DispatchFallback,
  OutputTypeName,
  StrategySummary,
  UserStrategy,
  StrategyBundleDefault,
  StrategyBundleOverride,
  OverrideRule,
  StrategyCreateBody,
  StrategyValidateBody,
  StrategyValidateResult,
} from './strategies'

import type {
  StrategySummary,
  UserStrategy,
  StrategyCreateBody,
  StrategyValidateBody,
  StrategyValidateResult,
} from './strategies'

export function listStrategiesFull(): Promise<StrategySummary[]> {
  return apiFetch<StrategySummary[]>('/strategies')
}

export function getStrategy(id: string): Promise<UserStrategy> {
  return apiFetch<UserStrategy>(`/strategies/${encodeURIComponent(id)}`)
}

export function createStrategy(body: StrategyCreateBody): Promise<UserStrategy> {
  return apiFetch<UserStrategy>('/strategies', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function validateStrategy(
  id: string,
  body: StrategyValidateBody
): Promise<StrategyValidateResult> {
  return apiFetch<StrategyValidateResult>(`/strategies/${encodeURIComponent(id)}/validate`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function deleteStrategy(id: string): Promise<void> {
  return apiFetch<void>(`/strategies/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function listProfiles(): Promise<string[]> {
  return fetchConfigList('/profiles')
}

export function listTemplates(): Promise<InjectionTemplate[]> {
  return apiFetch<InjectionTemplate[]>('/templates')
}

// ─── Global findings search ────────────────────────────────────────────────

export interface GlobalFinding extends Finding {
  experiment_name: string
  model_id: string
  strategy: string
  dataset_name: string
  created_at: string
  confidence?: number
  cwe_ids?: string[]
}

export interface FindingFacets {
  vuln_class: Record<string, number>
  severity: Record<string, number>
  match_status: Record<string, number>
  model_id: Record<string, number>
  strategy: Record<string, number>
  dataset_name: Record<string, number>
}

export interface GlobalFindingsResponse {
  total: number
  limit: number
  offset: number
  facets: FindingFacets
  items: GlobalFinding[]
}

export interface GlobalFindingsParams {
  q?: string
  vuln_class?: string[]
  severity?: string[]
  match_status?: string[]
  model_id?: string[]
  strategy?: string[]
  experiment_id?: string[]
  dataset_name?: string[]
  created_from?: string
  created_to?: string
  sort?: string
  limit?: number
  offset?: number
}

export function searchFindingsGlobal(params: GlobalFindingsParams, signal?: AbortSignal): Promise<GlobalFindingsResponse> {
  const qs = new URLSearchParams()
  if (params.q) qs.set('q', params.q)
  for (const key of ['vuln_class', 'severity', 'match_status', 'model_id', 'strategy', 'experiment_id', 'dataset_name'] as const) {
    const vals = params[key]
    if (vals && vals.length > 0) {
      for (const v of vals) qs.append(key, v)
    }
  }
  if (params.created_from) qs.set('created_from', params.created_from)
  if (params.created_to) qs.set('created_to', params.created_to)
  if (params.sort) qs.set('sort', params.sort)
  if (params.limit !== undefined) qs.set('limit', String(params.limit))
  if (params.offset !== undefined) qs.set('offset', String(params.offset))
  const query = qs.toString()
  return apiFetch<GlobalFindingsResponse>(`/findings${query ? `?${query}` : ''}`, signal ? { signal } : undefined)
}

// ─── Experiment Import/Export ──────────────────────────────────────────────

export interface ImportSummary {
  experiment_id: string
  renamed_from: string | null
  runs_imported: number
  runs_skipped: number
  datasets_imported: number
  datasets_rehydrated: string[]
  datasets_missing: string[]
  dataset_labels_imported: number
  warnings: string[]
  findings_indexed: number
}

/** Returns the export bundle download URL (no fetch — open in browser directly) */
export function exportBundleUrl(experimentId: string, datasetMode: 'reference' | 'descriptor' = 'descriptor'): string {
  return `${BASE_URL}/experiments/${experimentId}/export?dataset_mode=${datasetMode}`
}

/**
 * Upload a bundle file to import an experiment.
 * Uses XMLHttpRequest so upload progress can be reported via onProgress.
 * Always sends rebuild_findings_index=true.
 * Throws ApiError on non-2xx responses.
 */
export function importBundle(
  file: File,
  conflictPolicy: 'reject' | 'rename' | 'merge',
  onProgress?: (pct: number) => void,
): Promise<ImportSummary> {
  return new Promise<ImportSummary>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${BASE_URL}/experiments/import`)

    if (onProgress) {
      xhr.upload.addEventListener('progress', (event) => {
        if (event.lengthComputable) {
          onProgress(Math.round((event.loaded / event.total) * 100))
        }
      })
    }

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as ImportSummary)
        } catch {
          reject(new ApiError('Invalid JSON response', xhr.status, null))
        }
      } else {
        let message = `API error ${xhr.status}`
        let body: unknown = null
        try {
          body = JSON.parse(xhr.responseText)
          const b = body as Record<string, unknown>
          const detail = b.detail ?? b.message
          if (typeof detail === 'string') {
            message = detail
          }
        } catch {
          // ignore parse failure
        }
        reject(new ApiError(message, xhr.status, body))
      }
    })

    xhr.addEventListener('error', () => {
      reject(new ApiError('Network error during upload', 0, null))
    })

    const formData = new FormData()
    formData.append('file', file)
    formData.append('conflict_policy', conflictPolicy)
    formData.append('rebuild_findings_index', 'true')
    xhr.send(formData)
  })
}

// ─── Smoke Test ────────────────────────────────────────────────────────────

export async function runSmokeTest(): Promise<{ experiment_id: string; message: string; total_runs: number }> {
  return apiFetch('/smoke-test', { method: 'POST' })
}

// ─── Trends ────────────────────────────────────────────────────────────────

export interface TrendPoint {
  experiment_id: string
  completed_at: string
  f1: number
  precision: number
  recall: number
  cost_usd: number
  run_count: number
}

export interface TrendSummary {
  latest_f1: number | null
  prev_f1: number | null
  delta_f1: number | null
  trailing_median_f1: number | null
  is_regression: boolean
}

export interface TrendSeriesKey {
  model: string
  strategy: string
  tool_variant: string
  tool_extensions: string[]
}

export interface TrendSeries {
  key: TrendSeriesKey
  points: TrendPoint[]
  summary: TrendSummary
}

export interface TrendResponse {
  dataset: string
  experiments: Array<{ experiment_id: string; completed_at: string }>
  series: TrendSeries[]
}

export interface TrendOptions {
  limit?: number
  tool_ext?: string
  since?: string
  until?: string
}

export function getTrends(dataset: string, opts: TrendOptions = {}): Promise<TrendResponse> {
  const params = new URLSearchParams({ dataset })
  if (opts.limit !== undefined) params.set('limit', String(opts.limit))
  if (opts.tool_ext) params.set('tool_ext', opts.tool_ext)
  if (opts.since) params.set('since', opts.since)
  if (opts.until) params.set('until', opts.until)
  return apiFetch<TrendResponse>(`/trends?${params.toString()}`)
}

// ─── LLM Providers ────────────────────────────────────────────────────────

export type ProviderAdapter = 'openai_compat' | 'anthropic_compat' | 'bedrock' | 'litellm'
export type ProviderAuthType = 'api_key' | 'aws' | 'none'
export type ProviderProbeStatusFull = 'fresh' | 'stale' | 'failed' | 'disabled' | null
export type ProviderSource = 'builtin' | 'custom'

export interface ProviderDTO {
  id: string
  name: string
  display_name: string
  adapter: ProviderAdapter
  model_id: string
  api_base: string | null
  auth_type: ProviderAuthType
  region: string | null
  enabled: boolean
  api_key_masked: string | null
  last_probe_at: string | null
  last_probe_status: ProviderProbeStatusFull
  last_probe_error: string | null
  source: ProviderSource
}

export interface ProviderListResponse {
  builtin: ProviderDTO[]
  custom: ProviderDTO[]
}

export interface ProviderCreateRequest {
  name: string
  display_name: string
  adapter: ProviderAdapter
  model_id: string
  api_base?: string
  api_key?: string
  auth_type: ProviderAuthType
  region?: string
}

export interface ProviderPatchRequest {
  name?: string
  display_name?: string
  adapter?: ProviderAdapter
  model_id?: string
  api_base?: string | null
  api_key?: string
  auth_type?: ProviderAuthType
  region?: string | null
}

export interface AppSettingsDefaults {
  allow_unavailable_models: boolean
  evidence_assessor: 'heuristic' | 'llm_judge'
  evidence_judge_model: string | null
}

export function listLlmProviders(): Promise<ProviderListResponse> {
  return apiFetch<ProviderListResponse>('/llm-providers')
}

export function createLlmProvider(data: ProviderCreateRequest): Promise<ProviderDTO> {
  return apiFetch<ProviderDTO>('/llm-providers', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export function patchLlmProvider(id: string, data: ProviderPatchRequest): Promise<ProviderDTO> {
  return apiFetch<ProviderDTO>(`/llm-providers/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export function deleteLlmProvider(id: string): Promise<void> {
  return apiFetch<void>(`/llm-providers/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function probeLlmProvider(id: string): Promise<ProviderDTO> {
  return apiFetch<ProviderDTO>(`/llm-providers/${encodeURIComponent(id)}/probe`, { method: 'POST' })
}

export function getSettingsDefaults(): Promise<AppSettingsDefaults> {
  return apiFetch<AppSettingsDefaults>('/settings/defaults')
}

export function patchSettingsDefaults(data: Partial<AppSettingsDefaults>): Promise<AppSettingsDefaults> {
  return apiFetch<AppSettingsDefaults>('/settings/defaults', {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}
