// Typed API client for the Security Review Framework coordinator

const BASE_URL = '/api'

// ─── Data Types ────────────────────────────────────────────────────────────

export interface Batch {
  batch_id: string
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
  batch_id: string
  experiment_id: string
  model: string
  strategy: string
  tool_variant: string
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
}

export interface Finding {
  finding_id: string
  run_id: string
  batch_id: string
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

export interface BatchConfig {
  dataset: string
  models: string[]
  strategies: string[]
  profiles: string[]
  tool_variants: string[]
  verification: string[]
  repetitions: number
  spend_cap_usd?: number
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

export interface RunComparison {
  run_a: Run
  run_b: Run
  found_by_both: Finding[]
  only_in_a: Finding[]
  only_in_b: Finding[]
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

// ─── Fetch Helper ──────────────────────────────────────────────────────────

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    let message = `API error ${res.status}`
    try {
      const body = await res.json()
      message = body.detail ?? body.message ?? message
    } catch {
      // ignore parse failure
    }
    throw new Error(message)
  }
  // 204 No Content
  if (res.status === 204) return undefined as unknown as T
  return res.json() as Promise<T>
}

// ─── Batch Endpoints ───────────────────────────────────────────────────────

export function submitBatch(config: BatchConfig): Promise<Batch> {
  return apiFetch<Batch>('/batches', { method: 'POST', body: JSON.stringify(config) })
}

export function listBatches(): Promise<Batch[]> {
  return apiFetch<Batch[]>('/batches')
}

export function getBatch(batchId: string): Promise<Batch> {
  return apiFetch<Batch>(`/batches/${batchId}`)
}

export function getBatchResults(batchId: string): Promise<{ runs: Run[]; findings: Finding[] }> {
  return apiFetch(`/batches/${batchId}/results`)
}

export function listRuns(batchId: string): Promise<Run[]> {
  return apiFetch<Run[]>(`/batches/${batchId}/runs`)
}

export function getRun(
  batchId: string,
  runId: string
): Promise<Run & { findings: Finding[]; tool_calls: ToolCall[]; messages: Message[] }> {
  return apiFetch(`/batches/${batchId}/runs/${runId}`)
}

export function cancelBatch(batchId: string): Promise<void> {
  return apiFetch<void>(`/batches/${batchId}/cancel`, { method: 'POST' })
}

/** Returns the download URL (not a fetch — open in browser directly) */
export function downloadReports(batchId: string): string {
  return `${BASE_URL}/batches/${batchId}/results/download`
}

export function compareRuns(
  batchId: string,
  runAId: string,
  runBId: string
): Promise<RunComparison> {
  return apiFetch<RunComparison>(
    `/batches/${batchId}/compare?a=${encodeURIComponent(runAId)}&b=${encodeURIComponent(runBId)}`
  )
}

export function searchFindings(batchId: string, q: string): Promise<Finding[]> {
  return apiFetch<Finding[]>(
    `/batches/${batchId}/findings/search?q=${encodeURIComponent(q)}`
  )
}

export function reclassifyFinding(
  batchId: string,
  runId: string,
  findingId: string,
  newStatus: string,
  note: string
): Promise<void> {
  return apiFetch<void>(`/batches/${batchId}/runs/${runId}/reclassify`, {
    method: 'POST',
    body: JSON.stringify({ finding_id: findingId, new_status: newStatus, note }),
  })
}

export function toolAudit(batchId: string, runId: string): Promise<ToolCall[]> {
  return apiFetch<ToolCall[]>(`/batches/${batchId}/runs/${runId}/tool-audit`)
}

export function compareBatches(
  batchAId: string,
  batchBId: string
): Promise<{
  metric_deltas: Record<string, unknown>[]
  fp_patterns: FPPattern[]
  stability: Record<string, unknown>
}> {
  return apiFetch(`/batches/compare?a=${encodeURIComponent(batchAId)}&b=${encodeURIComponent(batchBId)}`)
}

export function getFPPatterns(batchId: string): Promise<FPPattern[]> {
  return apiFetch<FPPattern[]>(`/batches/${batchId}/fp-patterns`)
}

export function estimateBatch(config: Partial<BatchConfig>): Promise<CostEstimate> {
  return apiFetch<CostEstimate>('/batches/estimate', {
    method: 'POST',
    body: JSON.stringify(config),
  })
}

// ─── Dataset Endpoints ─────────────────────────────────────────────────────

export function listDatasets(): Promise<Dataset[]> {
  return apiFetch<Dataset[]>('/datasets')
}

export function discoverCVEs(criteria: Record<string, unknown>): Promise<CVECandidate[]> {
  return apiFetch<CVECandidate[]>('/datasets/discover-cves', {
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

export function getLabels(datasetName: string): Promise<Label[]> {
  return apiFetch<Label[]>(`/datasets/${encodeURIComponent(datasetName)}/labels`)
}

export function getFileTree(datasetName: string): Promise<FileTree> {
  return apiFetch<FileTree>(`/datasets/${encodeURIComponent(datasetName)}/tree`)
}

export function getFileContent(
  datasetName: string,
  filePath: string
): Promise<{ content: string; language: string }> {
  return apiFetch(
    `/datasets/${encodeURIComponent(datasetName)}/file?path=${encodeURIComponent(filePath)}`
  )
}

// ─── Config Endpoints ──────────────────────────────────────────────────────

export function listModels(): Promise<string[]> {
  return apiFetch<string[]>('/models')
}

export function listStrategies(): Promise<string[]> {
  return apiFetch<string[]>('/strategies')
}

export function listProfiles(): Promise<string[]> {
  return apiFetch<string[]>('/profiles')
}

export function listTemplates(): Promise<InjectionTemplate[]> {
  return apiFetch<InjectionTemplate[]>('/templates')
}

// ─── Smoke Test ────────────────────────────────────────────────────────────

export async function runSmokeTest(): Promise<{ batch_id: string; message: string; total_runs: number }> {
  return apiFetch('/smoke-test', { method: 'POST' })
}
