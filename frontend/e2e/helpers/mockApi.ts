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
        'gpt-4o',
        'gpt-4o-mini',
        'claude-3-5-sonnet-20241022',
        'claude-3-haiku-20240307',
        'gemini-1.5-pro',
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
