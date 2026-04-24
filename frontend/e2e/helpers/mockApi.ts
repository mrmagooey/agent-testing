import type { Page, Route } from '@playwright/test'
import { readFileSync } from 'fs'
import { join, dirname } from 'path'
import { fileURLToPath } from 'url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const fixturesDir = join(__dirname, '../fixtures')

function loadFixture<T>(name: string): T {
  return JSON.parse(readFileSync(join(fixturesDir, name), 'utf-8')) as T
}

const experiments = loadFixture<unknown[]>('experiments.json')
const runs = loadFixture<unknown[]>('runs.json')
const findings = loadFixture<unknown[]>('findings.json')
const datasets = loadFixture<unknown[]>('datasets.json')
const labels = loadFixture<unknown[]>('labels.json')
const cveCandidates = loadFixture<unknown[]>('cve-candidates.json')
const runFull = loadFixture<{ tool_calls: unknown[] }>('run-full.json')
const comparison = loadFixture<unknown>('comparison.json')
const experimentResults = loadFixture<unknown>('experiment-results.json')
const fpPatterns = loadFixture<unknown[]>('fp-patterns.json')
const fileTree = loadFixture<unknown>('file-tree.json')
const templates = loadFixture<unknown[]>('templates.json')
const accuracyMatrix = loadFixture<unknown>('accuracy-matrix.json')
const trendsData = loadFixture<unknown>('trends.json')

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

