# AI/LLM Security Code Review Testing Framework

A framework for empirically comparing LLM-driven approaches to security code review of production source code. It runs a full experiment matrix across models, agent decomposition strategies, tool access, review profiles, and optional verification passes, then scores each run against ground truth labels (CVE patches + injected vulnerabilities).

The goal: identify the best pipeline for production security reviews by measuring precision, recall, F1, false-positive rate, evidence quality, and cost-per-true-positive at scale.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design.

---

## What it does

Given a corpus of code targets with known vulnerabilities, the framework runs the cartesian product of:

- **Models** — any LiteLLM-supported backend (OpenAI, Anthropic, Gemini, OpenRouter, Mistral, ...)
- **Strategies** — how the agent decomposes the review:
  - `single_agent` — one agent scans the full repo
  - `per_file` — one agent per source file
  - `per_vuln_class` — specialist agents per vulnerability class (SQLi, XSS, RCE, ...)
  - `sast_first` — Semgrep triage then LLM investigates flagged files
  - `diff_review` — review only the changed lines between two refs
- **Tool variants** — `with_tools` (read_file, grep, etc.) vs. `without_tools`
- **Review profiles** — `default`, `strict`, `comprehensive`, `owasp_focused`, `quick_scan`
- **Tool extensions** — optional MCP-backed tools for enhanced code inspection and documentation lookup:
  - `TREE_SITTER` — symbol search and AST queries (`ts_find_symbol`, `ts_get_ast`, `ts_query`, `ts_list_functions`)
  - `LSP` — language-server-driven definition/reference/hover/symbol navigation (`lsp_definition`, `lsp_references`, `lsp_hover`, `lsp_document_symbols`, `lsp_workspace_symbols`)
  - `DEVDOCS` — offline documentation search (`doc_search`, `doc_fetch`, `doc_list_docsets`)
