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
│  - SQLite experiment tracking │        │   → Finding parser           │
│  - Result aggregation        │        │   → Evaluator + Reporter     │
│  - Cost & spend caps         │◀───────│  Writes run_result.json,     │
└──────────────────────────────┘ shared │  findings.jsonl, report.md   │
                                storage └──────────────────────────────┘
```

- **Coordinator**: single FastAPI deployment that serves the API and the frontend SPA on one port. Manages the experiment lifecycle, creates K8s Jobs for each run, enforces per-model concurrency caps, and finalizes matrix reports.
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
  coordinator.py        FastAPI app + ExperimentCoordinator
  worker.py             ExperimentWorker (K8s Job entrypoint)
  db.py                 SQLite experiment/run tracking
  models/               LiteLLM provider + retry logic
  strategies/           single_agent, per_file, per_vuln_class, sast_first, diff_review
  tools/                repo_access, semgrep, doc_lookup, registry with audit log
  ground_truth/         TargetCodebase, LabelStore, CVE importer, vuln injector
  evaluation/           Bipartite matcher, metrics, evidence quality
  verification/         Optional LLM verifier pass
  reporting/            Markdown + JSON matrix reports
  cost/                 Per-model token pricing and spend tracking
  profiles/             Review profile registry
  feedback/             Experiment comparison, FP pattern mining
  data/                 Pydantic models (experiment, findings, evaluation)
  prompts/              System + user prompt templates (loader, registry)
  config.py             YAML config loaders

config/                 models, pricing, concurrency, retry, profiles, vuln_classes, experiments, coordinator
frontend/               React 19 + Vite SPA (served by coordinator)
helm/sec-review/        Chart.yaml, values{,-minikube,-prod,-e2e}.yaml, templates/, grafana-dashboard.json
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
| `tests/e2e/test_coordinator_smoke.py` | Full experiment lifecycle via `TestClient` | FakeModelProvider |
| `tests/e2e/test_live_smoke.py` | Full pipeline with a real OpenRouter model | Real LLM |
| `tests/infra/` | Docker build + K8s manifest validation | None |

Run a subset with pytest markers:

```bash
uv run pytest -m "not slow"
uv run pytest tests/unit/
```

### Live e2e against a kind cluster

Frontend specs tagged `@live` (currently `frontend/e2e/live-golden-path.spec.ts`) run against a real coordinator + worker deployed to a dedicated kind cluster. Every kubectl/helm call is pinned to `--context=kind-sec-review-e2e`, so these targets are safe to run regardless of your active context (e.g. while minikube is also up).

Prereqs: `kind`, `helm`, `kubectl`, Node 22+, and one of the backend credentials:

```bash
export OPENROUTER_TEST_KEY=<key>                 # OpenRouter, or
export LIVE_TEST_API_BASE=<url>                  # OpenAI-compatible
export LIVE_TEST_API_KEY=<key>
```

One-shot (bootstraps the cluster if absent, manages port-forward, runs specs):

```bash
make kind-e2e-playwright-all
make kind-e2e-down                      # when finished
```

Step-by-step (re-runnable while the cluster stays up):

```bash
make kind-e2e-up                        # kind + helm install + seed fixture
./scripts/e2e-live/port-forward.sh &    # kubectl port-forward :8080 → coordinator
make kind-e2e-pytest                    # backend live tests
make kind-e2e-playwright                # Playwright --grep @live
make kind-e2e-down
```

To add a spec to the live suite, tag its `describe` with `@live` and make it tolerant of real-backend state (no mocked `page.route()`, don't assume a clean dataset). See `live-golden-path.spec.ts` for the pattern — it submits via `request.post('/api/experiments')` rather than driving the form, because the dataset and model lists come from the live database.

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

Prompt templates (system / user / verification) live alongside the code at `src/sec_review_framework/prompts/`, not under `config/`.

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

### Models

The framework ships with 29 LiteLLM-backed models across 7 major providers. Enable each via its required environment variable:

| Provider | Models | Auth | API Key Env |
|---|---|---|---|
| **OpenAI** | GPT-4o, GPT-4o mini, GPT-4.1, GPT-4.1 mini, o1, o3, o3-mini, o4-mini | API key | `OPENAI_API_KEY` |
| **Anthropic** | Claude Opus 4.7, Sonnet 4.6, Haiku 4.5, Claude 3.5 Sonnet (latest), Claude 3.5 Haiku (latest) | API key | `ANTHROPIC_API_KEY` |
| **Google Gemini** | Gemini 2.5 Pro, 2.0 Pro, 2.0 Flash, 1.5 Pro | API key | `GEMINI_API_KEY` |
| **Mistral** | Large, Small, Codestral | API key | `MISTRAL_API_KEY` |
| **Cohere** | Command R+, Command R | API key | `COHERE_API_KEY` |
| **OpenRouter** | Llama 3.1 8B, Llama 3.2 3B | API key | `OPENROUTER_API_KEY` |
| **AWS Bedrock** | Claude 3.5 Sonnet/Haiku, Amazon Nova Pro/Lite, Llama 3.1 70B | IAM + region | See below |

For AWS Bedrock, prefer IRSA (IAM Roles for Service Accounts): annotate the coordinator's ServiceAccount with `eks.amazonaws.com/role-arn: arn:aws:iam::...`. For development, set static credentials via `secrets.aws.accessKeyId` and `secrets.aws.secretAccessKey` in Helm values, but this is not recommended in production. All Bedrock models default to `us-east-1`; override via `helm/sec-review/values.yaml` (`providerConfig.bedrock.region`).

Models inherit defaults from `config/models.yaml`: `temperature: 0.2`, `max_tokens: 8192`. Override per-model in the YAML or via the frontend picker.

### Provider availability probing

The coordinator probes each provider's live model catalog (via `litellm.get_valid_models`, plus a Bedrock-specific path) and caches the snapshot. Experiment submissions that reference a model the latest probe reports as unavailable are rejected upfront — set `allow_unavailable_models: true` on the experiment to bypass the check (e.g. for offline replay or when targeting a model the probe cannot see).

Helm knobs in `values.yaml` under `providerProbe`:

- `enabled` — turn the whole probe loop on/off (default `true`).
- `ttlSeconds` — how often each provider is re-probed (default `600`).
- `maxStaleSeconds` — how long a stale cached snapshot is still served before the probe is reported `failed`. Defaults to `ttlSeconds * 6` (≈1 h).
- `bedrock.enabled` + `bedrock.regions` — opt-in Bedrock probing per region.

`PROVIDER_PROBE_MAX_STALE_SECONDS` on the coordinator deployment overrides `maxStaleSeconds` at runtime without a chart redeploy.

---

## Makefile targets

```
make minikube-start             Start minikube profile 'agent-testing'
make minikube-stop              Stop the minikube profile
make minikube-delete            Delete the minikube profile
make minikube-dashboard         Open the minikube dashboard
make docker-build-all           Build worker + coordinator images in minikube's daemon
make docker-build-worker        Build only the worker image
make docker-build-coordinator   Build only the coordinator image
make helm-lint                  Lint the chart
make helm-template              Render the chart with values-minikube.yaml (stdout)
make helm-install-minikube      helm upgrade --install using values-minikube.yaml
make helm-upgrade-minikube      Alias for helm-install-minikube (idempotent upgrade)
make helm-uninstall             helm uninstall the release
make redeploy                   Rebuild only changed images and helm upgrade if needed (stamp-driven)
make redeploy-clean             Clear .build-stamps/ to force a full rebuild on the next redeploy
make dev-coordinator            Run coordinator locally with --reload
make dev-frontend               Run the Vite dev server
make test                       Run pytest (fails fast with -x)
make lint                       ruff check
make format                     ruff format
make sync                       uv sync --all-extras
make kind-e2e-up                Bootstrap kind cluster + helm install + seed
make kind-e2e-pytest            Run backend live tests against the kind cluster
make kind-e2e-playwright        Run @live Playwright specs (assumes port-forward :8080)
make kind-e2e-playwright-all    One-shot: bootstrap (if needed) + port-forward + @live specs
make kind-e2e-down              Delete the kind cluster
```

---

## License

Internal research project.