export async function mockApi(page: Page) {
  if (process.env.E2E_LIVE === '1') return
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()

    // --- Experiments ---
    if (path === '/experiments' && method === 'GET') {
      return json(route, experiments)
    }
    if (path === '/experiments' && method === 'POST') {
      // Validate the submitted config shape the way the real coordinator does, so
      // UI-level regressions (e.g. submitting with empty tool_variants) surface in
      // e2e tests instead of being silently accepted.
      const body = route.request().postDataJSON() as Partial<Record<string, unknown>>
      const requiredArrayFields = ['models', 'strategies', 'tool_variants', 'verification'] as const
      for (const field of requiredArrayFields) {
        const value = body[field]
        if (!Array.isArray(value) || value.length === 0) {
          return json(route, { detail: `${field} must be a non-empty array` }, 422)
        }
      }
      if (!body.dataset) {
        return json(route, { detail: 'dataset is required' }, 422)
      }
      // Simulate unavailable_models error unless override is set
      if (!body.allow_unavailable_models) {
        const models = body.models as string[]
        const unavailableInFixture = ['gpt-4o-mini-unavailable', 'claude-3-unavailable']
        const unavailable = models.filter((m) => unavailableInFixture.includes(m))
        if (unavailable.length > 0) {
          return json(route, {
            detail: {
              error: 'unavailable_models',
              models: unavailable.map((id) => ({ id, status: 'key_missing' })),
            },
          }, 400)
        }
      }
      const newExperiment = {
        ...(experiments[0] as Record<string, unknown>),
        experiment_id: 'newexperiment-1111-1111-1111-111111111111',
        status: 'pending',
        total_runs: 8,
        completed_runs: 0,
        running_runs: 0,
        pending_runs: 8,
        failed_runs: 0,
        total_cost_usd: 0,
      }
      return json(route, newExperiment, 201)
    }
    if (path === '/experiments/estimate' && method === 'POST') {
      return json(route, {
        total_runs: 8,
        estimated_cost_usd: 4.0,
        by_model: { 'gpt-4o': 2.0, 'claude-3-5-sonnet-20241022': 2.0 },
      })
    }
    if (path.match(/^\/experiments\/[^/]+\/results$/) && method === 'GET') {
      return json(route, experimentResults)
    }
    if (path.match(/^\/experiments\/[^/]+\/runs$/) && method === 'GET') {
      return json(route, runs)
    }
    if (path.match(/^\/experiments\/[^/]+\/runs\/[^/]+$/) && method === 'GET') {
      return json(route, runFull)
    }
    if (path.match(/^\/experiments\/[^/]+\/compare$/) && method === 'GET') {
      return json(route, comparison)
    }
    if (path.match(/^\/experiments\/[^/]+\/cancel$/) && method === 'POST') {
      return json(route, null, 204)
    }
    if (path.match(/^\/experiments\/[^/]+\/findings\/search$/) && method === 'GET') {
      return json(route, findings)
    }
    if (path.match(/^\/experiments\/[^/]+\/runs\/[^/]+\/reclassify$/) && method === 'POST') {
      return json(route, null, 204)
    }
    if (path.match(/^\/experiments\/[^/]+\/runs\/[^/]+\/tool-audit$/) && method === 'GET') {
      return json(route, runFull.tool_calls)
    }
    if (path.match(/^\/experiments\/[^/]+\/fp-patterns$/) && method === 'GET') {
      return json(route, fpPatterns)
    }
    if (path === '/experiments/compare' && method === 'GET') {
      return json(route, {
        metric_deltas: [
          {
            experiment_id: 'gpt-4o__zero_shot__with_tools',
            precision_delta: 0.056,
            recall_delta: 0.045,
            f1_delta: 0.050,
          },
        ],
        fp_patterns: fpPatterns,
        stability: {},
      })
    }
    if (path === '/compare-runs' && method === 'GET') {
      return json(route, comparison)
    }
    if (path.match(/^\/experiments\/[^/]+$/) && method === 'GET') {
      const experimentId = path.split('/')[2]
      const experiment = (experiments as Array<Record<string, unknown>>).find((e) => e.experiment_id === experimentId) ?? experiments[0]
      return json(route, experiment)
    }

    // --- Datasets ---
    if (path === '/datasets' && method === 'GET') {
      return json(route, datasets)
    }
    if (path === '/datasets/discover-cves' && method === 'POST') {
      return json(route, cveCandidates)
    }
    if (path.startsWith('/datasets/resolve-cve') && method === 'GET') {
      return json(route, cveCandidates[0])
    }
    if (path === '/datasets/import-cve' && method === 'POST') {
      return json(route, datasets[0])
    }
    if (path.match(/^\/datasets\/[^/]+\/labels$/) && method === 'GET') {
      return json(route, labels)
    }
    if (path.match(/^\/datasets\/[^/]+\/tree$/) && method === 'GET') {
      return json(route, fileTree)
    }
    if (path.match(/^\/datasets\/[^/]+\/file$/) && method === 'GET') {
      const reqPath = url.searchParams.get('path') ?? ''
      return json(route, {
        path: reqPath,
        content: 'def login(username, password):\n    query = f"SELECT * FROM users WHERE username=\'{username}\'"\n    return db.execute(query)\n',
        language: 'python',
        line_count: 3,
        size_bytes: 120,
        labels: (labels as Array<Record<string, unknown>>).filter((l) => l['file_path'] === reqPath),
        binary: false,
        truncated: false,
      })
    }
    if (path.match(/^\/datasets\/[^/]+\/inject\/preview$/) && method === 'POST') {
      return json(route, {
        before: 'def safe_query(val):\n    return db.execute("SELECT * FROM t WHERE id = ?", [val])\n',
        after: 'def safe_query(val):\n    return db.execute(f"SELECT * FROM t WHERE id = \'{val}\'")\n',
        language: 'python',
        label: labels[0],
        warnings: [],
      })
    }
    if (path.match(/^\/datasets\/[^/]+\/inject$/) && method === 'POST') {
      return json(route, { label_id: 'label-new-injected' })
    }

    // --- Config ---
    if (path === '/models' && method === 'GET') {
      return json(route, [
        {
          provider: 'openai',
          probe_status: 'fresh',
          models: [
            { id: 'gpt-4o', display_name: 'GPT-4o', status: 'available' },
            { id: 'gpt-4o-mini', display_name: 'GPT-4o Mini', status: 'available' },
          ],
        },
        {
          provider: 'anthropic',
          probe_status: 'fresh',
          models: [
            { id: 'claude-3-5-sonnet-20241022', display_name: 'Claude 3.5 Sonnet', status: 'available' },
            { id: 'claude-3-haiku-20240307', display_name: 'Claude 3 Haiku', status: 'available' },
          ],
        },
        {
          provider: 'google',
          probe_status: 'fresh',
          models: [
            { id: 'gemini-1.5-pro', display_name: 'Gemini 1.5 Pro', status: 'available' },
          ],
        },
      ])
    }
    if (path === '/strategies' && method === 'GET') {
      return json(route, ['zero_shot', 'chain_of_thought', 'few_shot', 'agent'])
    }
    if (path === '/profiles' && method === 'GET') {
      return json(route, ['default', 'strict', 'lenient'])
    }
    if (path === '/tool-extensions' && method === 'GET') {
      return json(route, [
        { key: 'tree_sitter', label: 'Tree-sitter', available: true },
        { key: 'lsp', label: 'LSP', available: true },
        { key: 'devdocs', label: 'DevDocs', available: false },
      ])
    }
    if (path === '/templates' && method === 'GET') {
      return json(route, templates)
    }

    // --- Global Findings ---
    if (path === '/findings' && method === 'GET') {
      const q = url.searchParams.get('q') ?? ''
      const limitParam = Number(url.searchParams.get('limit') ?? 50)
      const offsetParam = Number(url.searchParams.get('offset') ?? 0)

      const allItems = [
        {
          finding_id: 'find-001',
          run_id: 'run-001-aaa',
          experiment_id: 'aaaaaaaa-0001-0001-0001-000000000001',
          title: 'SQL Injection in user login handler',
          description: 'User-supplied input is concatenated directly into a SQL query without parameterization.',
          vuln_class: 'sqli',
          severity: 'critical',
          match_status: 'tp',
          file_path: 'src/auth/login.py',
          line_start: 42,
          line_end: 47,
          recommendation: 'Use parameterized queries or an ORM to prevent SQL injection.',
          evidence_quality: 'strong',
          matched_label_id: 'label-001',
          experiment_name: 'GPT-4o Zero-Shot April 2026',
          model_id: 'gpt-4o',
          strategy: 'zero_shot',
          dataset_name: 'cve-2024-python',
          created_at: '2026-04-17T08:15:00Z',
          confidence: 0.94,
          cwe_ids: ['CWE-89'],
        },
        {
          finding_id: 'find-002',
          run_id: 'run-001-aaa',
          experiment_id: 'aaaaaaaa-0001-0001-0001-000000000001',
          title: 'Reflected XSS in search results',
          description: 'The search query parameter is reflected in the response without HTML encoding.',
          vuln_class: 'xss',
          severity: 'high',
          match_status: 'fp',
          file_path: 'src/search/results.py',
          line_start: 88,
          line_end: 92,
          recommendation: 'Encode all user-supplied data before rendering in HTML context.',
          evidence_quality: 'adequate',
          matched_label_id: null,
          experiment_name: 'GPT-4o Zero-Shot April 2026',
          model_id: 'gpt-4o',
          strategy: 'zero_shot',
          dataset_name: 'cve-2024-python',
          created_at: '2026-04-17T08:16:00Z',
          confidence: 0.71,
          cwe_ids: ['CWE-79'],
        },
        {
          finding_id: 'find-003',
          run_id: 'run-001-aaa',
          experiment_id: 'aaaaaaaa-0001-0001-0001-000000000001',
          title: 'Path traversal in file download endpoint',
          description: 'The file path parameter is not sanitized, allowing traversal outside the allowed directory.',
          vuln_class: 'path_traversal',
          severity: 'high',
          match_status: 'tp',
          file_path: 'src/files/download.py',
          line_start: 15,
          line_end: 20,
          recommendation: 'Validate and normalize file paths before use.',
          evidence_quality: 'strong',
          matched_label_id: 'label-003',
          experiment_name: 'Claude 3.5 Sonnet Chain-of-Thought April 2026',
          model_id: 'claude-3-5-sonnet-20241022',
          strategy: 'chain_of_thought',
          dataset_name: 'cve-2024-python',
          created_at: '2026-04-17T09:00:00Z',
          cwe_ids: ['CWE-22'],
        },
      ]

      const filtered = q
        ? allItems.filter(
            (item) =>
              item.title.toLowerCase().includes(q.toLowerCase()) ||
              item.description.toLowerCase().includes(q.toLowerCase()) ||
              item.vuln_class.toLowerCase().includes(q.toLowerCase()),
          )
        : allItems

      const paginated = filtered.slice(offsetParam, offsetParam + limitParam)

      return json(route, {
        total: filtered.length,
        limit: limitParam,
        offset: offsetParam,
        facets: {
          vuln_class: { sqli: 1, xss: 1, path_traversal: 1 },
          severity: { critical: 1, high: 2 },
          match_status: { tp: 2, fp: 1 },
          model_id: { 'gpt-4o': 2, 'claude-3-5-sonnet-20241022': 1 },
          strategy: { zero_shot: 2, chain_of_thought: 1 },
          dataset_name: { 'cve-2024-python': 3 },
        },
        items: paginated,
      })
    }

    // --- Trends ---
    if (path === '/trends' && method === 'GET') {
      const dataset = url.searchParams.get('dataset')
      if (!dataset) {
        return json(route, { detail: 'dataset query parameter is required' }, 400)
      }
      return json(route, trendsData)
    }

    // --- Matrix ---
    if (path === '/matrix/accuracy' && method === 'GET') {
      return json(route, accuracyMatrix)
    }

    // --- Smoke test ---
    if (path === '/smoke-test' && method === 'POST') {
      return json(route, {
        experiment_id: 'smoke-test-experiment-id-0000000000001',
        message: 'Smoke test experiment created with 1 run.',
        total_runs: 1,
      })
    }

    // Fallthrough
    return route.continue()
  })
}