- **Verification** — optional second-pass LLM verifier over candidate findings
- **Repetitions** — for statistical significance (Wilson CI + McNemar's test)

Each run produces a `RunResult` with findings, token counts, tool-call audit, conversation log, duration, and an evaluation against ground truth using bipartite matching.

---

## Architecture

```
┌──────────────────────────────┐        ┌──────────────────────────────┐
│  Coordinator (FastAPI)       │  K8s   │  Worker pods (1 per run)     │
│  - REST API + React SPA      │──────▶ │  ExperimentWorker.run()      │
│  - Matrix expansion          │  Jobs  │   → ModelProvider (LiteLLM)  │
│  - Per-model concurrency     │        │   → Strategy + Tools         │
│  - SQLite batch tracking     │        │   → Finding parser           │
│  - Result aggregation        │        │   → Evaluator + Reporter     │
│  - Cost & spend caps         │◀───────│  Writes run_result.json,     │
└──────────────────────────────┘ shared │  findings.jsonl, report.md   │
                                storage └──────────────────────────────┘
```

- **Coordinator**: single FastAPI deployment that serves the API and the frontend SPA on one port. Manages the batch lifecycle, creates K8s Jobs for each run, enforces per-model concurrency caps, and finalizes matrix reports.
- **Workers**: one K8s Job per `ExperimentRun`. Pulls config from shared storage, runs the strategy against `LiteLLMProvider`, and writes artifacts back to shared storage.
- **Ground truth**: labels come from CVE-patched repos (auto-imported) and from the vulnerability injection system (templated vulns applied to clean repos for controlled experiments).
- **VPC-only**: network policies restrict worker pods to LLM egress + shared storage. Tool calls are audited and anomalous access is flagged.

---

## Quick start

### Prerequisites

- Python 3.14+
- [uv](https://github.com/astral-sh/uv) package manager
- Node.js 20+ (for the frontend)
- Docker + Minikube (for the full K8s deployment)

### Install

```bash
uv sync --all-extras
cd frontend && npm install
```

### Run the offline test suite

```bash
uv run pytest
```

### Run the live smoke test

A single-run end-to-end test that hits a real LLM via OpenRouter. Requires `OPENROUTER_TEST_KEY`:

```bash
export OPENROUTER_TEST_KEY=sk-or-v1-...
uv run pytest tests/e2e/test_live_smoke.py -v -s
```

The test skips automatically if the key is not set.

### Run the coordinator locally

```bash
make dev-coordinator    # FastAPI on :8080
make dev-frontend       # Vite dev server on :5173
```

### Deploy to Minikube

```bash
make minikube-start
make docker-build-all          # build images inside minikube's docker daemon
make helm-install-minikube     # helm upgrade --install with values-minikube.yaml
```

Supply an LLM key via `--set-string` (or an untracked local values file) on first install:

```bash
helm upgrade --install sec-review ./helm/sec-review \
  --namespace sec-review --create-namespace \
  --values ./helm/sec-review/values-minikube.yaml \
  --set-string secrets.apiKeys.openrouter=$OPENROUTER_TEST_KEY \
  --kube-context agent-testing

minikube service sec-review-coordinator -n sec-review  # opens the UI
```

### Deploy to production

```bash
helm upgrade --install sec-review ./helm/sec-review \
  --namespace sec-review --create-namespace \
  --values ./helm/sec-review/values-prod.yaml
```

The production values file expects `llm-api-keys` to already exist in the
release namespace (managed by external-secrets / sealed-secrets).

---

## Repository layout

```
src/sec_review_framework/
  coordinator.py        FastAPI app + BatchCoordinator
  worker.py             ExperimentWorker (K8s Job entrypoint)
  db.py                 SQLite batch/run tracking
  models/               LiteLLM provider + retry logic
  strategies/           single_agent, per_file, per_vuln_class, sast_first, diff_review
  tools/                repo_access, semgrep, doc_lookup, registry with audit log
  ground_truth/         TargetCodebase, LabelStore, CVE importer, vuln injector
  evaluation/           Bipartite matcher, metrics, evidence quality
  verification/         Optional LLM verifier pass
  reporting/            Markdown + JSON matrix reports
  cost/                 Per-model token pricing and spend tracking
  profiles/             Review profile registry
  feedback/             Batch comparison, FP pattern mining
  data/                 Pydantic models (experiment, findings, evaluation)
  config.py             YAML config loaders

config/                 Pricing, concurrency, prompts, profiles, vuln_classes
datasets/               CVE imports, injection templates, target repos
frontend/               React 19 + Vite SPA (served by coordinator)
helm/sec-review/        Helm chart: Chart.yaml, values{,-minikube,-prod}.yaml, templates/
tests/                  unit / integration / e2e / infra / performance
```

---

## Tests

The suite is organized by isolation level:

| Layer | What it exercises | LLM calls |
|---|---|---|
| `tests/unit/` | Individual modules (parsers, evaluator, dedup, cost, ...) | None |
| `tests/integration/` | Strategy + real tools + tmp repo + FakeModelProvider | None |
| `tests/e2e/test_offline_smoke.py` | Full `ExperimentWorker` pipeline | FakeModelProvider |
| `tests/e2e/test_coordinator_smoke.py` | Full batch lifecycle via `TestClient` | FakeModelProvider |
| `tests/e2e/test_live_smoke.py` | Full pipeline with a real OpenRouter model | Real LLM |
| `tests/infra/` | Docker build + K8s manifest validation | None |

Run a subset with pytest markers:

```bash
uv run pytest -m "not slow"
uv run pytest tests/unit/
```

---

## Configuration

All config lives under `config/` as YAML, loaded via Pydantic:

- `models.yaml` — enabled model IDs and per-model overrides
- `pricing.yaml` — per-model token pricing ($/M tokens) for cost tracking
- `concurrency.yaml` — per-model and global concurrency caps
- `retry.yaml` — retry policy (max retries, backoff, retryable status codes)
- `review_profiles.yaml` — system prompt modifiers per profile
- `vuln_classes.yaml` — vulnerability taxonomy with CWE mappings
- `experiments.yaml` — default experiment matrix presets
- `coordinator.yaml` — namespace, storage paths, spend caps
- `prompts/` — finding output format, verification prompt

### Tool Extensions Configuration

Tool extensions are optional and disabled by default. Enable them in the Helm values:

```yaml
workerTools:
  treeSitter:
    enabled: true
  lsp:
    enabled: true
  devdocs:
    enabled: true
    docsets:
      - python~3.12
      - javascript
      - go
```

See `helm/sec-review/values.yaml` for the full `workerTools` block. When enabled, each extension is spawned as a subprocess MCP server within the worker pod. The coordinator exposes a `GET /api/tool-extensions` endpoint listing available extensions and their capabilities.

---

## Makefile targets

```
make minikube-start           Start minikube profile 'agent-testing'
make docker-build-all         Build worker + coordinator images in minikube's daemon
make helm-lint                Lint the chart
make helm-template            Render the chart with values-minikube.yaml (stdout)
make helm-install-minikube    helm upgrade --install using values-minikube.yaml
make helm-uninstall           helm uninstall the release
make dev-coordinator          Run coordinator locally with --reload
make dev-frontend             Run the Vite dev server
make test                     Run pytest (fails fast with -x)
make lint                     ruff check
make format                   ruff format
make sync                     uv sync --all-extras
```

---

## License

Internal research project.