/**
 * Layer an imported-dataset into the mock API so subsequent GET /datasets
 * responses include the new dataset, and related per-dataset endpoints
 * return plausible data.
 *
 * Must be called *after* mockApi() because it uses route.fallback() to
 * chain on top of the existing handler rather than replace it.
 *
 * @param page   The Playwright page object.
 * @param name   The dataset name that the import returned (e.g. 'cve-2024-imported').
 */
export async function mockImportedDataset(page: Page, name: string) {
  const importedDataset = {
    name,
    source: 'cve',
    label_count: 3,
    file_count: 12,
    size_bytes: 204800,
    created_at: new Date().toISOString(),
    languages: ['python'],
  }

  // Prepend the imported dataset to GET /datasets
  await page.route('**/api/datasets', async (route) => {
    const method = route.request().method()
    if (method !== 'GET') {
      return route.fallback()
    }
    const allDatasets = [importedDataset, ...(datasets as unknown[])]
    return json(route, allDatasets)
  })

  // Labels for the imported dataset — reuse the shape from the base fixture
  await page.route(`**/api/datasets/${name}/labels`, async (route) => {
    return json(route, [
      {
        label_id: `${name}-label-001`,
        dataset: name,
        file_path: 'src/auth/login.py',
        line_start: 10,
        line_end: 15,
        vuln_class: 'sqli',
        cwe: 'CWE-89',
        severity: 'critical',
        description: `SQL injection in ${name}`,
        source: 'cve',
      },
    ])
  })

  // File tree for the imported dataset
  await page.route(`**/api/datasets/${name}/tree`, async (route) => {
    return json(route, fileTree)
  })

  // File content for the imported dataset
  await page.route(`**/api/datasets/${name}/file*`, async (route) => {
    const url = new URL(route.request().url())
    const reqPath = url.searchParams.get('path') ?? ''
    return json(route, {
      path: reqPath,
      content: 'def login(username, password):\n    query = f"SELECT * FROM users WHERE username=\'{username}\'"\n    return db.execute(query)\n',
      language: 'python',
      line_count: 3,
      size_bytes: 120,
      labels: [],
      binary: false,
      truncated: false,
    })
  })

  // Override the global /findings endpoint to include a finding that
  // references the imported dataset, so the cross-feature assertion can verify
  // the end-to-end chain.
  await page.route('**/api/findings*', async (route) => {
    const method = route.request().method()
    if (method !== 'GET') {
      return route.fallback()
    }
    const importedFinding = {
      finding_id: `${name}-find-001`,
      run_id: 'run-001-aaa',
      experiment_id: 'newexperiment-1111-1111-1111-111111111111',
      title: `SQL Injection discovered in ${name}`,
      description: 'User-supplied input is concatenated directly into a SQL query.',
      vuln_class: 'sqli',
      severity: 'critical',
      match_status: 'tp',
      file_path: 'src/auth/login.py',
      line_start: 10,
      line_end: 15,
      recommendation: 'Use parameterized queries.',
      evidence_quality: 'strong',
      matched_label_id: `${name}-label-001`,
      experiment_name: 'CVE Import E2E Experiment',
      model_id: 'gpt-4o',
      strategy: 'zero_shot',
      dataset_name: name,
      created_at: new Date().toISOString(),
      confidence: 0.95,
      cwe_ids: ['CWE-89'],
    }
    const url = new URL(route.request().url())
    const q = url.searchParams.get('q') ?? ''
    const limitParam = Number(url.searchParams.get('limit') ?? 50)
    const offsetParam = Number(url.searchParams.get('offset') ?? 0)
    const allItems = [importedFinding]
    const filtered = q
      ? allItems.filter(
          (item) =>
            item.title.toLowerCase().includes(q.toLowerCase()) ||
            item.description.toLowerCase().includes(q.toLowerCase()) ||
            item.vuln_class.toLowerCase().includes(q.toLowerCase()),
        )
      : allItems
    const paginated = filtered.slice(offsetParam, offsetParam + limitParam)
    return json(route, {
      total: filtered.length,
      limit: limitParam,
      offset: offsetParam,
      facets: {
        vuln_class: { sqli: 1 },
        severity: { critical: 1 },
        match_status: { tp: 1 },
        model_id: { 'gpt-4o': 1 },
        strategy: { zero_shot: 1 },
        dataset_name: { [name]: 1 },
      },
      items: paginated,
    })
  })
}
