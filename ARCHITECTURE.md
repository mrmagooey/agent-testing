# AI/LLM Security Code Review Testing Framework — Architecture Document

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Core Abstractions and Interfaces](#2-core-abstractions-and-interfaces)
3. [Directory and Module Structure](#3-directory-and-module-structure)
4. [Data Models](#4-data-models)
5. [Experiment Runner Flow](#5-experiment-runner-flow)
6. [Ground Truth Management](#6-ground-truth-management)
7. [Strategy Implementations](#7-strategy-implementations)
8. [Verification Pass](#8-verification-pass)
9. [Evaluation Pipeline](#9-evaluation-pipeline)
10. [Report Generation](#10-report-generation)
11. [Cost Modeling](#11-cost-modeling)
12. [Feedback Loop](#12-feedback-loop)
13. [Exfiltration Risk Documentation](#13-exfiltration-risk-documentation)
14. [Configuration Format](#14-configuration-format)
15. [Deployment Architecture](#15-deployment-architecture)
16. [Web Frontend](#16-web-frontend)
17. [Extension Points](#17-extension-points)
18. [Tool Extensions (MCP-backed)](#18-tool-extensions-mcp-backed)
19. [Future Work: Iterative Review](#19-future-work-iterative-review)

---

## 1. System Overview

### System Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  COORDINATOR SERVICE (K8s Deployment, 1 replica)                            │
│                                                                             │
│  Serves: React SPA at / + REST API at /api/*                               │
│  Single image, single port — web UI and API in one deployment              │
│                                                                             │
│  Experiments:                                                               │
│    POST /experiments                      — submit new experiment (config as body)   │
│    GET  /experiments                      — list all experiments                    │
│    GET  /experiments/{id}                 — status + progress                   │
│    GET  /experiments/{id}/results         — matrix report (JSON)               │
│    GET  /experiments/{id}/results/markdown — matrix report (Markdown)          │
│    GET  /experiments/{id}/runs            — list all runs                      │
│    GET  /experiments/{id}/runs/{run_id}   — single run result + findings       │
│    POST /experiments/{id}/cancel          — cancel remaining jobs              │
│    GET  /experiments/{id}/results/download — download reports as .zip          │
│    GET  /experiments/{id}/compare-runs    — side-by-side run comparison        │
│                                                                             │
│  Findings:                                                                  │
│    POST /experiments/{id}/runs/{run_id}/reclassify — reclassify a finding      │
│    GET  /experiments/{id}/runs/{run_id}/tool-audit — tool call audit report    │
│    GET  /experiments/{id}/findings/search — free-text search across findings   │
│                                                                             │
│  Datasets:                                                                  │
│    GET  /datasets                     — list available datasets            │
│    POST /datasets/discover-cves       — auto-discover CVEs by criteria     │
│    POST /datasets/resolve-cve         — resolve a CVE ID to repo+commit   │
│    POST /datasets/import-cve          — import (auto-resolve or full spec) │
│    POST /datasets/{name}/inject/preview — dry-run: show diff before inject │
│    POST /datasets/{name}/inject       — inject a vuln from template        │
│    GET  /datasets/{name}/labels       — view ground truth labels           │
│    GET  /datasets/{name}/tree         — repo file tree                    │
│    GET  /datasets/{name}/file         — read file content (syntax-ready)  │
│                                                                             │
│  Feedback:                                                                  │
│    POST /feedback/compare             — compare two experiments                │
│    GET  /feedback/patterns/{experiment_id} — FP patterns from an experiment           │
│                                                                             │
│  Reference:                                                                 │
│    GET  /models                       — list available model providers      │
│    GET  /strategies                   — list available strategies           │
│    GET  /profiles                     — list review profiles               │
│    GET  /templates                    — list vuln injection templates       │
│                                                                             │
│  Ops:                                                                       │
│    GET  /health                       — readiness/liveness                  │
│    GET  /metrics                      — Prometheus metrics                  │
│                                                                             │
│  Internals:                                                                 │
│    - Expands matrix → list[ExperimentRun]                                   │
│    - Creates one K8s Job per experiment run                                 │
│    - Enforces per-model concurrency caps                                    │
│    - Collects RunResults from shared storage                                │
│    - Generates matrix report when all jobs complete                         │
└──────────┬──────────────────────────────────────┬────────────────────────────┘
           │ creates K8s Jobs                     │ reads RunResults from
           │ (respecting concurrency caps)        │ shared storage
           ▼                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  EXPERIMENT WORKER PODS (one K8s Job per ExperimentRun)                     │
│                                                                             │
│  Each pod runs a single experiment in an isolated container:                │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │  Worker Container (sec-review-worker image)                           │  │
│  │                                                                       │  │
│  │  ExperimentWorker._run_single(run_config)                            │  │
│  │    → ModelProviderFactory.create()    → ModelProvider                 │  │
│  │    → StrategyFactory.create()         → ScanStrategy                 │  │
│  │    → ToolRegistryFactory.create()     → ToolRegistry                 │  │
│  │    → ProfileRegistry.get()            → ReviewProfile                │  │
│  │    → LabelStore.load()                → list[GroundTruthLabel]       │  │
│  │    → ScanStrategy.run(target, model, tools, config)                  │  │
│  │        → run_agentic_loop / run_subagents                            │  │
│  │    → [if verification] Verifier.verify(candidates, target, model)    │  │
│  │    → Evaluator.evaluate(findings, labels)                            │  │
│  │    → CostCalculator.compute(model_id, tokens)                        │  │
│  │    → Write RunResult to shared storage                               │  │
│  │                                                                       │  │
│  │  Mounts:                                                              │  │
│  │    - /data/datasets (ReadOnlyMany PV — target repos + labels)        │  │
│  │    - /data/outputs/{experiment_id}/{run_id} (ReadWriteOnce PV)     │  │
│  │  Env:                                                                 │  │
│  │    - API keys from K8s Secrets                                        │  │
│  │  Resources:                                                           │  │
│  │    - CPU/memory limits per pod                                        │  │
│  │    - Deadline: activeDeadlineSeconds on the Job                       │  │
│  │  Network:                                                             │  │
│  │    - NetworkPolicy: egress only to LLM provider endpoints             │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  SHARED STORAGE (PersistentVolume — NFS or EFS)                             │
│                                                                             │
│  /data/                                                                     │
│  ├── datasets/                  (ReadOnlyMany — mounted by all workers)    │
│  │   └── targets/{name}/repo, labels.jsonl, diff_spec.yaml                 │
│  ├── config/                    (ReadOnlyMany — runtime configuration)      │
│  │   ├── concurrency.yaml                                                   │
│  │   ├── coordinator.yaml                                                   │
│  │   ├── experiments.yaml                                                   │
│  │   ├── pricing.yaml                                                       │
│  │   ├── retry.yaml                                                         │
│  │   ├── review_profiles.yaml                                               │
│  │   └── vuln_classes.yaml                                                  │
│  └── outputs/                   (ReadWriteMany — workers write, coord reads)│
│      └── {experiment_id}/                                                        │
│          ├── {run_id}/   (one dir per job)                           │
│          │   ├── run_result.json                                            │
│          │   ├── findings.jsonl                                             │
│          │   ├── tool_calls.jsonl                                           │
│          │   └── report.md / report.json                                    │
│          ├── matrix_report.md   (written by coordinator after all complete) │
│          └── matrix_report.json                                             │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Key Design Principles

**Experiment as first-class object.** Every combination of active dimensions is a fully specified `ExperimentRun` with its own output directory, findings file, and evaluation result. Nothing is inferred at report time.

**Post-hoc merging only.** No coordinating agent runs during strategy execution. All strategies emit a flat `list[Finding]` when complete. The evaluator and reporter operate identically regardless of which strategy produced those findings.

**Tool access as a dimension, not a feature flag.** The `tool_variant` field is part of the experiment identity. Strategies receive a `ToolRegistry` that either contains `SemgrepTool` or does not, based on the variant. No strategy-level branching on this.

**Ground truth is mutable but versioned.** Labels are stored in JSONL with a dataset version identifier. CVE imports and injected vulns both land in the same label format. The "unlabeled real vuln" bucket is a first-class output field.

**Verification is distinct from evaluation.** Verification is a pipeline stage that filters candidate findings before they become final — it improves precision. Evaluation scores the final output against ground truth — it measures precision. Verification is part of the system under test; evaluation is the test harness.

**Dimensions are opt-in.** Not every experiment needs to vary every dimension. The matrix expands only the dimensions listed in the config. An experiment testing review profiles might fix the model and strategy and only vary profiles. This keeps run counts manageable.

---

## 2. Core Abstractions and Interfaces

### 2.1 ModelProvider

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str
    tool_call_id: str | None = None

@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema

@dataclass
class ModelResponse:
    content: str
    tool_calls: list[dict[str, Any]]  # [{name, id, input}]
    input_tokens: int
    output_tokens: int
    model_id: str
    raw: dict[str, Any]  # full provider response, for debugging

@dataclass
class RetryPolicy:
    """Per-provider retry configuration. Loaded from config/retry.yaml."""
    max_retries: int = 3
    base_delay: float = 1.0          # seconds
    max_delay: float = 60.0          # seconds
    jitter: bool = True              # randomize delay to avoid thundering herd
    retryable_status_codes: list[int] = field(
        default_factory=lambda: [429, 529, 503]
    )

    def compute_delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            delay = retry_after          # honour provider's Retry-After header
        else:
            delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        if self.jitter:
            delay *= (0.5 + random.random() * 0.5)
        return delay

class ModelProvider(ABC):
    """Unified interface over any LLM API. Includes retry wrapping."""

    def __init__(self, retry_policy: RetryPolicy | None = None):
        self.retry_policy = retry_policy or RetryPolicy()
        self.token_log: list[ModelResponse] = []

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system_prompt: str | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.2,
    ) -> ModelResponse:
        """Calls _do_complete() with retry logic around provider rate limits."""
        policy = self.retry_policy
        last_exc: Exception | None = None

        for attempt in range(policy.max_retries + 1):
            try:
                response = self._do_complete(
                    messages, tools, system_prompt, max_tokens, temperature
                )
                self.token_log.append(response)
                return response
            except ProviderRateLimitError as exc:
                last_exc = exc
                if attempt == policy.max_retries:
                    break
                delay = policy.compute_delay(attempt, retry_after=exc.retry_after)
                time.sleep(delay)
            except ProviderError as exc:
                if exc.status_code not in policy.retryable_status_codes:
                    raise
                last_exc = exc
                if attempt == policy.max_retries:
                    break
                delay = policy.compute_delay(attempt)
                time.sleep(delay)

        raise MaxRetriesExceeded(
            f"Provider failed after {policy.max_retries} retries"
        ) from last_exc

    @abstractmethod
    def _do_complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        """Provider-specific implementation. Called by complete() with retry wrapping."""
        ...

    @abstractmethod
    def model_id(self) -> str:
        """Canonical identifier used in experiment records."""
        ...

    def supports_tools(self) -> bool:
        """Override to False for providers that don't support function calling."""
        return True
```

The five concrete providers (`OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`, `MistralProvider`, `CohereProvider`) each implement `_do_complete()`. The base class handles retry logic, token logging, and rate limit backoff. Retry policies are loaded from `config/retry.yaml` and differ per provider (e.g., Anthropic uses 529 for overload, OpenAI returns Retry-After headers on 429).

### 2.2 Strategy

```python
from abc import ABC, abstractmethod

class ScanStrategy(ABC):
    """
    A strategy receives a target codebase and a tool registry,
    and produces a flat list of findings.

    Strategies are stateless — all context comes in via arguments.
    The runner instantiates a fresh strategy per experiment run.
    """

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def run(
        self,
        target: "TargetCodebase",
        model: ModelProvider,
        tools: "ToolRegistry",
        config: "StrategyConfig",
    ) -> "StrategyOutput":
        ...
```

The critical constraint: strategies do not receive the ground truth labels. They operate as a real scanner would — against the source code only.

### 2.3 ToolRegistry

```python
from dataclasses import dataclass, field

@dataclass
class ToolRegistry:
    tools: dict[str, "Tool"] = field(default_factory=dict)
    audit_log: "ToolCallAuditLog" = field(default_factory=lambda: ToolCallAuditLog())

    def get_tool_definitions(self) -> list[ToolDefinition]:
        return [t.definition() for t in self.tools.values()]

    def invoke(self, name: str, input: dict[str, Any], call_id: str) -> str:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")
        self.audit_log.record(name, input, call_id)
        return tool.invoke(input)

class Tool(ABC):
    @abstractmethod
    def definition(self) -> ToolDefinition: ...
    @abstractmethod
    def invoke(self, input: dict[str, Any]) -> str: ...
```

### 2.4 Verifier

```python
class Verifier(ABC):
    """
    Receives candidate findings from a strategy and verifies each one
    against the source code. Returns verified findings (with evidence)
    and rejected findings (with rejection reason).

    This is a pipeline stage, not an evaluator — it runs BEFORE
    findings are scored against ground truth.
    """

    @abstractmethod
    def verify(
        self,
        candidates: list["Finding"],
        target: "TargetCodebase",
        model: ModelProvider,
        tools: "ToolRegistry",
    ) -> "VerificationResult":
        ...
```

### 2.5 Evaluator

```python
class Evaluator(ABC):
    @abstractmethod
    def evaluate(
        self,
        findings: list["Finding"],
        labels: list["GroundTruthLabel"],
    ) -> "EvaluationResult":
        ...
```

Only one concrete implementation is needed initially: `FileLevelEvaluator`. It matches findings to labels by file path, then computes precision, recall, and false positive rate. Line-level matching is stored as metadata but does not affect primary metrics.

### 2.6 ReportGenerator

```python
class ReportGenerator(ABC):
    @abstractmethod
    def render_run(self, result: "RunResult", output_dir: Path) -> None:
        """Write markdown and JSON for a single experiment run."""
        ...

    @abstractmethod
    def render_matrix(self, results: list["RunResult"], output_dir: Path) -> None:
        """Write the comparative matrix across all runs in an experiment."""
        ...
```

### 2.7 CostCalculator

```python
class CostCalculator:
    """Maps token usage to estimated dollar cost per model."""

    def __init__(self, pricing: dict[str, "ModelPricing"]):
        self.pricing = pricing

    def compute(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        p = self.pricing[model_id]
        return (input_tokens / 1_000_000 * p.input_per_million
                + output_tokens / 1_000_000 * p.output_per_million)
```

### 2.8 ReviewProfile

```python
class ReviewProfileName(str, Enum):
    DEFAULT = "default"
    STRICT = "strict"
    COMPREHENSIVE = "comprehensive"
    OWASP_FOCUSED = "owasp_focused"
    QUICK_SCAN = "quick_scan"

class ReviewProfile(BaseModel):
    """Pre-canned review profile injected into strategy system prompts."""
    name: ReviewProfileName
    description: str
    system_prompt_modifier: str  # injected into all strategy system prompts
```

See [Section 14.4](#144-review-profiles) for the full profile definitions.

---

## 3. Directory and Module Structure

```
agent-testing-framework/
│
├── pyproject.toml
├── ARCHITECTURE.md
├── Dockerfile.worker             # worker container image
├── Dockerfile.coordinator        # coordinator + frontend image (multi-stage build)
├── Dockerfile.dataset-builder    # dataset builder image (git + import tools)
│
├── frontend/                     # React SPA — lightweight web UI
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── pages/                # Dashboard, ExperimentNew, ExperimentDetail, CVEDiscovery, etc.
│   │   ├── components/           # MatrixTable, FindingsExplorer, CostEstimate, etc.
│   │   ├── api/                  # auto-generated typed client from OpenAPI spec
│   │   └── hooks/                # useExperiment (polling), useEstimate (debounced)
│   └── dist/                     # built output, bundled into coordinator image
│
├── helm/sec-review/              # Helm chart for Kubernetes deployment
│   ├── Chart.yaml
│   ├── values.yaml               # default values (images, replicas, egress CIDRs, etc.)
│   └── templates/                # coordinator, worker, PVC, network-policy, secrets, etc.
│
├── config/
│   ├── experiments.yaml          # primary experiment definition
│   ├── concurrency.yaml          # per-model concurrency caps
│   ├── retry.yaml                # per-provider retry policies
│   ├── pricing.yaml              # per-model token pricing for cost estimates
│   ├── coordinator.yaml          # retention, cleanup, job TTL settings
│   ├── review_profiles.yaml      # pre-canned review profile definitions
│   ├── vuln_classes.yaml         # canonical vulnerability class definitions
│   └── prompts/                  # versioned prompt snapshots (auto-populated)
│       └── {strategy}/{snapshot_id}.yaml
│
├── datasets/
│   ├── targets/                  # checked-out target codebases (gitignored)
│   │   └── {dataset_name}/
│   │       ├── repo/             # the actual source code
│   │       ├── labels.jsonl      # ground truth labels for this dataset
│   │       └── diff_spec.yaml    # optional: base_ref + head_ref for diff-mode
│   ├── templates/                # vuln injection templates
│   │   ├── sqli/
│   │   ├── ssrf/
│   │   ├── crypto_misuse/
│   │   └── ...
│   └── cve_imports/              # raw CVE metadata before normalization
│
├── outputs/                      # on shared PV in production
│   └── {experiment_id}/
│       ├── matrix_report.md
│       ├── matrix_report.json
│       ├── feedback/             # cross-experiment feedback analysis
│       │   └── experiment_comparison.json
│       └── {run_id}/
│           ├── findings.jsonl
│           ├── findings_pre_verification.jsonl
│           ├── verification_log.jsonl
│           ├── tool_calls.jsonl
│           ├── run_result.json
│           ├── report.md
│           └── report.json
│
├── src/
│   └── sec_review_framework/
│       │
│       ├── __init__.py
│       ├── worker.py             # ExperimentWorker — K8s Job entry point
│       ├── coordinator.py        # ExperimentCoordinator + FastAPI app (HTTP API)
│       ├── config.py             # Pydantic config loaders
│       │
│       ├── models/               # ModelProvider implementations
│       │   ├── __init__.py
│       │   ├── base.py           # ModelProvider ABC, Message, ModelResponse
│       │   ├── openai.py
│       │   ├── anthropic.py
│       │   ├── gemini.py
│       │   ├── mistral.py
│       │   └── cohere.py
│       │
│       ├── strategies/           # ScanStrategy implementations
│       │   ├── __init__.py
│       │   ├── base.py           # ScanStrategy ABC, StrategyOutput
│       │   ├── common.py         # run_agentic_loop, FindingParser, dedup utils
│       │   ├── single_agent.py
│       │   ├── per_file.py
│       │   ├── per_vuln_class.py
│       │   ├── sast_first.py
│       │   └── diff_review.py
│       │
│       ├── verification/         # Verification pass
│       │   ├── __init__.py
│       │   └── verifier.py       # Verifier ABC, LLMVerifier
│       │
│       ├── tools/                # Tool implementations + registry
│       │   ├── __init__.py
│       │   ├── registry.py       # ToolRegistry, ToolCallAuditLog, factory
│       │   ├── repo_access.py    # file read, directory list, grep
│       │   ├── semgrep.py        # Semgrep wrapper
│       │   └── doc_lookup.py     # documentation retrieval
│       │
│       ├── ground_truth/
│       │   ├── __init__.py
│       │   ├── models.py         # GroundTruthLabel, TargetCodebase, LabelStore
│       │   ├── cve_importer.py   # CVEImporter
│       │   ├── vuln_injector.py  # VulnInjector, InjectionTemplate
│       │   └── diff_generator.py # DiffDatasetGenerator
│       │
│       ├── evaluation/
│       │   ├── __init__.py
│       │   ├── evaluator.py      # Evaluator ABC, FileLevelEvaluator (bipartite matching)
│       │   ├── metrics.py        # precision/recall/FP computation
│       │   ├── evidence.py       # EvidenceQualityAssessor + LLMEvidenceAssessor
│       │   └── statistics.py     # StatisticalAnalyzer, wilson_ci, mcnemar_test
│       │
│       ├── reporting/
│       │   ├── __init__.py
│       │   ├── generator.py      # ReportGenerator ABC
│       │   ├── markdown.py       # MarkdownReportGenerator
│       │   └── json_report.py    # JSONReportGenerator
│       │
│       ├── cost/
│       │   ├── __init__.py
│       │   └── calculator.py     # CostCalculator, ModelPricing
│       │
│       ├── feedback/
│       │   ├── __init__.py
│       │   └── tracker.py        # FeedbackTracker, cross-experiment analysis
│       │
│       ├── profiles/
│       │   ├── __init__.py
│       │   └── review_profiles.py  # ReviewProfile, BUILTIN_PROFILES
│       │
│       ├── prompts/
│       │   ├── __init__.py
│       │   └── registry.py       # PromptSnapshot, PromptRegistry
│       │
│       └── data/                 # Shared Pydantic/dataclass models
│           ├── __init__.py
│           ├── findings.py       # Finding, FindingIdentity, Severity, VulnClass
│           ├── experiment.py     # ExperimentRun, RunResult, ExperimentMatrix
│           └── evaluation.py     # EvaluationResult, MatchedFinding
│
└── tests/
    ├── unit/
    │   ├── test_evaluator.py
    │   ├── test_verifier.py
    │   ├── test_evidence_quality.py
    │   ├── test_cost_calculator.py
    │   ├── test_vuln_injector.py
    │   └── test_label_store.py
    └── integration/
        ├── test_single_agent_strategy.py
        └── test_full_run.py      # smoke test with a tiny synthetic target
```

---

## 4. Data Models

### 4.1 PromptSnapshot

Every experiment run captures the exact prompts used, so experiment comparisons can distinguish "prompt changed" from "model behaved differently."

```python
import hashlib

class PromptSnapshot(BaseModel):
    """Immutable record of every prompt surface used in a single experiment run."""
    snapshot_id: str              # sha256[:16] of all fields concatenated
    captured_at: datetime
    system_prompt: str
    user_message_template: str
    review_profile_modifier: str | None = None
    finding_output_format: str
    verification_prompt: str | None = None

    @classmethod
    def capture(cls, **kwargs) -> "PromptSnapshot":
        blob = "".join(str(v) for v in kwargs.values() if v)
        snap_id = hashlib.sha256(blob.encode()).hexdigest()[:16]
        return cls(snapshot_id=snap_id, captured_at=datetime.utcnow(), **kwargs)
```

Strategies call `PromptSnapshot.capture()` at the start of `run()` and attach it to the `RunResult`. The `PromptRegistry` persists snapshots to `config/prompts/{name}/{snapshot_id}.yaml` for version tracking across experiments.

### 4.2 FindingIdentity

Defines what makes "the same finding" across runs for cross-experiment comparison and stability measurement.

```python
from typing import NamedTuple

LINE_BUCKET_SIZE = 10

class FindingIdentity(NamedTuple):
    """
    Canonical key for deduplication and cross-run matching.
    Line numbers are bucketed to 10-line windows so minor formatting
    changes don't produce false negatives.
    """
    file_path: str
    vuln_class: str
    line_bucket: int        # floor(line_start / LINE_BUCKET_SIZE)

    @classmethod
    def from_finding(cls, finding: "Finding") -> "FindingIdentity":
        bucket = (finding.line_start or 0) // LINE_BUCKET_SIZE
        return cls(
            file_path=finding.file_path,
            vuln_class=finding.vuln_class,
            line_bucket=bucket,
        )

    def __str__(self) -> str:
        lo = self.line_bucket * LINE_BUCKET_SIZE
        return f"{self.file_path}:{self.vuln_class}:L{lo}-{lo + LINE_BUCKET_SIZE - 1}"
```

Used by `FeedbackTracker` to match findings across experiments: two findings from different runs refer to the same underlying issue if their `FindingIdentity` matches.

### 4.3 Finding

```python
from pydantic import BaseModel
from enum import Enum
from datetime import datetime

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class VulnClass(str, Enum):
    SQLI = "sqli"
    XSS = "xss"
    SSRF = "ssrf"
    RCE = "rce"
    IDOR = "idor"
    AUTH_BYPASS = "auth_bypass"
    CRYPTO_MISUSE = "crypto_misuse"
    HARDCODED_SECRET = "hardcoded_secret"
    PATH_TRAVERSAL = "path_traversal"
    SUPPLY_CHAIN = "supply_chain"
    MEMORY_SAFETY = "memory_safety"
    LOGIC_BUG = "logic_bug"
    DESERIALIZATION = "deserialization"
    XXE = "xxe"
    OPEN_REDIRECT = "open_redirect"
    OTHER = "other"

class Finding(BaseModel):
    id: str                        # uuid4, generated at finding creation
    file_path: str                 # relative to repo root
    line_start: int | None = None
    line_end: int | None = None
    vuln_class: VulnClass
    cwe_ids: list[str] = []        # e.g. ["CWE-89"]
    severity: Severity
    title: str                     # one-line description
    description: str               # full LLM explanation, preserved verbatim
    recommendation: str | None = None
    confidence: float              # 0.0-1.0, self-reported by model
    raw_llm_output: str            # verbatim LLM text this finding was extracted from
    produced_by: str               # strategy name + subagent id if applicable
    experiment_id: str

    # Verification fields — populated by the verification pass
    verified: bool | None = None           # None = not verified, True/False = result
    verification_evidence: str | None = None  # verifier's reasoning, verbatim
    verification_rejected_reason: str | None = None  # why it was rejected, if applicable
```

`description` and `raw_llm_output` are preserved verbatim. Report generation renders them as-is.

### 4.4 Ground Truth Label

```python
class GroundTruthSource(str, Enum):
    CVE_PATCH = "cve_patch"        # extracted from a CVE before-fix state
    INJECTED = "injected"          # added via VulnInjector from a template
    MANUAL = "manual"              # hand-labeled by a team member

class GroundTruthLabel(BaseModel):
    id: str
    dataset_version: str           # semver string, e.g. "1.2.0"
    file_path: str                 # relative to repo root
    line_start: int
    line_end: int
    cwe_id: str
    vuln_class: VulnClass
    severity: Severity
    description: str               # human-readable description of the vuln
    source: GroundTruthSource
    source_ref: str | None = None  # CVE ID, template name, or author initials
    confidence: str                # "confirmed" | "likely" | "suspected"
    created_at: datetime
    notes: str | None = None
    introduced_in_diff: bool | None = None  # for diff-mode datasets: was this vuln in the diff?
```

Labels are stored in `datasets/targets/{dataset_name}/labels.jsonl`, one JSON object per line.

### 4.5 Strategy Output

```python
class StrategyOutput(BaseModel):
    """Returned by strategies — captures both findings and dedup metadata."""
    findings: list[Finding]
    pre_dedup_count: int           # how many findings before deduplication
    post_dedup_count: int          # how many after (= len(findings))
    dedup_log: list["DedupEntry"]  # which findings were merged and why

class DedupEntry(BaseModel):
    kept_finding_id: str
    merged_finding_ids: list[str]
    reason: str                    # e.g. "same file + vuln_class within 5 lines"
```

Strategies that don't deduplicate (SingleAgent, SASTFirst) return `pre_dedup_count == post_dedup_count` with an empty `dedup_log`.

### 4.6 Verification Result

```python
class VerificationOutcome(str, Enum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"        # verifier could not determine

class VerifiedFinding(BaseModel):
    finding: Finding               # original finding, with verification fields populated
    outcome: VerificationOutcome
    evidence: str                  # verifier's full reasoning
    cited_lines: list[str]         # file:line references the verifier cited

class VerificationResult(BaseModel):
    verified: list[VerifiedFinding]
    rejected: list[VerifiedFinding]
    uncertain: list[VerifiedFinding]
    total_candidates: int
    verification_tokens: int       # tokens used by the verification pass
```

### 4.7 Experiment Configuration and Run

```python
class ToolVariant(str, Enum):
    WITH_TOOLS = "with_tools"
    WITHOUT_TOOLS = "without_tools"

class VerificationVariant(str, Enum):
    NONE = "none"
    WITH_VERIFICATION = "with_verification"

class StrategyName(str, Enum):
    SINGLE_AGENT = "single_agent"
    PER_FILE = "per_file"
    PER_VULN_CLASS = "per_vuln_class"
    SAST_FIRST = "sast_first"
    DIFF_REVIEW = "diff_review"

class ExperimentRun(BaseModel):
    """One cell in the experiment matrix."""
    id: str
    experiment_id: str
    model_id: str
    strategy: StrategyName
    tool_variant: ToolVariant
    review_profile: "ReviewProfileName"
    verification_variant: VerificationVariant
    verifier_model_id: str | None = None  # if None, uses model_id for verification
    dataset_name: str
    dataset_version: str
    strategy_config: dict          # strategy-specific params
    model_config: dict             # temperature, max_tokens, etc.
    parallel: bool = False         # whether subagents run in parallel
    repetition_index: int = 0     # which repetition this is (0-indexed)
    created_at: datetime | None = None

    @property
    def effective_verifier_model(self) -> str:
        return self.verifier_model_id or self.model_id

class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class RunResult(BaseModel):
    experiment: ExperimentRun
    status: RunStatus
    findings: list[Finding]                    # final findings (post-verification if enabled)
    findings_pre_verification: list[Finding] | None  # candidates before verification
    verification_result: "VerificationResult | None"
    evaluation: "EvaluationResult | None"
    strategy_output: StrategyOutput            # includes dedup metrics
    prompt_snapshot: PromptSnapshot             # exact prompts used in this run

    # Token and cost tracking
    tool_call_count: int
    total_input_tokens: int
    total_output_tokens: int
    verification_tokens: int                   # tokens used by verification pass (0 if skipped)
    estimated_cost_usd: float                  # computed by CostCalculator

    duration_seconds: float
    error: str | None = None
    completed_at: datetime | None = None
```

### 4.8 Evaluation Result

```python
class MatchStatus(str, Enum):
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    UNLABELED_REAL = "unlabeled_real"  # team manually confirms post-hoc

class EvidenceQuality(str, Enum):
    """How good was the model's explanation for a true positive finding?"""
    STRONG = "strong"        # correct code cited, mechanism explained accurately
    ADEQUATE = "adequate"    # right area identified, explanation partially correct
    WEAK = "weak"            # right file but wrong reasoning or no code citation
    NOT_ASSESSED = "not_assessed"

class MatchedFinding(BaseModel):
    finding: Finding
    matched_label: GroundTruthLabel | None
    match_status: MatchStatus
    file_match: bool
    line_overlap: bool             # informational only
    evidence_quality: EvidenceQuality = EvidenceQuality.NOT_ASSESSED

class EvaluationResult(BaseModel):
    experiment_id: str
    dataset_version: str
    total_labels: int
    total_findings: int
    true_positives: int
    false_positives: int
    false_negatives: int           # labels with no matching finding
    unlabeled_real_count: int      # findings flagged as real but unlabeled
    precision: float               # TP / (TP + FP), excludes unlabeled_real
    recall: float                  # TP / (TP + FN)
    f1: float
    false_positive_rate: float
    matched_findings: list[MatchedFinding]
    unmatched_labels: list[GroundTruthLabel]

    # Evidence quality breakdown (for true positives only)
    evidence_quality_counts: dict[str, int]  # {"strong": 5, "adequate": 3, "weak": 1}
```

Note on `unlabeled_real`: these are findings where a human reviewer determines post-hoc that the model found a genuine vulnerability not in the ground truth. They are excluded from false positive counts to avoid penalizing models that find real things. Tracking them separately surfaces ground truth gaps.

---

## 5. Experiment Runner Flow

### 5.1 ExperimentMatrix

```python
class ExperimentMatrix(BaseModel):
    experiment_id: str
    dataset_name: str
    dataset_version: str

    # Dimensions — each is a list. The matrix is their cartesian product.
    # Omit or use a single-element list to hold a dimension constant.
    model_ids: list[str]
    strategies: list[StrategyName]
    tool_variants: list[ToolVariant] = [ToolVariant.WITH_TOOLS, ToolVariant.WITHOUT_TOOLS]
    review_profiles: list[ReviewProfileName] = [ReviewProfileName.DEFAULT]
    verification_variants: list[VerificationVariant] = [VerificationVariant.NONE, VerificationVariant.WITH_VERIFICATION]
    parallel_modes: list[bool] = [False]

    strategy_configs: dict[str, dict]
    model_configs: dict[str, dict]

    # Repetitions — run each cell N times for statistical significance
    num_repetitions: int = 1

    # Spend control — coordinator cancels remaining jobs when exceeded
    max_experiment_cost_usd: float | None = None

    # Cross-model verification — optional, defaults to same model
    verifier_model_id: str | None = None

    def expand(self) -> list[ExperimentRun]:
        """
        Cartesian product of all active dimensions × num_repetitions.

        Default: 5 models × 5 strategies × 2 tool variants × 2 verification = 100 runs
        With 3 repetitions: 300 runs.
        Use single-element lists to fix dimensions you don't want to vary.
        """
        runs = []
        for model_id in self.model_ids:
            for strategy in self.strategies:
                for tool_variant in self.tool_variants:
                    for profile in self.review_profiles:
                        for verif in self.verification_variants:
                            for parallel in self.parallel_modes:
                                for rep in range(self.num_repetitions):
                                    rep_suffix = f"_rep{rep}" if self.num_repetitions > 1 else ""
                                    run_id = (
                                        f"{self.experiment_id}_{model_id}_{strategy}"
                                        f"_{tool_variant}_{profile}_{verif}{rep_suffix}"
                                    )
                                    runs.append(ExperimentRun(
                                        id=run_id,
                                        experiment_id=self.experiment_id,
                                        model_id=model_id,
                                        strategy=strategy,
                                        tool_variant=tool_variant,
                                        review_profile=profile,
                                        verification_variant=verif,
                                        verifier_model_id=self.verifier_model_id,
                                        parallel=parallel,
                                        repetition_index=rep,
                                        dataset_name=self.dataset_name,
                                        dataset_version=self.dataset_version,
                                        strategy_config=self.strategy_configs.get(strategy, {}),
                                        model_config=self.model_configs.get(model_id, {}),
                                ))
        return runs
```

### 5.2 Coordinator Service (ExperimentCoordinator)

The coordinator is a FastAPI service running as a single-replica K8s Deployment. It is the sole management interface for the framework — all operations (submitting experiments, managing datasets, viewing results, reclassifying findings) happen through its HTTP API. There is no CLI; users interact via `curl`, a browser, or any HTTP client.

```python
class ExperimentCoordinator:
    """
    Manages the lifecycle of an experiment:
    1. Receives an experiment submission (ExperimentMatrix + config)
    2. Expands the matrix into ExperimentRuns
    3. Creates K8s Jobs, respecting per-model concurrency caps
    4. Monitors job completion, collects results from shared storage
    5. Generates the matrix report when all jobs finish
    """

    def __init__(
        self,
        k8s_client: "kubernetes.client.BatchV1Api",
        storage_root: Path,              # shared NFS/EFS mount
        concurrency_caps: dict[str, int],  # model_id → max concurrent jobs
        worker_image: str,               # container image for worker pods
        worker_resources: "ResourceSpec", # CPU/memory limits
        reporter: ReportGenerator,
        namespace: str = "sec-review",
    ):
        ...

    def submit_experiment(self, matrix: ExperimentMatrix) -> str:
        """
        Expand matrix, persist experiment metadata, start scheduling jobs.
        Returns experiment_id for tracking.
        """
        runs = matrix.expand()
        experiment_id = matrix.experiment_id
        self._persist_experiment_metadata(experiment_id, runs)

        # Start the scheduling loop (runs in background)
        self._schedule_jobs(experiment_id, runs)
        return experiment_id

    def _schedule_jobs(self, experiment_id: str, runs: list[ExperimentRun]) -> None:
        """
        Schedules K8s Jobs respecting per-model concurrency caps.

        Uses a simple scheduling loop:
        1. Check how many jobs are currently running per model
        2. For each model under its cap, create the next pending job
        3. Sleep, repeat until all runs are scheduled

        This avoids flooding a single provider's API with all runs at once.
        """
        pending = list(runs)
        while pending:
            running_by_model = self._count_running_by_model(experiment_id)

            scheduled_this_round = []
            for run in pending:
                cap = self.concurrency_caps.get(run.model_id, self.default_cap)
                current = running_by_model.get(run.model_id, 0)
                if current < cap:
                    self._create_k8s_job(experiment_id, run)
                    running_by_model[run.model_id] = current + 1
                    scheduled_this_round.append(run)

            for run in scheduled_this_round:
                pending.remove(run)

            if pending:
                time.sleep(10)  # poll interval

    def _create_k8s_job(self, experiment_id: str, run: ExperimentRun) -> None:
        """
        Creates a K8s Job that runs a single experiment.
        The job spec is a single container running:
            python -m sec_review_framework.worker --run-config /data/config/run.json
        """
        job = kubernetes.client.V1Job(
            metadata=kubernetes.client.V1ObjectMeta(
                name=f"exp-{run.id[:50]}",
                namespace=self.namespace,
                labels={
                    "app": "sec-review-worker",
                    "experiment": experiment_id,
                    "model": run.model_id,
                    "strategy": run.strategy,
                },
            ),
            spec=kubernetes.client.V1JobSpec(
                backoff_limit=1,             # retry once on failure
                active_deadline_seconds=1800, # 30 min hard timeout
                template=kubernetes.client.V1PodTemplateSpec(
                    spec=kubernetes.client.V1PodSpec(
                        restart_policy="Never",
                        containers=[kubernetes.client.V1Container(
                            name="worker",
                            image=self.worker_image,
                            command=[
                                "python", "-m", "sec_review_framework.worker",
                                "--run-config", f"/data/config/runs/{run.id}.json",
                                "--output-dir", f"/data/outputs/{experiment_id}/{run.id}",
                                "--datasets-dir", "/data/datasets",
                            ],
                            resources=self.worker_resources.to_k8s(),
                            env_from=[kubernetes.client.V1EnvFromSource(
                                secret_ref=kubernetes.client.V1SecretEnvSource(
                                    name="llm-api-keys"
                                )
                            )],
                            volume_mounts=[
                                kubernetes.client.V1VolumeMount(
                                    name="shared-data",
                                    mount_path="/data",
                                ),
                            ],
                        )],
                        volumes=[
                            kubernetes.client.V1Volume(
                                name="shared-data",
                                persistent_volume_claim=
                                    kubernetes.client.V1PersistentVolumeClaimVolumeSource(
                                        claim_name="sec-review-data"
                                    ),
                            ),
                        ],
                    ),
                ),
            ),
        )
        self.k8s_client.create_namespaced_job(self.namespace, job)

    def get_experiment_status(self, experiment_id: str) -> "ExperimentStatus":
        """
        Polls K8s for job statuses and reads completed RunResults from storage.
        """
        jobs = self.k8s_client.list_namespaced_job(
            self.namespace, label_selector=f"experiment={experiment_id}"
        )
        completed = 0
        failed = 0
        running = 0
        pending = 0
        for job in jobs.items:
            if job.status.succeeded:
                completed += 1
            elif job.status.failed:
                failed += 1
            elif job.status.active:
                running += 1
            else:
                pending += 1

        total = completed + failed + running + pending
        return ExperimentStatus(
            experiment_id=experiment_id,
            total=total,
            completed=completed,
            failed=failed,
            running=running,
            pending=pending,
        )

    def collect_results(self, experiment_id: str) -> list[RunResult]:
        """
        Reads all run_result.json files from shared storage for this experiment.
        Called when all jobs are complete.
        """
        results = []
        experiment_dir = self.storage_root / "outputs" / experiment_id
        for run_dir in experiment_dir.iterdir():
            result_file = run_dir / "run_result.json"
            if result_file.exists():
                results.append(RunResult.model_validate_json(result_file.read_text()))
        return results

    def finalize_experiment(self, experiment_id: str) -> None:
        """
        Called when all jobs complete. Collects results and generates matrix report.
        """
        results = self.collect_results(experiment_id)
        output_dir = self.storage_root / "outputs" / experiment_id
        self.reporter.render_matrix(results, output_dir)
```

#### FastAPI Application

```python
# src/sec_review_framework/coordinator.py

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Security Review Framework", version="1.0.0")
coordinator: ExperimentCoordinator = None  # initialized at startup

# --- Experiments ---

@app.post("/experiments", status_code=201)
def submit_experiment(matrix: ExperimentMatrix) -> dict:
    """Submit a new experiment. Returns experiment_id."""
    experiment_id = coordinator.submit_experiment(matrix)
    return {"experiment_id": experiment_id, "total_runs": len(matrix.expand())}

@app.get("/experiments")
def list_experiments() -> list[dict]:
    """List all experiments with summary status."""
    return coordinator.list_experiments()

@app.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str) -> dict:
    """Experiment status: total/completed/failed/running/pending counts, cost so far."""
    return coordinator.get_experiment_status(experiment_id).model_dump()

@app.get("/experiments/{experiment_id}/results")
def get_results_json(experiment_id: str) -> dict:
    """Matrix report as JSON. Available after experiment completes."""
    return coordinator.get_matrix_report_json(experiment_id)

@app.get("/experiments/{experiment_id}/results/markdown")
def get_results_markdown(experiment_id: str) -> str:
    """Matrix report as Markdown. Available after experiment completes."""
    return coordinator.get_matrix_report_markdown(experiment_id)

@app.get("/experiments/{experiment_id}/runs")
def list_runs(experiment_id: str) -> list[dict]:
    """List all runs in an experiment with status and summary metrics."""
    return coordinator.list_runs(experiment_id)

@app.get("/experiments/{experiment_id}/runs/{run_id}")
def get_run(experiment_id: str, run_id: str) -> dict:
    """Full RunResult including findings, evaluation, and verification details."""
    return coordinator.get_run_result(experiment_id, run_id)

@app.post("/experiments/{experiment_id}/cancel")
def cancel_experiment(experiment_id: str) -> dict:
    """Cancel pending jobs. Running jobs complete but no new ones are scheduled."""
    cancelled = coordinator.cancel_experiment(experiment_id)
    return {"cancelled_jobs": cancelled}

@app.get("/experiments/{experiment_id}/results/download")
def download_reports(experiment_id: str) -> FileResponse:
    """Download matrix_report.md, matrix_report.json, and all run reports as a .zip."""
    zip_path = coordinator.package_reports(experiment_id)
    return FileResponse(zip_path, media_type="application/zip",
                        filename=f"{experiment_id}_reports.zip")

@app.get("/experiments/{experiment_id}/compare-runs")
def compare_runs(experiment_id: str, run_a: str, run_b: str) -> dict:
    """
    Side-by-side comparison of two runs. Returns findings matched
    by FindingIdentity: which were found by both, only by A, only by B.
    """
    return coordinator.compare_runs(experiment_id, run_a, run_b)
    # Returns:
    # {
    #   "run_a": {"id": "...", "model": "...", "strategy": "...", "metrics": {...}},
    #   "run_b": {"id": "...", "model": "...", "strategy": "...", "metrics": {...}},
    #   "both": [{"identity": "src/auth.py:sqli:L40-49", "finding_a": {...}, "finding_b": {...}}],
    #   "only_a": [{"identity": "...", "finding": {...}}],
    #   "only_b": [{"identity": "...", "finding": {...}}],
    #   "metric_deltas": {"precision": +0.12, "recall": -0.05, "f1": +0.04}
    # }

# --- Findings ---

@app.get("/experiments/{experiment_id}/findings/search")
def search_findings(experiment_id: str, q: str, run_id: str | None = None) -> list[dict]:
    """
    Free-text search across all finding descriptions in an experiment.
    Searches: title, description, recommendation, raw_llm_output.
    Optional run_id filter to scope to a single run.
    """
    return coordinator.search_findings(experiment_id, q, run_id)

class ReclassifyRequest(BaseModel):
    finding_id: str
    status: str  # "unlabeled_real"
    note: str | None = None

@app.post("/experiments/{experiment_id}/runs/{run_id}/reclassify")
def reclassify_finding(experiment_id: str, run_id: str, req: ReclassifyRequest) -> dict:
    """Reclassify a false positive as unlabeled_real. Recomputes metrics."""
    return coordinator.reclassify_finding(experiment_id, run_id, req)

@app.get("/experiments/{experiment_id}/runs/{run_id}/tool-audit")
def tool_audit(experiment_id: str, run_id: str) -> dict:
    """Tool call audit: counts by tool, any calls with URL-like strings in inputs."""
    return coordinator.audit_tool_calls(experiment_id, run_id)

# --- Datasets ---

@app.get("/datasets")
def list_datasets() -> list[dict]:
    """List available target datasets with label counts."""
    return coordinator.list_datasets()

@app.post("/datasets/import-cve", status_code=201)
def import_cve(spec: CVEImportSpec) -> dict:
    """Import a CVE-patched repo as a ground truth dataset."""
    labels = coordinator.import_cve(spec)
    return {"dataset_name": spec.dataset_name, "labels_created": len(labels)}

@app.post("/datasets/{dataset_name}/inject/preview")
def preview_injection(dataset_name: str, req: "InjectionRequest") -> dict:
    """
    Dry-run injection: returns the unified diff and affected line range
    without modifying any files. Used by the frontend to show a preview
    before the user commits the injection.
    """
    return coordinator.preview_injection(dataset_name, req)
    # Returns:
    # {
    #   "template_id": "sqli_string_format_python",
    #   "target_file": "src/api/users.py",
    #   "anchor_line": 42,
    #   "unified_diff": "--- a/src/api/users.py\n+++ b/src/api/users.py\n@@ ...",
    #   "before_snippet": "lines 38-46 of original file",
    #   "after_snippet": "lines 38-49 after injection",
    #   "label_preview": {
    #     "file_path": "src/api/users.py",
    #     "line_start": 43,
    #     "line_end": 45,
    #     "vuln_class": "sqli",
    #     "cwe_id": "CWE-89",
    #     "severity": "high"
    #   }
    # }

@app.post("/datasets/{dataset_name}/inject", status_code=201)
def inject_vuln(dataset_name: str, req: "InjectionRequest") -> dict:
    """Inject a vulnerability from a template into a dataset."""
    label = coordinator.inject_vuln(dataset_name, req)
    return {"label_id": label.id, "file_path": label.file_path}

@app.get("/datasets/{dataset_name}/labels")
def get_labels(dataset_name: str, version: str | None = None) -> list[dict]:
    """View ground truth labels for a dataset."""
    return coordinator.get_labels(dataset_name, version)

@app.get("/datasets/{dataset_name}/tree")
def get_file_tree(dataset_name: str) -> dict:
    """Repo file tree. Returns nested structure: {name, type, children, size, language}."""
    return coordinator.get_file_tree(dataset_name)

@app.get("/datasets/{dataset_name}/file")
def get_file_content(dataset_name: str, path: str) -> dict:
    """
    Read a file from the dataset repo. Returns content with metadata
    for the frontend code viewer.
    """
    return coordinator.get_file_content(dataset_name, path)
    # Returns:
    # {
    #   "path": "src/api/users.py",
    #   "content": "...",
    #   "language": "python",       # detected from extension, for syntax highlighting
    #   "line_count": 142,
    #   "size_bytes": 4821,
    #   "labels": [...]             # ground truth labels in this file, if any
    # }

# --- Feedback ---

class CompareRequest(BaseModel):
    experiment_a_id: str
    experiment_b_id: str

@app.post("/feedback/compare")
def compare_experiments(req: CompareRequest) -> dict:
    """Compare two experiments: metric deltas, persistent FP patterns, regressions."""
    return coordinator.compare_experiments(req.experiment_a_id, req.experiment_b_id)

@app.get("/feedback/patterns/{experiment_id}")
def get_fp_patterns(experiment_id: str) -> list[dict]:
    """Extract recurring false positive patterns from an experiment."""
    return coordinator.get_fp_patterns(experiment_id)

# --- Reference ---

@app.get("/models")
def list_models() -> list[dict]:
    """List configured model providers."""
    return coordinator.list_models()

@app.get("/strategies")
def list_strategies() -> list[dict]:
    """List available scan strategies."""
    return coordinator.list_strategies()

@app.get("/profiles")
def list_profiles() -> list[dict]:
    """List available review profiles with descriptions."""
    return coordinator.list_profiles()

@app.get("/templates")
def list_templates() -> list[dict]:
    """List available vulnerability injection templates."""
    return coordinator.list_templates()

# --- Ops ---

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}

# --- Spend Controls ---

class EstimateRequest(BaseModel):
    matrix: ExperimentMatrix
    target_kloc: float            # estimated KLOC of target codebase

AVG_TOKENS_PER_KLOC = 1400       # prompt + completion, calibrated empirically

@app.post("/experiments/estimate")
def estimate_experiment(req: EstimateRequest) -> dict:
    """Pre-flight cost estimate. Call before submit to avoid expensive mistakes."""
    runs = req.matrix.expand()
    estimates_by_model = {}
    for run in runs:
        tokens = int(req.target_kloc * AVG_TOKENS_PER_KLOC)
        cost = coordinator.cost_calculator.compute(run.model_id, tokens, tokens // 3)
        estimates_by_model.setdefault(run.model_id, 0.0)
        estimates_by_model[run.model_id] += cost
    total = sum(estimates_by_model.values())
    return {
        "total_runs": len(runs),
        "estimated_cost_usd": round(total, 2),
        "by_model": {k: round(v, 2) for k, v in estimates_by_model.items()},
        "warning": "Estimates based on avg tokens/KLOC. Actual cost varies with strategy and model."
    }

# --- Retention ---

@app.delete("/experiments/{experiment_id}", status_code=204)
def delete_experiment(experiment_id: str):
    """Delete an experiment and all its outputs from shared storage."""
    coordinator.delete_experiment(experiment_id)
```

#### Spend Cap Enforcement

The coordinator tracks running cost per experiment and auto-cancels remaining jobs when the cap is reached.

```python
class ExperimentCostTracker:
    """Tracks running spend per experiment. Thread-safe."""

    def __init__(self, experiment_id: str, cap_usd: float | None):
        self.experiment_id = experiment_id
        self.cap_usd = cap_usd
        self._spent = 0.0
        self._lock = threading.Lock()
        self._cancelled = False

    def record_job_cost(self, cost_usd: float) -> bool:
        """Returns True if cap exceeded and experiment should halt."""
        with self._lock:
            self._spent += cost_usd
            if self.cap_usd and not self._cancelled and self._spent >= self.cap_usd:
                self._cancelled = True
                return True   # caller cancels remaining K8s jobs
        return False

    @property
    def spent_usd(self) -> float:
        return self._spent
```

When a worker completes, the coordinator reads `estimated_cost_usd` from its `run_result.json` and calls `record_job_cost()`. If the cap is hit, all pending Jobs for that experiment are deleted.

#### Reconciliation on Startup

```python
def reconcile(self) -> None:
    """
    Called once at coordinator startup. Scans for experiments in non-terminal
    states, identifies runs that were never dispatched as K8s Jobs, and
    creates their Jobs. Idempotent — safe to call multiple times.
    """
    active_experiments = self._get_active_experiments()
    for experiment in active_experiments:
        label_selector = f"experiment={experiment.id}"
        existing_jobs = self.k8s_client.list_namespaced_job(
            self.namespace, label_selector=label_selector
        )
        dispatched_run_ids = {
            job.metadata.labels.get("run-id")
            for job in existing_jobs.items
            if job.metadata.labels
        }
        pending_runs = self._get_pending_runs(experiment.id)
        orphaned = [r for r in pending_runs if r.id not in dispatched_run_ids]
        if orphaned:
            logger.warning(f"Experiment {experiment.id}: {len(orphaned)} orphaned runs — dispatching")
            for run in orphaned:
                try:
                    self._create_k8s_job(experiment.id, run)
                except kubernetes.client.ApiException as e:
                    if e.status == 409:
                        pass  # Job already exists — race condition, safe to ignore
                    else:
                        logger.error(f"Failed to dispatch orphaned run {run.id}: {e}")

    # Also check for completed experiments that never got their matrix report
    for experiment in active_experiments:
        status = self.get_experiment_status(experiment.id)
        if status.running == 0 and status.pending == 0:
            self.finalize_experiment(experiment.id)
```

Wired into FastAPI startup:

```python
@app.on_event("startup")
async def startup():
    await asyncio.get_event_loop().run_in_executor(None, coordinator.reconcile)
```

#### Retention Cleanup

```python
async def retention_cleanup_loop(
    storage_root: Path, retention_days: int, interval_s: int = 3600
):
    """Background task: deletes experiment directories older than retention_days."""
    while True:
        await asyncio.sleep(interval_s)
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        for experiment_dir in (storage_root / "outputs").iterdir():
            if not experiment_dir.is_dir():
                continue
            mtime = datetime.utcfromtimestamp(experiment_dir.stat().st_mtime)
            if mtime < cutoff:
                shutil.rmtree(experiment_dir, ignore_errors=True)
                logger.info(f"Retention cleanup: deleted {experiment_dir.name}")
```

### 5.3 Experiment Worker

Each K8s Job runs a single experiment inside a container. The worker is a standalone entry point that receives its config, executes the experiment, and writes results to shared storage.

```python
# src/sec_review_framework/worker.py

class ExperimentWorker:
    """
    Runs a single ExperimentRun inside a container.
    Entry point for K8s Job pods.
    """

    def run(self, run: ExperimentRun, output_dir: Path, datasets_dir: Path) -> None:
        target = self._load_target(run.dataset_name, datasets_dir)
        labels = LabelStore(datasets_dir).load(run.dataset_name, run.dataset_version)
        model = ModelProviderFactory().create(run.model_id, run.model_config)
        strategy = StrategyFactory().create(run.strategy)
        tools = ToolRegistryFactory().create(run.tool_variant, target)
        profile = ProfileRegistry().get(run.review_profile)

        config = {**run.strategy_config, "review_profile": profile, "parallel": run.parallel}

        start = time.monotonic()
        try:
            # Phase 1: Strategy produces candidate findings
            strategy_output = strategy.run(target, model, tools, config)
            candidates = strategy_output.findings

            # Phase 2: Optional verification pass
            findings_pre_verification = None
            verification_result = None
            if run.verification_variant == VerificationVariant.WITH_VERIFICATION:
                findings_pre_verification = list(candidates)
                verifier = LLMVerifier()
                verification_result = verifier.verify(candidates, target, model, tools)
                candidates = [
                    vf.finding for vf in
                    verification_result.verified + verification_result.uncertain
                ]

            findings = candidates
            status = RunStatus.COMPLETED
            error = None
        except Exception as e:
            findings = []
            findings_pre_verification = None
            verification_result = None
            strategy_output = StrategyOutput(
                findings=[], pre_dedup_count=0, post_dedup_count=0, dedup_log=[]
            )
            status = RunStatus.FAILED
            error = str(e)

        duration = time.monotonic() - start

        evaluation = (
            Evaluator().evaluate(findings, labels)
            if status == RunStatus.COMPLETED else None
        )

        verification_tokens = (
            verification_result.verification_tokens if verification_result else 0
        )
        total_input = sum(t.input_tokens for t in model.token_log)
        total_output = sum(t.output_tokens for t in model.token_log)
        estimated_cost = CostCalculator.from_config().compute(
            run.model_id, total_input, total_output
        )

        result = RunResult(
            experiment=run,
            status=status,
            findings=findings,
            findings_pre_verification=findings_pre_verification,
            verification_result=verification_result,
            evaluation=evaluation,
            strategy_output=strategy_output,
            tool_call_count=len(tools.audit_log.entries),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            verification_tokens=verification_tokens,
            estimated_cost_usd=estimated_cost,
            duration_seconds=duration,
            error=error,
            completed_at=datetime.utcnow(),
        )

        # Write all outputs to shared storage
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "run_result.json").write_text(result.model_dump_json(indent=2))
        self._write_jsonl(output_dir / "findings.jsonl", findings)
        self._write_jsonl(output_dir / "tool_calls.jsonl", tools.audit_log.entries)
        self._write_jsonl(output_dir / "conversation.jsonl", model.conversation_log)
        if findings_pre_verification:
            self._write_jsonl(output_dir / "findings_pre_verification.jsonl",
                              findings_pre_verification)
        ReportGenerator().render_run(result, output_dir)


def main():
    """Entry point for worker container. Args passed by K8s Job command spec."""
    import sys
    run_config_path, output_dir, datasets_dir = sys.argv[1], sys.argv[2], sys.argv[3]

    run = ExperimentRun.model_validate_json(Path(run_config_path).read_text())
    worker = ExperimentWorker()
    worker.run(run, Path(output_dir), Path(datasets_dir))
```

### 5.4 Per-Model Concurrency Caps

Different LLM providers have different rate limits. The coordinator enforces per-model concurrency caps so N experiments using the same model API don't all run simultaneously.

```yaml
# config/concurrency.yaml

default_cap: 4                    # models not listed below

per_model:
  gpt-4o: 6                      # OpenAI has higher rate limits
  claude-opus-4: 3               # Anthropic has stricter limits
  gemini-2.0-pro: 4
  mistral-large: 4
  command-r-plus: 4
```

The coordinator reads this config and uses it in `_schedule_jobs()`. If 3 claude-opus-4 jobs are already running, the coordinator won't schedule a 4th until one completes — regardless of the global `max_parallel_runs`.

The global `max_parallel_runs` in `experiments.yaml` sets an overall cap across all models. The effective concurrency for a given model is `min(per_model_cap, available_global_slots)`.

### 5.5 Output Directory Layout Per Run

```
/data/outputs/{experiment_id}/{run_id}/
    findings.jsonl                    — final findings (post-verification)
    findings_pre_verification.jsonl   — candidates before verification (if verification enabled)
    verification_log.jsonl            — verify/reject decisions with evidence
    tool_calls.jsonl                  — one ToolCallRecord per line from audit log
    conversation.jsonl                — full message-by-message LLM transcript (for debugging)
    run_result.json                   — full RunResult including EvaluationResult + PromptSnapshot
    report.md                         — human-readable run report
    report.json                       — machine-readable run report
```

---

## 6. Ground Truth Management

### 6.1 Label Store

```python
class LabelStore:
    """Reads and writes ground truth labels from JSONL files."""

    def __init__(self, datasets_root: Path):
        self.datasets_root = datasets_root

    def load(self, dataset_name: str, version: str | None = None) -> list[GroundTruthLabel]:
        path = self.datasets_root / "targets" / dataset_name / "labels.jsonl"
        labels = [
            GroundTruthLabel.model_validate_json(line)
            for line in path.read_text().splitlines() if line.strip()
        ]
        if version:
            labels = [l for l in labels if l.dataset_version == version]
        return labels

    def append(self, dataset_name: str, labels: list[GroundTruthLabel]) -> None:
        path = self.datasets_root / "targets" / dataset_name / "labels.jsonl"
        with path.open("a") as f:
            for label in labels:
                f.write(label.model_dump_json() + "\n")
```

### 6.2 CVE Selection Pipeline

Two modes: **automatic discovery** (query advisory databases, filter by criteria, surface candidates) and **manual resolve** (user provides a CVE ID, framework finds the repo and fix commit automatically).

#### Data Sources

The pipeline queries multiple sources and merges results:

| Source | What it provides | API |
|--------|-----------------|-----|
| **NVD** (NIST) | CVE metadata, CWE mapping, severity (CVSS), affected products | `services.nvd.nist.gov/rest/json/cves/2.0` |
| **GitHub Advisory Database** | GHSA records, linked repos, fix commit SHAs, affected version ranges | `api.github.com/advisories` |
| **OSV.dev** | Cross-ecosystem vuln data, Git commit ranges for fixes | `api.osv.dev/v1/query` |

GitHub Advisory DB is the most valuable because it directly links CVEs to repositories and fix commits. NVD provides the canonical severity and CWE classification. OSV fills gaps for ecosystems GitHub doesn't cover well.

#### Selection Criteria

```python
class CVESelectionCriteria(BaseModel):
    """Configurable filters for automatic CVE discovery."""

    # Language / ecosystem
    languages: list[str] = ["python", "javascript", "go", "java", "ruby"]

    # Vulnerability classification
    vuln_classes: list[VulnClass] | None = None   # None = all classes
    cwe_ids: list[str] | None = None              # e.g. ["CWE-89", "CWE-79"]
    min_severity: Severity = Severity.MEDIUM       # skip low/info
    max_severity: Severity = Severity.CRITICAL

    # Patch characteristics (proxy for "detectable from source")
    max_files_changed: int = 5           # large patches are refactors, not bug fixes
    max_lines_changed: int = 200         # small patches = localized, testable vulns
    min_lines_changed: int = 2           # trivial 1-line version bumps aren't interesting

    # Repository characteristics
    max_repo_kloc: float = 50.0          # keep within context window feasibility
    min_repo_kloc: float = 1.0           # too tiny = synthetic, not realistic

    # Temporal
    published_after: str | None = None   # ISO date, e.g. "2022-01-01"
    published_before: str | None = None

    # Must have a resolvable fix commit on GitHub
    require_github_fix: bool = True
```

#### CVE Resolver (Manual Mode)

Given a CVE ID, automatically find the GitHub repo and fix commit. The user provides only the CVE ID — everything else is resolved.

```python
class ResolvedCVE(BaseModel):
    """A CVE with all metadata resolved from advisory databases."""
    cve_id: str
    ghsa_id: str | None = None
    description: str
    cwe_ids: list[str]
    vuln_class: VulnClass             # mapped from primary CWE
    severity: Severity                # from CVSS score
    cvss_score: float | None = None
    repo_url: str                     # GitHub repo URL
    fix_commit_sha: str               # commit that fixed the vulnerability
    affected_files: list[str]         # files changed in the fix commit
    lines_changed: int                # total lines added + removed in fix
    language: str                     # primary language of the repo
    repo_kloc: float | None = None   # estimated repo size
    published_date: str
    source: str                       # "ghsa" | "nvd" | "osv"

class CVEResolver:
    """Resolves a CVE ID to a ResolvedCVE with repo + fix commit info."""

    CWE_TO_VULN_CLASS: dict[str, VulnClass] = {
        "CWE-89": VulnClass.SQLI,
        "CWE-79": VulnClass.XSS,
        "CWE-918": VulnClass.SSRF,
        "CWE-78": VulnClass.RCE,
        "CWE-22": VulnClass.PATH_TRAVERSAL,
        "CWE-327": VulnClass.CRYPTO_MISUSE,
        "CWE-798": VulnClass.HARDCODED_SECRET,
        "CWE-502": VulnClass.DESERIALIZATION,
        "CWE-611": VulnClass.XXE,
        "CWE-601": VulnClass.OPEN_REDIRECT,
        # ... complete mapping
    }

    def __init__(self, github_token: str):
        self.github_token = github_token

    def resolve(self, cve_id: str) -> ResolvedCVE | None:
        """
        Resolution order:
        1. Query GitHub Advisory DB for the CVE → get GHSA record with repo + references
        2. From GHSA references, extract fix commit SHA (tagged as "patch" type)
        3. If no fix commit in GHSA, query OSV.dev for git commit ranges
        4. If still no fix, query NVD references for GitHub commit URLs
        5. Validate: clone repo, verify fix commit exists, parse diff for patch stats
        6. Map CWE → VulnClass, extract severity from CVSS
        """
        # Step 1: GitHub Advisory DB
        ghsa = self._query_ghsa(cve_id)
        if ghsa and ghsa.get("references"):
            fix_commit = self._extract_fix_commit(ghsa["references"])
            repo_url = self._extract_repo_url(ghsa["references"])
            if fix_commit and repo_url:
                return self._build_resolved(cve_id, ghsa, repo_url, fix_commit)

        # Step 2: OSV.dev fallback
        osv = self._query_osv(cve_id)
        if osv:
            fix_commit = self._extract_osv_fix(osv)
            repo_url = self._extract_osv_repo(osv)
            if fix_commit and repo_url:
                return self._build_resolved(cve_id, osv, repo_url, fix_commit)

        # Step 3: NVD reference URLs fallback
        nvd = self._query_nvd(cve_id)
        if nvd:
            for ref in nvd.get("references", []):
                if "github.com" in ref["url"] and "/commit/" in ref["url"]:
                    repo_url, fix_commit = self._parse_github_commit_url(ref["url"])
                    return self._build_resolved(cve_id, nvd, repo_url, fix_commit)

        return None  # could not resolve

    def _query_ghsa(self, cve_id: str) -> dict | None:
        """Query GitHub Advisory Database: GET /advisories?cve_id={cve_id}"""
        resp = requests.get(
            "https://api.github.com/advisories",
            params={"cve_id": cve_id},
            headers={"Authorization": f"token {self.github_token}"},
        )
        if resp.ok and resp.json():
            return resp.json()[0]
        return None

    def _extract_fix_commit(self, references: list[dict]) -> str | None:
        """Extract commit SHA from GHSA references tagged as 'PATCH'."""
        for ref in references:
            url = ref.get("url", "")
            if "/commit/" in url:
                return url.rstrip("/").split("/")[-1]
        return None

    def _validate_patch(self, repo_url: str, fix_sha: str) -> dict:
        """
        Clone repo, parse diff, return patch stats.
        Runs inside dataset-builder pod (has GitHub egress).
        """
        # git clone --depth=10 repo_url, git diff fix_sha~1 fix_sha --stat
        ...
```

#### CVE Discovery (Automatic Mode)

Queries advisory databases with selection criteria, filters candidates, and returns ranked results for review before import.

```python
class CVECandidate(BaseModel):
    """A CVE that passed selection criteria, ready for user review."""
    resolved: ResolvedCVE
    score: float                       # 0-1, how well it fits the criteria
    score_breakdown: dict[str, float]  # {"patch_size": 0.9, "language": 1.0, ...}
    importable: bool                   # all required fields resolved?
    rejection_reason: str | None = None

class CVEDiscovery:
    """Discovers and ranks CVE candidates matching selection criteria."""

    def __init__(self, resolver: CVEResolver):
        self.resolver = resolver

    def discover(
        self,
        criteria: CVESelectionCriteria,
        max_results: int = 50,
    ) -> list[CVECandidate]:
        """
        1. Query GitHub Advisory DB with ecosystem + severity filters
        2. For each advisory, resolve to get repo + fix commit
        3. Filter by patch size, repo size, language
        4. Score remaining candidates
        5. Return top N ranked by score
        """
        raw_advisories = self._search_advisories(criteria)
        candidates = []

        for advisory in raw_advisories:
            cve_id = advisory.get("cve_id")
            if not cve_id:
                continue

            resolved = self.resolver.resolve(cve_id)
            if not resolved:
                continue

            candidate = self._evaluate(resolved, criteria)
            if candidate.score > 0:
                candidates.append(candidate)

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:max_results]

    def _search_advisories(self, criteria: CVESelectionCriteria) -> list[dict]:
        """
        Query GitHub Advisory DB:
        GET /advisories?ecosystem={lang}&severity={sev}&type=reviewed
        Also queries NVD for CWE-specific searches when cwe_ids specified.
        """
        ecosystems = self._languages_to_ecosystems(criteria.languages)
        results = []
        for ecosystem in ecosystems:
            resp = requests.get(
                "https://api.github.com/advisories",
                params={
                    "ecosystem": ecosystem,
                    "severity": criteria.min_severity.value,
                    "type": "reviewed",
                    "per_page": 100,
                },
                headers={"Authorization": f"token {self.github_token}"},
            )
            if resp.ok:
                results.extend(resp.json())
        return results

    def _evaluate(self, resolved: ResolvedCVE, criteria: CVESelectionCriteria) -> CVECandidate:
        """Score a resolved CVE against selection criteria."""
        scores = {}
        rejection = None

        # Hard filters (pass/fail)
        if criteria.require_github_fix and not resolved.fix_commit_sha:
            return CVECandidate(resolved=resolved, score=0, score_breakdown={},
                                importable=False, rejection_reason="No fix commit found")

        if resolved.lines_changed > criteria.max_lines_changed:
            rejection = f"Patch too large ({resolved.lines_changed} lines)"
        if resolved.lines_changed < criteria.min_lines_changed:
            rejection = f"Patch too small ({resolved.lines_changed} lines)"
        if len(resolved.affected_files) > criteria.max_files_changed:
            rejection = f"Too many files changed ({len(resolved.affected_files)})"

        if rejection:
            return CVECandidate(resolved=resolved, score=0, score_breakdown={},
                                importable=False, rejection_reason=rejection)

        # Soft scores (0-1)
        # Patch size: prefer small, focused patches
        patch_score = 1.0 - (resolved.lines_changed / criteria.max_lines_changed)
        scores["patch_size"] = max(0, patch_score)

        # Language match
        scores["language"] = 1.0 if resolved.language in criteria.languages else 0.0

        # Vuln class match (if specified)
        if criteria.vuln_classes:
            scores["vuln_class"] = 1.0 if resolved.vuln_class in criteria.vuln_classes else 0.3
        else:
            scores["vuln_class"] = 1.0

        # Severity preference: prefer high/critical over medium
        severity_order = {Severity.CRITICAL: 1.0, Severity.HIGH: 0.8,
                          Severity.MEDIUM: 0.5, Severity.LOW: 0.2}
        scores["severity"] = severity_order.get(resolved.severity, 0.3)

        # Repo size (if known): prefer repos in the target range
        if resolved.repo_kloc:
            if criteria.min_repo_kloc <= resolved.repo_kloc <= criteria.max_repo_kloc:
                scores["repo_size"] = 1.0
            else:
                scores["repo_size"] = 0.3

        total = sum(scores.values()) / len(scores)
        return CVECandidate(
            resolved=resolved,
            score=total,
            score_breakdown=scores,
            importable=True,
        )

    @staticmethod
    def _languages_to_ecosystems(languages: list[str]) -> list[str]:
        """Map programming languages to GitHub Advisory DB ecosystem names."""
        mapping = {
            "python": "pip",
            "javascript": "npm",
            "go": "go",
            "java": "maven",
            "ruby": "rubygems",
            "rust": "crates.io",
            "php": "composer",
            "dotnet": "nuget",
        }
        return [mapping[l] for l in languages if l in mapping]
```

#### CVE Importer (Unified)

Accepts either a full `CVEImportSpec` (manual, all fields provided) or just a CVE ID (auto-resolved).

```python
@dataclass
class CVEImportSpec:
    """Full spec — used when user provides all details, or after resolution."""
    cve_id: str
    repo_url: str
    fix_commit_sha: str
    dataset_name: str
    cwe_id: str
    vuln_class: VulnClass
    severity: Severity
    description: str
    affected_files: list[str] | None = None

class CVEImporter:
    def __init__(self, datasets_root: Path, resolver: CVEResolver):
        self.datasets_root = datasets_root
        self.resolver = resolver

    def import_from_id(self, cve_id: str, dataset_name: str | None = None) -> list[GroundTruthLabel]:
        """
        Resolve-and-import: user provides only the CVE ID.
        Framework resolves repo, fix commit, metadata automatically.
        """
        resolved = self.resolver.resolve(cve_id)
        if not resolved:
            raise ValueError(f"Could not resolve {cve_id} to a GitHub repo + fix commit")

        spec = CVEImportSpec(
            cve_id=resolved.cve_id,
            repo_url=resolved.repo_url,
            fix_commit_sha=resolved.fix_commit_sha,
            dataset_name=dataset_name or f"cve-{cve_id.lower().replace('-', '_')}",
            cwe_id=resolved.cwe_ids[0] if resolved.cwe_ids else "CWE-unknown",
            vuln_class=resolved.vuln_class,
            severity=resolved.severity,
            description=resolved.description,
            affected_files=resolved.affected_files,
        )
        return self.import_from_spec(spec)

    def import_from_spec(self, spec: CVEImportSpec) -> list[GroundTruthLabel]:
        """
        Full-spec import: user provides all details.
        1. Clone repo at fix_commit_sha~1 (the vulnerable state).
        2. Parse diff to extract file paths and line ranges.
        3. Create GroundTruthLabel records.
        4. Install repo and labels.
        """
        repo_path = self._clone_at_parent(spec.repo_url, spec.fix_commit_sha)
        affected = spec.affected_files or self._parse_diff(repo_path, spec.fix_commit_sha)
        labels = self._build_labels(spec, affected)
        self._install_repo(repo_path, spec.dataset_name)
        return labels
```

#### Coordinator API Endpoints

```python
# --- CVE Selection ---

@app.post("/datasets/discover-cves")
def discover_cves(criteria: CVESelectionCriteria, max_results: int = 50) -> list[dict]:
    """
    Automatic mode: query advisory databases, filter by criteria, return
    ranked candidates. User reviews candidates, then imports chosen ones.
    """
    candidates = coordinator.cve_discovery.discover(criteria, max_results)
    return [c.model_dump() for c in candidates]

@app.post("/datasets/resolve-cve")
def resolve_cve(cve_id: str) -> dict:
    """
    Manual mode: given a CVE ID, find the GitHub repo, fix commit, and metadata.
    Returns a ResolvedCVE that can be reviewed before import.
    """
    resolved = coordinator.cve_resolver.resolve(cve_id)
    if not resolved:
        raise HTTPException(404, f"Could not resolve {cve_id}")
    return resolved.model_dump()

@app.post("/datasets/import-cve", status_code=201)
def import_cve(req: "CVEImportRequest") -> dict:
    """
    Import a CVE as a dataset. Two modes:
    - Provide only cve_id: framework resolves everything automatically
    - Provide full CVEImportSpec: use exact repo/commit/metadata provided
    """
    if req.spec:
        labels = coordinator.cve_importer.import_from_spec(req.spec)
    else:
        labels = coordinator.cve_importer.import_from_id(req.cve_id, req.dataset_name)
    return {"dataset_name": req.dataset_name or req.cve_id, "labels_created": len(labels)}

class CVEImportRequest(BaseModel):
    cve_id: str                          # always required
    dataset_name: str | None = None      # auto-generated if omitted
    spec: CVEImportSpec | None = None    # provide for full-spec mode, omit for auto-resolve
```

#### Workflow

**Automatic discovery:**

```bash
# Find CVEs matching criteria
curl -X POST $COORD/datasets/discover-cves \
  -H "Content-Type: application/json" \
  -d '{
    "languages": ["python", "javascript"],
    "vuln_classes": ["sqli", "xss", "ssrf", "auth_bypass"],
    "min_severity": "medium",
    "max_files_changed": 3,
    "max_lines_changed": 100,
    "max_repo_kloc": 30,
    "published_after": "2023-01-01"
  }'

# Response: ranked candidates with scores
# [
#   {
#     "resolved": {"cve_id": "CVE-2024-1234", "repo_url": "...", "fix_commit_sha": "...", ...},
#     "score": 0.92,
#     "score_breakdown": {"patch_size": 0.95, "language": 1.0, "severity": 0.8, ...},
#     "importable": true
#   },
#   ...
# ]

# Import a chosen candidate
curl -X POST $COORD/datasets/import-cve \
  -H "Content-Type: application/json" \
  -d '{"cve_id": "CVE-2024-1234"}'
```

**Manual resolve:**

```bash
# User has a specific CVE in mind
curl -X POST "$COORD/datasets/resolve-cve?cve_id=CVE-2023-45678"

# Response: resolved metadata for review
# {
#   "cve_id": "CVE-2023-45678",
#   "repo_url": "https://github.com/example/project",
#   "fix_commit_sha": "abc123def456",
#   "cwe_ids": ["CWE-89"],
#   "vuln_class": "sqli",
#   "severity": "high",
#   "affected_files": ["src/db/queries.py"],
#   "lines_changed": 12,
#   "language": "python"
# }

# Looks good — import it
curl -X POST $COORD/datasets/import-cve \
  -H "Content-Type: application/json" \
  -d '{"cve_id": "CVE-2023-45678", "dataset_name": "example-sqli"}'
```

**Operational note**: Both `discover-cves` and `resolve-cve` require outbound GitHub API access. These run inside the coordinator (or are proxied to a dataset-builder pod) since worker pods have restricted egress. The `import-cve` endpoint triggers a dataset-builder K8s Job for the actual git clone.

### 6.3 Vulnerability Injection

```python
@dataclass
class InjectionTemplate:
    id: str                          # e.g. "sqli_string_format_python"
    vuln_class: VulnClass
    cwe_id: str
    language: str                    # "python" | "javascript" | "go" | etc.
    description: str
    severity: Severity
    patch_template: str              # unified diff with {{PLACEHOLDER}} tokens
    anchor_pattern: str              # regex that must match a line in the target
    anchor_mode: str                 # "after" | "before" | "replace"

class VulnInjector:
    def __init__(self, templates_root: Path):
        self.templates_root = templates_root
        self.templates: dict[str, InjectionTemplate] = self._load_templates()

    def preview(
        self,
        repo_path: Path,
        template_id: str,
        target_file: str,
        substitutions: dict[str, str] | None = None,
    ) -> "InjectionPreview":
        """
        Dry-run: compute the injection without modifying files.
        Returns the unified diff, before/after snippets, and the label
        that would be created. Used by the frontend to show what will happen.
        """
        template = self.templates[template_id]
        file_path = repo_path / target_file
        original_content = file_path.read_text()
        anchor_line = self._find_anchor(original_content, template.anchor_pattern)
        patched_content = self._apply_patch(
            original_content, template, anchor_line, substitutions
        )
        diff = difflib.unified_diff(
            original_content.splitlines(keepends=True),
            patched_content.splitlines(keepends=True),
            fromfile=f"a/{target_file}",
            tofile=f"b/{target_file}",
        )
        return InjectionPreview(
            template_id=template_id,
            target_file=target_file,
            anchor_line=anchor_line,
            unified_diff="".join(diff),
            before_snippet=self._extract_snippet(original_content, anchor_line, context=4),
            after_snippet=self._extract_snippet(patched_content, anchor_line, context=6),
            label_preview=self._preview_label(template, target_file, anchor_line, patched_content),
        )

    def inject(
        self,
        repo_path: Path,
        template_id: str,
        target_file: str,
        substitutions: dict[str, str] | None = None,
    ) -> InjectionResult:
        """Applies a template to a specific file in the repo. Modifies the file on disk."""
        ...

    def build_label(
        self,
        result: InjectionResult,
        template: InjectionTemplate,
        dataset_version: str,
    ) -> GroundTruthLabel:
        ...
```

Templates live at `datasets/templates/{vuln_class}/{template_id}.yaml`.

### 6.4 Diff Spec

For diff-mode datasets, a `diff_spec.yaml` file alongside the repo specifies what constitutes the "change" under review:

```yaml
# datasets/targets/{dataset_name}/diff_spec.yaml
base_ref: "abc123"     # commit hash of the "before" state
head_ref: "def456"     # commit hash of the "after" state (current repo state)
changed_files:         # optional explicit list; if omitted, computed from git diff
  - src/auth/session.py
  - src/api/users.py
```

The DiffReview strategy reads this file to scope its analysis. Labels with `introduced_in_diff: true` are vulns that exist in the diff; labels with `introduced_in_diff: false` are pre-existing.

### 6.5 Diff Dataset Generator

Creates realistic diff-mode datasets from clean repos by injecting vulnerabilities and producing a `diff_spec.yaml`.

```python
class DiffDatasetGenerator:
    """
    Takes a clean repo, applies vuln injection templates to create a
    "vulnerable branch", and generates diff_spec.yaml for DiffReview testing.
    """

    def __init__(self, injector: VulnInjector, datasets_root: Path):
        self.injector = injector
        self.datasets_root = datasets_root

    def generate(
        self,
        clean_repo_path: Path,
        dataset_name: str,
        template_ids: list[str],
        target_files: dict[str, str],     # template_id → target file path
        dataset_version: str,
    ) -> list[GroundTruthLabel]:
        """
        1. Copy clean repo to datasets/targets/{dataset_name}/repo/
        2. Create a git commit (the "clean" state = base_ref)
        3. Apply each injection template to the specified target files
        4. Create a git commit (the "vulnerable" state = head_ref)
        5. Generate diff_spec.yaml with base_ref and head_ref
        6. Generate labels with introduced_in_diff=True
        """
        repo_dest = self.datasets_root / "targets" / dataset_name / "repo"
        shutil.copytree(clean_repo_path, repo_dest, dirs_exist_ok=True)

        # Initialize git and commit clean state
        subprocess.run(["git", "init"], cwd=repo_dest)
        subprocess.run(["git", "add", "."], cwd=repo_dest)
        subprocess.run(["git", "commit", "-m", "clean"], cwd=repo_dest)
        base_ref = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_dest, text=True
        ).strip()

        # Apply injections
        labels = []
        for tmpl_id in template_ids:
            target_file = target_files[tmpl_id]
            result = self.injector.inject(repo_dest, tmpl_id, target_file)
            template = self.injector.templates[tmpl_id]
            label = self.injector.build_label(result, template, dataset_version)
            label.introduced_in_diff = True
            labels.append(label)

        # Commit vulnerable state
        subprocess.run(["git", "add", "."], cwd=repo_dest)
        subprocess.run(["git", "commit", "-m", "inject vulns"], cwd=repo_dest)
        head_ref = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_dest, text=True
        ).strip()

        # Write diff_spec.yaml
        diff_spec = {"base_ref": base_ref, "head_ref": head_ref}
        spec_path = self.datasets_root / "targets" / dataset_name / "diff_spec.yaml"
        spec_path.write_text(yaml.dump(diff_spec))

        # Write labels
        LabelStore(self.datasets_root).append(dataset_name, labels)
        return labels
```

This is triggered via `POST /datasets/{name}/generate-diff` on the coordinator.

---

## 7. Strategy Implementations

All strategies share the same agentic loop pattern: build a prompt, call the model, handle tool calls, loop until done, parse findings. The review profile's `system_prompt_modifier` is appended to every strategy's system prompt.

### 7.1 Shared Agentic Loop

```python
def run_agentic_loop(
    model: ModelProvider,
    tools: ToolRegistry,
    system_prompt: str,
    initial_user_message: str,
    max_turns: int = 50,
) -> str:
    """
    Runs a standard tool-use loop until the model returns a non-tool response.
    Returns the final text response.
    """
    messages = [Message(role="user", content=initial_user_message)]
    tool_defs = tools.get_tool_definitions()

    for _ in range(max_turns):
        response = model.complete(messages, tools=tool_defs, system_prompt=system_prompt)
        if not response.tool_calls:
            return response.content

        messages.append(Message(role="assistant", content=response.content))
        for call in response.tool_calls:
            result = tools.invoke(call["name"], call["input"], call["id"])
            messages.append(Message(role="tool", content=result, tool_call_id=call["id"]))

    raise RuntimeError(f"Exceeded max_turns={max_turns} in agentic loop")
```

When `tool_variant=WITHOUT_TOOLS`, `tools.get_tool_definitions()` returns an empty list — the model never receives tool definitions.

### 7.2 Review Profile Injection

All strategies inject the review profile into their system prompt:

```python
def build_system_prompt(base_prompt: str, config: dict) -> str:
    profile: ReviewProfile = config["review_profile"]
    if profile.system_prompt_modifier:
        return f"{base_prompt}\n\n{profile.system_prompt_modifier}"
    return base_prompt
```

### 7.3 Finding Parser

```python
FINDING_OUTPUT_FORMAT = """
At the end of your analysis, output your findings as a JSON array in a ```json block.
Each finding must be a JSON object with these fields:
{
  "file_path": "relative/path/to/file.py",
  "line_start": 42,
  "line_end": 47,
  "vuln_class": "sqli",
  "cwe_ids": ["CWE-89"],
  "severity": "high",
  "title": "SQL Injection in user login endpoint",
  "description": "...",
  "recommendation": "...",
  "confidence": 0.9
}
Include all genuine findings. Do not include false alarms you are uncertain about.
"""

class FindingParser:
    def parse(self, llm_output: str, experiment_id: str, produced_by: str) -> list[Finding]:
        """Extract JSON block from llm_output and validate into Finding objects."""
        ...
```

### 7.4 Deduplication with Tracking

```python
def deduplicate(findings: list[Finding]) -> StrategyOutput:
    """
    Deduplicates findings: same file_path + vuln_class within 5 lines → keep highest confidence.
    Returns StrategyOutput with pre/post counts and a log of what was merged.
    """
    pre_count = len(findings)
    dedup_log: list[DedupEntry] = []
    kept: list[Finding] = []

    # Group by (file_path, vuln_class), merge overlapping line ranges
    ...

    return StrategyOutput(
        findings=kept,
        pre_dedup_count=pre_count,
        post_dedup_count=len(kept),
        dedup_log=dedup_log,
    )
```

### 7.5 Parallel Subagent Execution

Strategies with multiple subagents (PerFile, PerVulnClass) support a `parallel` config flag:

```python
import concurrent.futures

def run_subagents(
    tasks: list[dict],              # [{system_prompt, user_message, max_turns}]
    model: ModelProvider,
    tools: ToolRegistry,
    parallel: bool,
    max_workers: int = 4,
) -> list[str]:
    """
    Runs multiple agentic loops either sequentially or in parallel.
    Each task gets its own tool registry clone (for independent audit logs).
    """
    if not parallel:
        return [
            run_agentic_loop(model, tools, t["system_prompt"], t["user_message"], t["max_turns"])
            for t in tasks
        ]

    def _run_one(task):
        tools_clone = tools.clone()  # independent audit log per subagent
        return run_agentic_loop(model, tools_clone, task["system_prompt"],
                                task["user_message"], task["max_turns"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_run_one, tasks))
```

When `parallel=True`, each subagent gets a cloned `ToolRegistry` so audit logs don't interleave. The parent merges audit logs after all subagents complete. Rate limiting is handled by the `ModelProvider` implementation (retry with backoff).

### 7.6 Strategy 1: SingleAgent

One agent scans the full repo. Has access to the full repo via `RepoAccessTool` and navigates it autonomously.

```python
class SingleAgentStrategy(ScanStrategy):
    def name(self) -> str:
        return "single_agent"

    def run(self, target, model, tools, config) -> StrategyOutput:
        system_prompt = build_system_prompt(self._base_system_prompt(), config)
        repo_summary = self._build_repo_summary(target)
        user_message = f"""
You are a security code reviewer. Perform a comprehensive security audit
of the following codebase.

Repository structure:
{repo_summary}

Use your tools to read any files you need. Cover all vulnerability classes.
{FINDING_OUTPUT_FORMAT}
"""
        raw_output = run_agentic_loop(
            model, tools, system_prompt, user_message,
            max_turns=config.get("max_turns", 80)
        )
        findings = FindingParser().parse(raw_output, experiment_id=..., produced_by="single_agent")
        # No dedup for single agent — one pass, no overlap
        return StrategyOutput(
            findings=findings,
            pre_dedup_count=len(findings),
            post_dedup_count=len(findings),
            dedup_log=[],
        )
```

### 7.7 Strategy 2: PerFileSubagents

Subagents assigned per source file. Supports parallel execution.

```python
class PerFileStrategy(ScanStrategy):
    def name(self) -> str:
        return "per_file"

    def run(self, target, model, tools, config) -> StrategyOutput:
        source_files = target.list_source_files()
        system_prompt = build_system_prompt(self._base_system_prompt(), config)

        tasks = []
        for file_path in source_files:
            file_content = target.read_file(file_path)
            tasks.append({
                "system_prompt": system_prompt,
                "user_message": f"""
Perform a security audit of the following file.
You may use tools to read related files if context is needed.

File: {file_path}
```
{file_content}
```
{FINDING_OUTPUT_FORMAT}
""",
                "max_turns": config.get("max_turns_per_file", 20),
            })

        outputs = run_subagents(tasks, model, tools, parallel=config.get("parallel", False))
        all_findings = []
        for file_path, raw_output in zip(source_files, outputs):
            findings = FindingParser().parse(
                raw_output, experiment_id=..., produced_by=f"per_file:{file_path}"
            )
            all_findings.extend(findings)

        return deduplicate(all_findings)
```

### 7.8 Strategy 3: PerVulnClassSpecialists

Subagents specialized by vulnerability class, each scanning the full repo. Supports parallel execution.

```python
VULN_CLASS_SYSTEM_PROMPTS: dict[VulnClass, str] = {
    VulnClass.SQLI: "You are a SQL injection specialist...",
    VulnClass.CRYPTO_MISUSE: "You are a cryptography specialist...",
    # ... one per VulnClass
}

class PerVulnClassStrategy(ScanStrategy):
    def name(self) -> str:
        return "per_vuln_class"

    def run(self, target, model, tools, config) -> StrategyOutput:
        active_classes = config.get("vuln_classes", list(VulnClass))
        repo_summary = self._build_repo_summary(target)

        tasks = []
        for vuln_class in active_classes:
            base_prompt = VULN_CLASS_SYSTEM_PROMPTS[vuln_class]
            system_prompt = build_system_prompt(base_prompt, config)
            tasks.append({
                "system_prompt": system_prompt,
                "user_message": f"""
You are scanning this repository for {vuln_class} vulnerabilities only.
Ignore all other vulnerability types.

Repository structure:
{repo_summary}

Use your tools to read any files that might contain {vuln_class} issues.
{FINDING_OUTPUT_FORMAT}
""",
                "max_turns": config.get("max_turns_per_class", 40),
            })

        outputs = run_subagents(tasks, model, tools, parallel=config.get("parallel", False))
        all_findings = []
        for vuln_class, raw_output in zip(active_classes, outputs):
            findings = FindingParser().parse(
                raw_output, experiment_id=..., produced_by=f"specialist:{vuln_class}"
            )
            all_findings.extend(findings)

        return deduplicate(all_findings)
```

### 7.9 Strategy 4: SASTFirstTriage

Semgrep runs first to triage, then LLM deep-dives on flagged areas. Supports parallel execution for the LLM phase.

```python
class SASTFirstStrategy(ScanStrategy):
    def name(self) -> str:
        return "sast_first"

    def run(self, target, model, tools, config) -> StrategyOutput:
        # Phase 1: Run Semgrep unconditionally
        semgrep_tool = SemgrepTool(target.repo_path)
        sast_results = semgrep_tool.run_full_scan()

        if not sast_results:
            return StrategyOutput(
                findings=[], pre_dedup_count=0, post_dedup_count=0, dedup_log=[]
            )

        by_file: dict[str, list[SASTMatch]] = defaultdict(list)
        for match in sast_results:
            by_file[match.file_path].append(match)

        system_prompt = build_system_prompt(self._base_system_prompt(), config)
        tasks = []
        file_paths = []
        for file_path, matches in by_file.items():
            file_content = target.read_file(file_path)
            sast_summary = self._format_sast_matches(matches)
            tasks.append({
                "system_prompt": system_prompt,
                "user_message": f"""
Semgrep flagged the following issues in {file_path}.
Confirm, reject, or escalate each. Also look for issues Semgrep missed.

SAST findings:
{sast_summary}

File content:
```
{file_content}
```
{FINDING_OUTPUT_FORMAT}
""",
                "max_turns": config.get("max_turns_per_file", 25),
            })
            file_paths.append(file_path)

        outputs = run_subagents(tasks, model, tools, parallel=config.get("parallel", False))
        all_findings = []
        for file_path, raw_output in zip(file_paths, outputs):
            findings = FindingParser().parse(
                raw_output, experiment_id=..., produced_by=f"sast_first:{file_path}"
            )
            all_findings.extend(findings)

        # No dedup — each file processed once
        return StrategyOutput(
            findings=all_findings,
            pre_dedup_count=len(all_findings),
            post_dedup_count=len(all_findings),
            dedup_log=[],
        )
```

**Important**: Semgrep always runs in Phase 1 (it is the triage mechanism). The `tool_variant` dimension controls whether the LLM in Phase 2 has additional tool access for cross-file context.

### 7.10 Strategy 5: DiffReview

Reviews a diff (simulating PR-time review) rather than the full codebase.

```python
class DiffReviewStrategy(ScanStrategy):
    """
    Simulates PR-time review. Requires a diff_spec.yaml in the dataset
    that specifies base_ref and head_ref.

    The agent receives:
    - The unified diff between base and head
    - Full file content for each changed file (for context)
    - Access to the full repo via tools (if tool_variant = WITH_TOOLS)

    Findings are expected to focus on the changed code. The evaluator can
    use the introduced_in_diff label field to measure whether the model
    correctly distinguishes new vs pre-existing issues.
    """

    def name(self) -> str:
        return "diff_review"

    def run(self, target, model, tools, config) -> StrategyOutput:
        diff_spec = target.load_diff_spec()  # raises if no diff_spec.yaml
        diff_text = target.get_diff(diff_spec.base_ref, diff_spec.head_ref)
        changed_files = target.get_changed_files(diff_spec.base_ref, diff_spec.head_ref)

        system_prompt = build_system_prompt(self._base_system_prompt(), config)

        # Build context: diff + full content of changed files
        file_context = ""
        for fp in changed_files:
            content = target.read_file(fp)
            file_context += f"\n--- {fp} (full file) ---\n{content}\n"

        user_message = f"""
You are reviewing a code change (pull request). Focus your security analysis
on the changed code, but consider the full file context for understanding.

Flag issues in three categories:
- Bugs introduced by this change
- Pre-existing bugs in the touched files that should be addressed
- Bugs in unchanged code that interact with the changes

Unified diff:
```diff
{diff_text}
```

Full content of changed files:
{file_context}

Use your tools to read any other files needed for context.
{FINDING_OUTPUT_FORMAT}
"""
        raw_output = run_agentic_loop(
            model, tools, system_prompt, user_message,
            max_turns=config.get("max_turns", 60)
        )
        findings = FindingParser().parse(
            raw_output, experiment_id=..., produced_by="diff_review"
        )
        return StrategyOutput(
            findings=findings,
            pre_dedup_count=len(findings),
            post_dedup_count=len(findings),
            dedup_log=[],
        )
```

If a dataset has no `diff_spec.yaml`, the runner skips `DIFF_REVIEW` for that dataset and logs a warning.

---

## 8. Verification Pass

The verification pass is an optional pipeline stage that sits between strategy output and evaluation. It uses the same model (or optionally a different one) to check each candidate finding against the actual source code.

### 8.1 LLMVerifier

```python
VERIFICATION_PROMPT = """
You are a security finding verifier. For each candidate finding below,
determine whether it is a genuine vulnerability by examining the source code.

For each finding, you must:
1. Read the cited file and line range
2. Trace the data flow to determine if the vulnerability is exploitable
3. Check if any existing mitigations (input validation, framework guards, etc.) prevent exploitation
4. Cite specific lines of code as evidence for your decision

Respond with a JSON array where each entry has:
{
  "finding_id": "...",
  "outcome": "verified" | "rejected" | "uncertain",
  "evidence": "Your detailed reasoning with file:line citations",
  "cited_lines": ["src/auth.py:42", "src/auth.py:55"]
}
"""

class LLMVerifier(Verifier):
    def verify(
        self,
        candidates: list[Finding],
        target: TargetCodebase,
        model: ModelProvider,
        tools: ToolRegistry,
    ) -> VerificationResult:
        # Build a prompt with all candidate findings
        findings_summary = self._format_candidates(candidates)
        user_message = f"""
Verify the following {len(candidates)} candidate security findings:

{findings_summary}

Use your tools to read the source code and verify each finding.
{VERIFICATION_PROMPT}
"""
        raw_output = run_agentic_loop(
            model, tools,
            system_prompt="You are a precise security finding verifier.",
            initial_user_message=user_message,
            max_turns=40,
        )

        decisions = self._parse_verification_output(raw_output)

        # Update findings with verification data and classify
        verified, rejected, uncertain = [], [], []
        for candidate in candidates:
            decision = decisions.get(candidate.id)
            if decision is None:
                # Verifier didn't address this finding — treat as uncertain
                candidate.verified = None
                uncertain.append(VerifiedFinding(
                    finding=candidate,
                    outcome=VerificationOutcome.UNCERTAIN,
                    evidence="Not addressed by verifier",
                    cited_lines=[],
                ))
                continue

            candidate.verified = decision["outcome"] == "verified"
            candidate.verification_evidence = decision["evidence"]
            if decision["outcome"] == "rejected":
                candidate.verification_rejected_reason = decision["evidence"]

            vf = VerifiedFinding(
                finding=candidate,
                outcome=VerificationOutcome(decision["outcome"]),
                evidence=decision["evidence"],
                cited_lines=decision.get("cited_lines", []),
            )
            if decision["outcome"] == "verified":
                verified.append(vf)
            elif decision["outcome"] == "rejected":
                rejected.append(vf)
            else:
                uncertain.append(vf)

        return VerificationResult(
            verified=verified,
            rejected=rejected,
            uncertain=uncertain,
            total_candidates=len(candidates),
            verification_tokens=model.last_call_tokens,
        )
```

### 8.2 What Verification Measures

Verification is itself a testable variable. By running the same strategy with and without verification, you measure:

- **Precision impact**: Does verification reduce false positives?
- **Recall impact**: Does verification incorrectly reject true positives?
- **Cost**: How many additional tokens does verification consume?
- **Verification accuracy**: Of the findings verification rejects, how many were actually true positives? (measurable by comparing verification decisions against ground truth)

The `findings_pre_verification` field in `RunResult` preserves the pre-verification state so these comparisons are possible.

### 8.3 Cross-Model Verification

By default the verification pass uses the same model as the strategy. The optional `verifier_model_id` field on `ExperimentRun` enables cross-model verification — e.g., Claude verifying GPT-4o's findings.

This tests whether a different model's biases can catch false positives that the original model would confirm (since models tend to "agree with themselves"). The worker creates a separate `ModelProvider` for verification:

```python
# In ExperimentWorker.run():
if run.verification_variant == VerificationVariant.WITH_VERIFICATION:
    if run.verifier_model_id and run.verifier_model_id != run.model_id:
        verifier_model = ModelProviderFactory().create(
            run.effective_verifier_model, run.model_config
        )
    else:
        verifier_model = model  # same model

    findings_pre_verification = list(candidates)
    verification_result = LLMVerifier().verify(
        candidates, target, verifier_model, tools
    )
```

Cross-model verification is set at the matrix level (`verifier_model_id` in `ExperimentMatrix`) and applies to all runs where `verification_variant=WITH_VERIFICATION`. To test multiple verifier models, run separate experiments.

---

## 9. Evaluation Pipeline

### 9.1 Matching Algorithm (Bipartite Assignment)

The previous greedy matching algorithm failed when a file had multiple labeled vulnerabilities. For example, if `auth.py` had both a SQLi on line 42 and an XSS on line 180, and the model only found the XSS, the greedy matcher would consume the first label (SQLi) for the XSS finding — producing a misleading TP.

The fix uses bipartite matching: build a score matrix (finding x label), then use optimal assignment to find the best global matching.

```python
import numpy as np
from scipy.optimize import linear_sum_assignment

class FileLevelEvaluator(Evaluator):
    """
    Bipartite matching between findings and labels.
    Score considers: file path (required), vuln_class (high weight), line overlap (medium weight).
    Uses scipy linear_sum_assignment for optimal global matching.
    """

    def _match_score(self, finding: Finding, label: GroundTruthLabel) -> float:
        """Score a (finding, label) pair. Returns -1.0 if file path doesn't match."""
        if finding.file_path != label.file_path:
            return -1.0  # hard constraint — never cross-file match

        score = 0.0

        # Vuln class match (high weight)
        if finding.vuln_class == label.vuln_class:
            score += 4.0

        # Line overlap (medium weight)
        if finding.line_start is not None and label.line_start is not None:
            f_end = finding.line_end or finding.line_start
            tolerance = 5
            label_range = range(label.line_start - tolerance, label.line_end + tolerance + 1)
            finding_range = range(finding.line_start, f_end + 1)
            overlap = len(set(finding_range) & set(label_range))
            if overlap > 0:
                score += 2.0 * min(overlap / max(len(finding_range), 1), 1.0)

        return score

    def evaluate(self, findings: list[Finding], labels: list[GroundTruthLabel]) -> EvaluationResult:
        if not findings and not labels:
            return self._empty_result()

        # Build score matrix: rows = findings, cols = labels
        n_findings, n_labels = len(findings), len(labels)
        scores = np.full((n_findings, n_labels), -1.0)
        for i, finding in enumerate(findings):
            for j, label in enumerate(labels):
                scores[i, j] = self._match_score(finding, label)

        # Optimal assignment (negate for minimization, mask infeasible pairs)
        cost = np.where(scores < 0, 1e9, -scores)
        matched_findings: list[MatchedFinding] = []
        matched_finding_indices: set[int] = set()
        matched_label_indices: set[int] = set()

        if n_findings > 0 and n_labels > 0:
            row_ind, col_ind = linear_sum_assignment(cost)
            for r, c in zip(row_ind, col_ind):
                if scores[r, c] > 0:  # must have at least file match + one signal
                    line_overlap = self._check_line_overlap(findings[r], labels[c])
                    matched_findings.append(MatchedFinding(
                        finding=findings[r],
                        matched_label=labels[c],
                        match_status=MatchStatus.TRUE_POSITIVE,
                        file_match=True,
                        line_overlap=line_overlap,
                    ))
                    matched_finding_indices.add(r)
                    matched_label_indices.add(c)

        # Unmatched findings → false positives
        for i, finding in enumerate(findings):
            if i not in matched_finding_indices:
                matched_findings.append(MatchedFinding(
                    finding=finding,
                    matched_label=None,
                    match_status=MatchStatus.FALSE_POSITIVE,
                    file_match=False,
                    line_overlap=False,
                ))

        unmatched_labels = [l for j, l in enumerate(labels) if j not in matched_label_indices]

        # Assess evidence quality for true positives
        evidence_assessor = self._get_evidence_assessor()
        for mf in matched_findings:
            if mf.match_status == MatchStatus.TRUE_POSITIVE:
                mf.evidence_quality = evidence_assessor.assess(mf.finding, mf.matched_label)

        return self._compute_metrics(matched_findings, unmatched_labels, labels)

    def _check_line_overlap(self, finding, label) -> bool:
        if finding.line_start is None or finding.line_end is None:
            return False
        return not (finding.line_end < label.line_start or finding.line_start > label.line_end)
```

### 9.2 Evidence Quality Assessment

```python
class EvidenceQualityAssessor:
    """
    Assesses the quality of a true positive finding's explanation.
    A finding can be correctly located (TP) but have poor reasoning.
    This matters for production use — human reviewers need actionable explanations.
    """

    def assess(self, finding: Finding, label: GroundTruthLabel) -> EvidenceQuality:
        score = 0

        # Does the description cite specific file:line references?
        has_line_citations = bool(re.findall(r'\b\w+\.\w+:\d+', finding.description))
        if has_line_citations:
            score += 1

        # Does the description mention the correct vulnerability mechanism?
        # Match CWE or vuln_class keywords in the description
        mechanism_keywords = self._get_mechanism_keywords(label.vuln_class)
        mechanism_mentioned = any(kw in finding.description.lower() for kw in mechanism_keywords)
        if mechanism_mentioned:
            score += 1

        # Does the finding have line overlap with the label (not just file match)?
        if (finding.line_start and finding.line_end
                and not (finding.line_end < label.line_start or finding.line_start > label.line_end)):
            score += 1

        if score >= 3:
            return EvidenceQuality.STRONG
        elif score >= 2:
            return EvidenceQuality.ADEQUATE
        else:
            return EvidenceQuality.WEAK

    def _get_mechanism_keywords(self, vuln_class: VulnClass) -> list[str]:
        return {
            VulnClass.SQLI: ["sql", "query", "injection", "parameterized", "prepared statement"],
            VulnClass.XSS: ["script", "html", "escape", "sanitize", "cross-site"],
            VulnClass.SSRF: ["url", "request", "fetch", "redirect", "internal"],
            VulnClass.CRYPTO_MISUSE: ["key", "encrypt", "hash", "random", "iv", "nonce"],
            # ... etc
        }.get(vuln_class, [])
```

The heuristic assessor is fast but limited — it can't judge whether the model's reasoning is actually correct. For higher fidelity, use the LLM-as-judge assessor.

#### LLM-as-Judge Evidence Assessor (Optional)

Sends the finding's description + actual source code to an LLM judge that evaluates whether the explanation is accurate, identifies the mechanism, and cites correct code. Drop-in replacement for the heuristic assessor.

```python
JUDGE_PROMPT = """
You are evaluating whether a security scanner correctly identified a vulnerability.

## Finding description (what the scanner reported)
{description}

## Source code excerpt
```
{source_excerpt}
```

## Labeled vulnerability
- Type: {vuln_class}
- Expected location: lines {line_start}-{line_end}

Answer ONLY with a JSON object:
{{
  "accurate": true|false,
  "identifies_mechanism": true|false,
  "cites_correct_code": true|false,
  "reason": "<one sentence>"
}}
"""

class LLMEvidenceAssessor:
    """Uses an LLM to judge evidence quality. More accurate than heuristics,
    but adds cost — use for final evaluation, not exploratory runs."""

    def __init__(self, model: ModelProvider, target: TargetCodebase):
        self.model = model
        self.target = target

    def assess(self, finding: Finding, label: GroundTruthLabel) -> EvidenceQuality:
        excerpt = self.target.read_file_excerpt(
            finding.file_path, label.line_start, context_lines=20
        )
        prompt = JUDGE_PROMPT.format(
            description=finding.description,
            source_excerpt=excerpt,
            vuln_class=label.vuln_class,
            line_start=label.line_start,
            line_end=label.line_end,
        )
        response = self.model.complete(
            [Message(role="user", content=prompt)], max_tokens=256
        )
        try:
            verdict = json.loads(response.content)
        except json.JSONDecodeError:
            return EvidenceQuality.NOT_ASSESSED

        score = sum([
            verdict.get("accurate", False),
            verdict.get("identifies_mechanism", False),
            verdict.get("cites_correct_code", False),
        ])
        if score >= 3:
            return EvidenceQuality.STRONG
        elif score >= 2:
            return EvidenceQuality.ADEQUATE
        else:
            return EvidenceQuality.WEAK
```

Configure which assessor to use via `experiments.yaml`:

```yaml
evaluation:
  evidence_assessor: "heuristic"   # or "llm_judge"
  evidence_judge_model: "claude-opus-4"  # only used when evidence_assessor = llm_judge
```

### 9.3 Metrics Computation

```python
def _compute_metrics(self, matched, unmatched_labels, all_labels) -> EvaluationResult:
    tp = sum(1 for m in matched if m.match_status == MatchStatus.TRUE_POSITIVE)
    fp = sum(1 for m in matched if m.match_status == MatchStatus.FALSE_POSITIVE)
    fn = len(unmatched_labels)
    unlabeled_real = sum(1 for m in matched if m.match_status == MatchStatus.UNLABELED_REAL)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # FPR: TN estimated as files in repo minus files with labels
    total_files = self._total_file_count
    positive_files = len(set(l.file_path for l in all_labels))
    tn = max(0, total_files - positive_files)
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    # Evidence quality breakdown
    tp_findings = [m for m in matched if m.match_status == MatchStatus.TRUE_POSITIVE]
    evidence_counts = {}
    for eq in EvidenceQuality:
        evidence_counts[eq.value] = sum(1 for m in tp_findings if m.evidence_quality == eq)

    return EvaluationResult(
        experiment_id=...,
        dataset_version=...,
        total_labels=len(all_labels),
        total_findings=len(matched),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        unlabeled_real_count=unlabeled_real,
        precision=precision,
        recall=recall,
        f1=f1,
        false_positive_rate=fpr,
        matched_findings=matched,
        unmatched_labels=unmatched_labels,
        evidence_quality_counts=evidence_counts,
    )
```

### 9.4 Statistical Significance

With `num_repetitions > 1`, the framework runs each matrix cell multiple times. This enables confidence intervals on metrics and pairwise model comparisons.

```python
import math
from scipy.stats import chi2

@dataclass
class ConfidenceInterval:
    point_estimate: float
    lower: float
    upper: float
    alpha: float = 0.05

def wilson_ci(successes: int, n: int, alpha: float = 0.05) -> ConfidenceInterval:
    """Wilson score interval — handles p near 0 or 1 better than normal approx."""
    if n == 0:
        return ConfidenceInterval(0.0, 0.0, 0.0, alpha)
    z = 1.96  # z_{0.975}
    p_hat = successes / n
    center = (p_hat + z**2 / (2 * n)) / (1 + z**2 / n)
    margin = (z / (1 + z**2 / n)) * math.sqrt(
        p_hat * (1 - p_hat) / n + z**2 / (4 * n**2)
    )
    return ConfidenceInterval(p_hat, max(0.0, center - margin), min(1.0, center + margin), alpha)

class StatisticalAnalyzer:
    """Computes confidence intervals and pairwise significance tests."""

    def __init__(self, alpha: float = 0.05):
        self.alpha = alpha

    def precision_ci(self, results: list[EvaluationResult]) -> ConfidenceInterval:
        """Wilson CI on precision across repeated runs."""
        tp_total = sum(r.true_positives for r in results)
        fp_total = sum(r.false_positives for r in results)
        return wilson_ci(tp_total, tp_total + fp_total, self.alpha)

    def recall_ci(self, results: list[EvaluationResult]) -> ConfidenceInterval:
        """Wilson CI on recall across repeated runs."""
        tp_total = sum(r.true_positives for r in results)
        fn_total = sum(r.false_negatives for r in results)
        return wilson_ci(tp_total, tp_total + fn_total, self.alpha)

    def mcnemar_test(
        self,
        results_a: list[EvaluationResult],
        results_b: list[EvaluationResult],
        labels: list[GroundTruthLabel],
    ) -> dict:
        """
        Pairwise McNemar's test: does model A detect vulns that model B misses,
        and vice versa? Requires both to be evaluated against the same labels.

        Returns dict with statistic, p_value, significant, and interpretation.
        """
        # For each label: was it found (TP) by A? by B?
        a_found = self._label_detection_set(results_a, labels)
        b_found = self._label_detection_set(results_b, labels)

        # Discordant pairs
        b_count = sum(1 for lid in labels if lid.id in a_found and lid.id not in b_found)
        c_count = sum(1 for lid in labels if lid.id not in a_found and lid.id in b_found)
        n = b_count + c_count
        if n == 0:
            return {"statistic": 0.0, "p_value": 1.0, "significant": False}

        chi2_stat = (abs(b_count - c_count) - 1) ** 2 / n
        p_value = 1 - chi2.cdf(chi2_stat, df=1)
        winner = "A" if b_count > c_count else "B"
        return {
            "statistic": chi2_stat,
            "p_value": p_value,
            "significant": p_value < self.alpha,
            "a_only": b_count,
            "b_only": c_count,
            "interpretation": f"{winner} detects more (p={p_value:.4f})",
        }
```

When `num_repetitions > 1`, the matrix report includes confidence intervals on all metrics and significance markers on pairwise comparisons. With `num_repetitions=1` (default), these sections are omitted — single-run metrics are reported without error bars.

### 9.5 Reclassification Workflow

After an experiment run, team members can reclassify false positives to `UNLABELED_REAL` via the coordinator API:

```
POST /experiments/{experiment_id}/runs/{run_id}/reclassify
{
  "finding_id": "abc123",
  "status": "unlabeled_real",
  "note": "Genuine path traversal not in our label set"
}
```

This updates `run_result.json` in shared storage and recomputes metrics.

---

## 10. Report Generation

### 10.1 Per-Run Report Contents

1. **Run Header**: experiment_id, model, strategy, tool_variant, review_profile, verification, dataset, status, duration, token counts, estimated cost
2. **Metrics Summary**: precision, recall, F1, FPR, TP/FP/FN counts, unlabeled_real count, evidence quality breakdown
3. **Dedup Summary**: pre/post dedup counts, merge log (if applicable)
4. **Verification Summary** (if enabled): candidates in → verified/rejected/uncertain out, tokens used
5. **True Positives**: file path, vuln class, severity, title, LLM description (verbatim), line overlap, evidence quality rating
6. **False Positives**: same fields, with "not in ground truth" note
7. **Rejected by Verification** (if enabled): findings the verifier dropped, with rejection reasoning
8. **Missed Vulnerabilities**: file path, vuln class, description, source
9. **Tool Call Summary**: count by tool name, total calls

### 10.2 Matrix Report Structure

```markdown
# Security Review Framework — Experiment {experiment_id} Results

## Comparative Matrix

| Model | Strategy | Tools | Profile | Verif | Prec | Recall | F1 | FPR | TP | FP | FN | Ev:Strong | Cost | Duration |
|-------|----------|-------|---------|-------|------|--------|----|-----|----|----|----|-----------|------|----------|
| gpt-4o | single_agent | with | default | none | 0.73 | 0.81 | 0.77 | 0.12 | 13 | 5 | 3 | 9/13 | $18.40 | 4m22s |
| gpt-4o | single_agent | with | default | verif | 0.87 | 0.75 | 0.81 | 0.06 | 12 | 2 | 4 | 10/12 | $24.10 | 6m05s |
| ...   | ...          | ...  | ...     | ...   | ...  | ...    | ... | ... | .. | .. | .. | ... | ... | ... |

## Analysis by Dimension

### Model Comparison (averaged across other dimensions)
...

### Strategy Comparison (averaged across other dimensions)
...

### Tool Access Impact (paired: with vs without, same model+strategy+profile+verification)
...

### Review Profile Impact (paired: profile X vs default, same model+strategy+tools+verification)
...

### Verification Impact (paired: with vs without, same model+strategy+tools+profile)
...

### Evidence Quality by Model
...

## Deduplication Analysis

| Strategy | Avg Pre-Dedup | Avg Post-Dedup | Avg Dedup Rate |
|----------|---------------|----------------|----------------|
| per_file | 28.3 | 19.1 | 32% |
| per_vuln_class | 22.7 | 17.4 | 23% |

## Cost Analysis

| Model | Avg Cost/Run | Total Experiment Cost | Cost per True Positive |
|-------|-------------|----------------------|----------------------|
| gpt-4o | $18.40 | $184.00 | $1.42 |
| ...   | ...         | ...              | ...                  |

## Findings Across Experiments

### Vulnerabilities Found by All Models (High Confidence)
...

### Vulnerabilities Consistently Missed
...

### Unlabeled Real Vulnerabilities Discovered
...
```

### 10.3 JSON Report Schema

```json
{
  "experiment_id": "...",
  "dataset": { "name": "...", "version": "..." },
  "generated_at": "...",
  "runs": [
    {
      "experiment_id": "...",
      "model_id": "...",
      "strategy": "...",
      "tool_variant": "...",
      "review_profile": "...",
      "verification_variant": "...",
      "metrics": {
        "precision": 0.73,
        "recall": 0.81,
        "f1": 0.77,
        "false_positive_rate": 0.12,
        "true_positives": 13,
        "false_positives": 5,
        "false_negatives": 3,
        "unlabeled_real": 1,
        "evidence_quality": { "strong": 9, "adequate": 3, "weak": 1 }
      },
      "dedup": {
        "pre_count": 28,
        "post_count": 19,
        "dedup_rate": 0.32
      },
      "verification": {
        "candidates": 19,
        "verified": 14,
        "rejected": 4,
        "uncertain": 1,
        "verification_tokens": 12000
      },
      "token_usage": { "input": 71000, "output": 13000 },
      "estimated_cost_usd": 18.40,
      "duration_seconds": 262,
      "findings": [ "..." ]
    }
  ],
  "dimension_analysis": {
    "by_model": {},
    "by_strategy": {},
    "tool_access_impact": {},
    "review_profile_impact": {},
    "verification_impact": {},
    "evidence_quality_by_model": {}
  },
  "cost_analysis": {
    "by_model": {},
    "total_experiment_cost": 0.0,
    "cost_per_true_positive": {}
  }
}
```

---

## 11. Cost Modeling

### 11.1 Pricing Configuration

```yaml
# config/pricing.yaml

# Prices in USD per million tokens. Update as provider pricing changes.
models:
  gpt-4o:
    input_per_million: 2.50
    output_per_million: 10.00

  claude-opus-4:
    input_per_million: 15.00
    output_per_million: 75.00

  gemini-2.0-pro:
    input_per_million: 1.25
    output_per_million: 10.00

  mistral-large:
    input_per_million: 2.00
    output_per_million: 6.00

  command-r-plus:
    input_per_million: 2.50
    output_per_million: 10.00
```

### 11.2 CostCalculator

```python
@dataclass
class ModelPricing:
    input_per_million: float
    output_per_million: float

class CostCalculator:
    def __init__(self, pricing: dict[str, ModelPricing]):
        self.pricing = pricing

    def compute(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        p = self.pricing.get(model_id)
        if p is None:
            return 0.0  # unknown model — log warning, don't crash
        return (input_tokens / 1_000_000 * p.input_per_million
                + output_tokens / 1_000_000 * p.output_per_million)

    def cost_per_true_positive(self, cost: float, true_positives: int) -> float | None:
        if true_positives == 0:
            return None
        return cost / true_positives
```

### 11.3 Why Cost Matters

A model that's 5% less precise but 80% cheaper may be the right production choice. The matrix report includes cost columns alongside accuracy metrics so the team can make informed tradeoffs. Cost-per-true-positive is the key efficiency metric — it captures both model pricing and model effectiveness in a single number.

---

## 12. Feedback Loop

The feedback loop enables learning across experiments. It does not automatically retrain or modify prompts — it surfaces patterns that the team uses to manually refine strategies and profiles.

### 12.1 FeedbackTracker

```python
class FeedbackTracker:
    """
    Compares results across experiments to surface patterns in model behavior.
    Produces a structured comparison that the team reviews to refine
    prompts, profiles, and strategies.
    """

    def compare_experiments(
        self,
        experiment_a_results: list[RunResult],
        experiment_b_results: list[RunResult],
    ) -> "ExperimentComparison":
        """
        For runs that share (model, strategy, tool_variant):
        - Did metrics improve or regress?
        - Which false positive patterns are persistent across experiments?
        - Which vulnerabilities are consistently missed by a given model?
        """
        ...

    def extract_fp_patterns(
        self,
        results: list[RunResult],
    ) -> list["FalsePositivePattern"]:
        """
        Across all runs in an experiment, find recurring false positive patterns:
        - Model X consistently flags ORM code as SQL injection
        - Model Y reports hardcoded secrets in test fixtures
        - Strategy Z produces duplicates for imported modules

        Returns structured patterns the team can turn into profile rules
        or prompt refinements.
        """
        ...

@dataclass
class FalsePositivePattern:
    model_id: str
    vuln_class: VulnClass
    pattern_description: str          # e.g. "flags Django ORM queries as SQLi"
    occurrence_count: int
    example_finding_ids: list[str]
    suggested_action: str             # e.g. "add ORM exemption to SQLI specialist prompt"

@dataclass
class ExperimentComparison:
    experiment_a_id: str
    experiment_b_id: str
    metric_deltas: dict[str, dict]    # run_id → {precision: +0.05, recall: -0.02, ...}
    persistent_false_positives: list[FalsePositivePattern]
    persistent_misses: list[dict]     # labels missed in both experiments
    improvements: list[dict]          # findings that went from FP→TP or FN→TP
    regressions: list[dict]           # findings that went from TP→FP or TP→FN

    # Cross-experiment finding matching uses FindingIdentity
    # Two findings from different experiments are "the same" if their
    # FindingIdentity matches (same file, same vuln_class, same 10-line bucket)
    finding_stability: dict[str, float]  # FindingIdentity → fraction of runs it appeared in
```

### 12.2 Workflow

```
# Compare two experiments:
POST /feedback/compare
{
  "experiment_a_id": "experiment_2026_04_16_v1",
  "experiment_b_id": "experiment_2026_04_20_v2"
}
# Returns: metric deltas, persistent FP patterns, regressions

# Extract FP patterns from a single experiment:
GET /feedback/patterns/experiment_2026_04_16_v1
# Returns: recurring false positive patterns with suggested actions
```

The team reviews the comparison, identifies persistent false positive patterns, and adjusts:
- **Review profiles**: add skip rules for known FP patterns
- **Strategy prompts**: add specificity to specialist prompts
- **Ground truth**: add missed labels surfaced by unlabeled_real findings

This is a manual loop — the framework provides the data, humans make the decisions.

---

## 13. Exfiltration Risk Documentation

### 13.1 Threat Model

When agents receive full repository access and operate autonomously, they can issue tool calls that transmit source code externally. The primary risks:

- A model issues unexpected tool calls that send code to external endpoints
- A bug in tool implementation passes code content to a third-party API
- A model generates curl commands or shell invocations that the framework executes
- Provider API calls themselves receive code as prompt context (structural — must be accepted)

The last point is structural: using an LLM to review code means the code reaches the LLM provider's API. This should be acknowledged in the team's data handling policy before running production code through any model API.

### 13.2 Recommended Layered Defense

**Layer 1: Network Isolation (Primary Control)**

Deploy within a VPC with outbound internet restricted at the network layer. The chart ships with a permissive default (`llmEgressCidrs: [0.0.0.0/0]`); operators are expected to narrow `llmEgressCidrs` in `values.yaml` to the specific provider CIDR ranges required for their deployment. This is the highest-value control because it is independent of application code correctness.

**Layer 2: Tool Call Auditing (Detection and Forensics)**

The `ToolCallAuditLog` records every tool invocation with its full input, written to `tool_calls.jsonl` per run. This provides:
- Complete record of agent behavior during each run
- Input to post-run review for unexpected URLs or shell-like content
- Forensic evidence for debugging

```python
@dataclass
class ToolCallRecord:
    call_id: str
    tool_name: str
    input: dict[str, Any]         # full input, not truncated
    timestamp: datetime
    duration_ms: int
    output_truncated: bool
```

**Layer 3: Tool Implementation Constraints (Defense in Depth)**

- `RepoAccessTool` reads from a pre-specified local directory only, validates paths against repo root
- `SemgrepTool` invokes the local binary only, no cloud API
- `DocLookupTool` calls only designated internal documentation endpoints
- Tools never accept arbitrary shell commands or URLs as inputs

**Layer 4: Output Review Workflow**

```
GET /experiments/{experiment_id}/runs/{run_id}/tool-audit
# Returns: tool call counts by type, any calls with URL-like strings in inputs
```

### 13.3 Out of Scope

- Adversarial prompt injection testing of agents
- Preventing provider training on submitted code (contractual, not technical)
- Side-channel exfiltration (theoretical)
- Compliance certification

---

## 14. Configuration Format

### 14.1 Experiment Configuration

```yaml
# config/experiments.yaml

experiment_id: "experiment_2026_04_16_v1"
dataset:
  name: "django-cms-cve-2021-mixed"
  version: "1.3.0"

models:
  - id: "gpt-4o"
  - id: "claude-opus-4"
  - id: "gemini-2.0-pro"
  - id: "mistral-large"
  - id: "command-r-plus"

strategies:
  - name: "single_agent"
    config:
      max_turns: 80

  - name: "per_file"
    config:
      max_turns_per_file: 20
      skip_patterns: ["**/test_*", "**/*.min.js", "**/vendor/**"]

  - name: "per_vuln_class"
    config:
      max_turns_per_class: 40
      vuln_classes:
        - sqli
        - xss
        - ssrf
        - rce
        - auth_bypass
        - crypto_misuse
        - hardcoded_secret
        - path_traversal
        - logic_bug
        - deserialization

  - name: "sast_first"
    config:
      max_turns_per_file: 25
      semgrep_config: "p/owasp-top-ten"

  - name: "diff_review"
    config:
      max_turns: 60

tool_variants:
  - "with_tools"
  - "without_tools"

# Optional dimensions — omit or single-element list to hold constant
review_profiles:
  - "default"

verification_variants:
  - "none"
  - "with_verification"

parallel_modes:
  - false

# Repetitions — run each matrix cell N times for statistical significance
num_repetitions: 1                # increase to 3-5 for significance testing

# Cross-model verification — optional
# verifier_model_id: "claude-opus-4"  # uncomment to use a different model for verification

# Spend control — coordinator auto-cancels remaining jobs when exceeded
max_experiment_cost_usd: 500.00

# Global cap on concurrent experiment runs across all models.
# Per-model caps in concurrency.yaml further constrain this.
max_parallel_runs: 10

# Evidence quality assessment
evaluation:
  evidence_assessor: "heuristic"      # or "llm_judge"
  # evidence_judge_model: "claude-opus-4"  # only used when evidence_assessor = llm_judge

output_root: "outputs/"
```

### 14.2 Retry Configuration

```yaml
# config/retry.yaml — per-provider retry policies

defaults:
  max_retries: 3
  base_delay: 1.0
  max_delay: 60.0
  jitter: true
  retryable_status_codes: [429, 503, 529]

providers:
  openai:
    max_retries: 5
    base_delay: 1.0
    max_delay: 32.0
    # 429 carries Retry-After header; honoured via exc.retry_after

  anthropic:
    max_retries: 4
    base_delay: 2.0
    max_delay: 60.0
    retryable_status_codes: [429, 529]
    # 529 = overloaded; treat same as rate-limit

  gemini:
    max_retries: 4
    base_delay: 1.5
    max_delay: 45.0

  mistral:
    max_retries: 3
    base_delay: 1.0
    max_delay: 30.0

  cohere:
    max_retries: 3
    base_delay: 2.0
    max_delay: 30.0
    jitter: false
    # Cohere rate limits are quota-based; jitter is less effective
```

### 14.3 Retention Configuration

```yaml
# In config/coordinator.yaml

retention:
  retention_days: 30              # experiments older than this are auto-deleted
  cleanup_interval_hours: 1       # how often the cleanup loop runs

jobs:
  ttl_seconds_after_finished: 3600  # K8s garbage-collects Job pods 1h after completion
```

### 14.4 Model Provider Configuration

The model catalog is **probe-driven** — there is no static `config/models.yaml`.
On startup, `ProviderCatalog` runs each provider's probe concurrently and builds a
`ProviderSnapshot` per provider.  `build_effective_registry()` converts those
snapshots into a `list[ModelProviderConfig]` which is then passed to
`compute_availability()` to produce the grouped model list served at `GET /models`.

Flow:

```
ProviderCatalog.start()
  └─ probes run concurrently (litellm_probe, bedrock_probe, openrouter_probe, …)
       └─ each returns ProviderSnapshot(probe_status, model_ids, metadata)
build_effective_registry(snapshots)
  └─ synthesizes list[ModelProviderConfig] — one entry per discovered model_id
compute_availability(registry, snapshots, env)
  └─ annotates each entry with status (available / key_missing / not_listed / …)
GET /models
  └─ returns groups_to_dicts(groups) — grouped by provider with probe_status
```

See `src/sec_review_framework/models/probes/` for per-provider discovery logic and
`src/sec_review_framework/models/availability.py` for the availability rules.

### 14.5 Vulnerability Class Definitions

```yaml
# config/vuln_classes.yaml

vuln_classes:
  sqli:
    display_name: "SQL Injection"
    cwe_ids: ["CWE-89"]
    owasp_category: "A03:2021"

  crypto_misuse:
    display_name: "Cryptographic Misuse"
    cwe_ids: ["CWE-326", "CWE-327", "CWE-328", "CWE-330"]
    owasp_category: "A02:2021"

  # ... complete list matching VulnClass enum
```

### 14.6 Review Profiles

Pre-canned review profiles. Each is a named configuration with a system prompt modifier that is injected into all strategy system prompts. Users select profiles by name in `experiments.yaml` — they do not write freeform instructions.

```yaml
# config/review_profiles.yaml

profiles:

  default:
    description: >
      Balanced security review. Standard confidence thresholds.
      Report all vulnerability classes at all severity levels.
    system_prompt_modifier: ""

  strict:
    description: >
      High evidence bar. Only report findings you are near-certain about.
      Every finding must include a specific file:line citation and a concrete
      explanation of how the vulnerability could be exploited.
    system_prompt_modifier: |
      REVIEW INSTRUCTIONS — STRICT MODE:
      - Only report findings where you have high confidence (0.8+) the vulnerability is real
      - Every finding MUST include specific file:line citations in the source code
      - Every finding MUST explain the concrete exploitation path — not just
        "this could be vulnerable" but exactly how an attacker would exploit it
      - If you cannot trace the data flow from user input to the vulnerable sink,
        do not report the finding
      - Prefer fewer, higher-quality findings over comprehensive coverage
      - Do not report potential issues based on naming conventions alone

  comprehensive:
    description: >
      Cast a wide net. Lower confidence threshold. Report anything suspicious
      including potential issues that need manual review.
    system_prompt_modifier: |
      REVIEW INSTRUCTIONS — COMPREHENSIVE MODE:
      - Report all potential security issues, even at lower confidence levels (0.3+)
      - Include findings that might be false positives but warrant human review
      - Flag code patterns that are not vulnerable today but could become vulnerable
        if assumptions change (e.g., input validation removed upstream)
      - Report defense-in-depth concerns (missing but not strictly required mitigations)
      - When uncertain, report the finding with a clear note about what you're unsure about

  owasp_focused:
    description: >
      Focus exclusively on OWASP Top 10 2021 vulnerability categories.
      Ignore all other vulnerability classes.
    system_prompt_modifier: |
      REVIEW INSTRUCTIONS — OWASP TOP 10 FOCUS:
      You MUST limit your analysis to the OWASP Top 10 2021 categories only:
      - A01:2021 Broken Access Control
      - A02:2021 Cryptographic Failures
      - A03:2021 Injection
      - A04:2021 Insecure Design
      - A05:2021 Security Misconfiguration
      - A06:2021 Vulnerable and Outdated Components
      - A07:2021 Identification and Authentication Failures
      - A08:2021 Software and Data Integrity Failures
      - A09:2021 Security Logging and Monitoring Failures
      - A10:2021 Server-Side Request Forgery (SSRF)
      Do not report issues that fall outside these categories.
      Map each finding to its OWASP category in the description.

  quick_scan:
    description: >
      Triage mode. Only report critical and high severity issues.
      Skip detailed explanations in favor of fast, actionable output.
    system_prompt_modifier: |
      REVIEW INSTRUCTIONS — QUICK SCAN / TRIAGE MODE:
      - Only report Critical and High severity vulnerabilities
      - Skip Medium, Low, and Informational findings entirely
      - Keep descriptions brief — one or two sentences explaining the risk
      - Focus on issues that represent immediate, exploitable risk
      - Prioritize: RCE > SQLi > Auth Bypass > SSRF > XSS > everything else
      - Aim for speed over completeness — flag the worst issues first
```

To test profile impact, include multiple profiles in the experiment config:

```yaml
# In experiments.yaml — this varies the profile dimension
review_profiles:
  - "default"
  - "strict"
  - "comprehensive"
```

This multiplies the matrix: 5 models × 5 strategies × 2 tool variants × 3 profiles = 150 runs (with verification held constant at `none`).

---

## 15. Deployment Architecture

### 15.1 Overview

The framework deploys to a K8s cluster within the VPC. Three components:

1. **Coordinator Service** — single-replica Deployment (FastAPI). Manages experiment lifecycle, schedules K8s Jobs, enforces concurrency caps, aggregates results, exposes Prometheus metrics.
2. **Worker Pods** — one K8s Job per experiment run. Each runs a standalone container that executes one experiment and writes results to shared storage.
3. **Shared Storage** — NFS or EFS PersistentVolume. Holds datasets (read-only to workers), config, and outputs (read-write by workers, read by coordinator).

All interaction happens through the coordinator's HTTP API — there is no CLI. Users submit experiments, manage datasets, view results, and reclassify findings via HTTP requests.

### 15.2 Container Images

Two images, built from the same codebase:

**Dockerfile.worker**

```dockerfile
FROM python:3.12-slim

# Install Semgrep
RUN pip install semgrep && semgrep --version

# Install framework
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

# Download offline Semgrep rules (no cloud dependency at runtime)
RUN semgrep --config p/owasp-top-ten --dry-run /dev/null 2>/dev/null || true

ENTRYPOINT ["python", "-m", "sec_review_framework.worker"]
```

**Dockerfile.coordinator**

```dockerfile
FROM python:3.12-slim

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir ".[coordinator]"

# coordinator extras: fastapi, uvicorn, kubernetes, prometheus-client

EXPOSE 8080
ENTRYPOINT ["uvicorn", "sec_review_framework.coordinator:app", "--host", "0.0.0.0", "--port", "8080"]
```

### 15.3 Kubernetes Manifests

**Namespace and storage**

```yaml
# k8s/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: sec-review
---
# k8s/shared-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: sec-review-data
  namespace: sec-review
spec:
  accessModes:
    - ReadWriteMany          # NFS/EFS — workers write, coordinator reads
  resources:
    requests:
      storage: 50Gi
  storageClassName: efs-sc   # adjust for your cluster
```

**Coordinator Deployment**

```yaml
# k8s/coordinator-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: coordinator
  namespace: sec-review
  labels:
    app: sec-review-coordinator
spec:
  replicas: 1
  selector:
    matchLabels:
      app: sec-review-coordinator
  template:
    metadata:
      labels:
        app: sec-review-coordinator
    spec:
      serviceAccountName: coordinator-sa   # needs Job create/list/delete permissions
      containers:
        - name: coordinator
          image: sec-review-coordinator:latest
          ports:
            - containerPort: 8080
          volumeMounts:
            - name: shared-data
              mountPath: /data
            - name: config
              mountPath: /app/config
          env:
            - name: STORAGE_ROOT
              value: /data
            - name: WORKER_IMAGE
              value: sec-review-worker:latest
            - name: NAMESPACE
              value: sec-review
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              cpu: 1000m
              memory: 1Gi
      volumes:
        - name: shared-data
          persistentVolumeClaim:
            claimName: sec-review-data
        - name: config
          configMap:
            name: experiment-config
---
# k8s/coordinator-service.yaml
apiVersion: v1
kind: Service
metadata:
  name: coordinator
  namespace: sec-review
  labels:
    app: sec-review-coordinator
spec:
  selector:
    app: sec-review-coordinator
  ports:
    - port: 8080
      targetPort: 8080
```

**Secrets**

```yaml
# k8s/secrets.yaml
# Placeholder — in production, use sealed-secrets, external-secrets-operator,
# or AWS Secrets Manager CSI driver.
apiVersion: v1
kind: Secret
metadata:
  name: llm-api-keys
  namespace: sec-review
type: Opaque
stringData:
  OPENAI_API_KEY: "..."
  ANTHROPIC_API_KEY: "..."
  GEMINI_API_KEY: "..."
  MISTRAL_API_KEY: "..."
  COHERE_API_KEY: "..."
```

**Network Policy — Worker Egress Restriction**

```yaml
# k8s/network-policy.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: worker-egress
  namespace: sec-review
spec:
  podSelector:
    matchLabels:
      app: sec-review-worker
  policyTypes:
    - Egress
  egress:
    # Allow DNS resolution
    - to: []
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # Allow HTTPS to LLM provider endpoints only
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0        # narrowed by ports; for tighter control,
                                     # resolve provider IPs and list explicitly
      ports:
        - protocol: TCP
          port: 443
    # Allow NFS to shared storage
    - to:
        - podSelector: {}           # within namespace
      ports:
        - protocol: TCP
          port: 2049
```

For tighter egress control, replace the `0.0.0.0/0` CIDR with explicit IP ranges for each LLM provider. This requires maintaining the IP list as providers change, but prevents exfiltration to arbitrary HTTPS endpoints.

**RBAC — Coordinator Service Account**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: coordinator-sa
  namespace: sec-review
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: job-manager
  namespace: sec-review
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "watch", "delete"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]     # for job status
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]                      # for debugging failed workers
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: coordinator-job-manager
  namespace: sec-review
subjects:
  - kind: ServiceAccount
    name: coordinator-sa
roleRef:
  kind: Role
  name: job-manager
  apiGroup: rbac.authorization.k8s.io
```

### 15.4 Observability

**Prometheus Metrics** — exposed by the coordinator at `/metrics`:

```python
from prometheus_client import Counter, Gauge, Histogram, Info

# Experiment metrics
experiments_submitted = Counter("sec_review_experiments_submitted_total", "Total experiments submitted")
experiments_completed = Counter("sec_review_experiments_completed_total", "Total experiments completed")
experiment_runs_total = Gauge("sec_review_experiment_runs_total", "Total runs in current experiment",
                         ["experiment_id"])

# Per-run metrics
runs_completed = Counter("sec_review_runs_completed_total", "Completed experiment runs",
                         ["model", "strategy", "status"])
run_duration = Histogram("sec_review_run_duration_seconds", "Run duration",
                         ["model", "strategy"],
                         buckets=[60, 120, 300, 600, 900, 1200, 1800])
run_cost = Histogram("sec_review_run_cost_usd", "Run cost in USD",
                     ["model", "strategy"],
                     buckets=[1, 5, 10, 20, 50, 100])
run_tokens = Counter("sec_review_tokens_total", "Total tokens consumed",
                     ["model", "direction"])  # direction: input/output

# Quality metrics (updated when evaluation completes)
run_precision = Gauge("sec_review_precision", "Precision per run",
                      ["experiment_id", "run_id"])
run_recall = Gauge("sec_review_recall", "Recall per run",
                   ["experiment_id", "run_id"])
run_f1 = Gauge("sec_review_f1", "F1 per run",
               ["experiment_id", "run_id"])

# Concurrency
active_jobs_by_model = Gauge("sec_review_active_jobs", "Currently running jobs",
                             ["model"])
pending_jobs = Gauge("sec_review_pending_jobs", "Jobs waiting to be scheduled")
```

**ServiceMonitor** for Prometheus Operator:

```yaml
# k8s/servicemonitor.yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: sec-review-coordinator
  namespace: sec-review
spec:
  selector:
    matchLabels:
      app: sec-review-coordinator
  endpoints:
    - port: "8080"
      path: /metrics
      interval: 15s
```

**Grafana Dashboard** — pre-built dashboard (`k8s/grafana-dashboard.json`) with panels for:

- Experiment progress (runs completed / total over time)
- Active jobs by model (stacked area)
- Run duration distribution (heatmap by model × strategy)
- Cost accumulation (running total per experiment)
- Precision/recall scatter (per experiment, colored by model)
- Token usage by model (bar chart)
- Failed runs (table with error messages)

### 15.5 Usage Examples

All interaction is via the coordinator's HTTP API. Access via `kubectl port-forward`, an Ingress, or any in-cluster client.

```bash
# Port-forward for local access
kubectl port-forward -n sec-review svc/coordinator 8080:8080
COORD=http://localhost:8080
```

**Submit an experiment:**

```bash
curl -X POST $COORD/experiments \
  -H "Content-Type: application/json" \
  -d @config/experiments.yaml.json

# Response:
# {"experiment_id": "experiment_2026_04_16_v1", "total_runs": 100}
```

**Check progress:**

```bash
curl $COORD/experiments/experiment_2026_04_16_v1

# Response:
# {
#   "experiment_id": "experiment_2026_04_16_v1",
#   "total": 100,
#   "completed": 37,
#   "failed": 1,
#   "running": 12,
#   "pending": 50,
#   "estimated_cost_so_far": 487.20
# }
```

**Get results:**

```bash
# JSON matrix report
curl $COORD/experiments/experiment_2026_04_16_v1/results

# Markdown matrix report
curl $COORD/experiments/experiment_2026_04_16_v1/results/markdown

# Single run detail
curl $COORD/experiments/experiment_2026_04_16_v1/runs/gpt-4o_single_agent_with_tools_default_none
```

**Manage datasets:**

```bash
# Import a CVE
curl -X POST $COORD/datasets/import-cve \
  -H "Content-Type: application/json" \
  -d '{
    "cve_id": "CVE-2023-12345",
    "repo_url": "https://github.com/example/repo",
    "fix_commit_sha": "abc123",
    "dataset_name": "example-cve-2023",
    "cwe_id": "CWE-89",
    "vuln_class": "sqli",
    "severity": "high",
    "description": "SQL injection in user search"
  }'

# List datasets
curl $COORD/datasets

# View labels
curl $COORD/datasets/example-cve-2023/labels
```

**Reclassify a finding:**

```bash
curl -X POST $COORD/experiments/experiment_2026_04_16_v1/runs/gpt-4o_single_agent_with_tools_default_none/reclassify \
  -H "Content-Type: application/json" \
  -d '{"finding_id": "abc123", "status": "unlabeled_real", "note": "Real path traversal"}'
```

**Compare experiments:**

```bash
curl -X POST $COORD/feedback/compare \
  -H "Content-Type: application/json" \
  -d '{"experiment_a_id": "experiment_2026_04_16_v1", "experiment_b_id": "experiment_2026_04_20_v2"}'
```

**Browse reference data:**

```bash
curl $COORD/models      # available model providers
curl $COORD/strategies  # available scan strategies
curl $COORD/profiles    # review profiles with descriptions
curl $COORD/templates   # vuln injection templates
```

All state lives in K8s (job status) and shared storage (results). The coordinator is stateless — it can be restarted without losing experiment progress.

### 15.6 Scaling Considerations

| Factor | Constraint | Mitigation |
|--------|-----------|------------|
| LLM rate limits | Provider-specific RPM/TPM limits | Per-model concurrency caps in `concurrency.yaml` |
| Node resources | Each worker needs ~1-2GB RAM for conversation history | Set pod resource limits, use cluster autoscaler |
| Shared storage IOPS | Many workers writing simultaneously | Use EFS with bursting or provisioned throughput |
| Job scheduling | K8s Job creation overhead at scale | Coordinator groups Job creation, 10s poll interval |
| Provider outages | One provider down shouldn't block the whole experiment | Coordinator continues scheduling other models; failed jobs recorded, retryable |

For a 100-run experiment with `max_parallel_runs: 10` and average run duration of 10 minutes, expected wall-clock time is ~100 minutes (10 waves of 10). With concurrency caps, some models may take longer if their cap is lower than the global cap.

### 15.7 Log Aggregation

Worker pods are ephemeral — once a Job completes and the pod is garbage-collected, logs vanish. Two-layer solution:

**Layer 1: Conversation transcripts to shared storage.** Each worker writes `conversation.jsonl` incrementally during the run — one JSON line per message exchange. Survives OOM kills and evictions (partial transcripts are still useful). Written to the same output directory as `run_result.json`.

**Layer 2: Structured stdout to Loki via Fluentd DaemonSet.** Workers emit structured JSON to stdout with `experiment_id` and `run_id` fields. A Fluentd DaemonSet on each node tails `/var/log/pods/sec-review_*/*/*.log`, filters for `app=sec-review-worker`, and forwards to Loki.

```yaml
# Fluentd ConfigMap (excerpt)
apiVersion: v1
kind: ConfigMap
metadata:
  name: fluentd-worker-config
  namespace: logging
data:
  fluent.conf: |
    <source>
      @type tail
      path /var/log/pods/sec-review_*/*/*.log
      pos_file /var/log/fluentd-worker.pos
      tag kubernetes.worker.*
      <parse>
        @type cri
      </parse>
    </source>

    <filter kubernetes.worker.**>
      @type grep
      <regexp>
        key $.kubernetes.labels.app
        pattern ^sec-review-worker$
      </regexp>
    </filter>

    <match kubernetes.worker.**>
      @type loki
      url http://loki.monitoring.svc.cluster.local:3100
      flush_interval 5s
      <label>
        app $.kubernetes.labels.app
        experiment_id $.kubernetes.labels.experiment_id
        run_id $.kubernetes.labels.run_id
      </label>
    </match>
```

Worker pods must carry `experiment_id` and `run_id` as pod labels (already set by the coordinator's Job template).

### 15.8 Dataset-Builder Jobs

CVE imports require cloning repos from GitHub, but worker pods have egress locked to LLM provider endpoints. A separate `dataset-builder` Job type has its own NetworkPolicy permitting GitHub access.

```yaml
# k8s/network-policy-dataset-builder.yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: dataset-builder-egress
  namespace: sec-review
spec:
  podSelector:
    matchLabels:
      role: dataset-builder
  policyTypes:
    - Egress
  egress:
    # GitHub HTTPS
    - ports:
        - protocol: TCP
          port: 443
      to:
        - ipBlock:
            cidr: 140.82.112.0/20   # github.com (verify current ranges)
        - ipBlock:
            cidr: 192.30.252.0/22
    # DNS
    - ports:
        - protocol: UDP
          port: 53
    # NFS/EFS
    - ports:
        - protocol: TCP
          port: 2049
```

The coordinator creates `dataset-builder` Jobs when `POST /datasets/import-cve` is called. These jobs use a separate container image (`Dockerfile.dataset-builder`) with git installed, and mount the shared datasets volume read-write. Workers mount it read-only.

### 15.9 RBAC Hardening

The coordinator's ServiceAccount can create Jobs, but nothing prevents it from creating Jobs with arbitrary images. A Gatekeeper ConstraintTemplate restricts Jobs in the `sec-review` namespace to approved images only.

```yaml
# k8s/gatekeeper-constraint.yaml
apiVersion: templates.gatekeeper.sh/v1
kind: ConstraintTemplate
metadata:
  name: secreviewjobimage
spec:
  crd:
    spec:
      names:
        kind: SecReviewJobImage
      validation:
        openAPIV3Schema:
          type: object
          properties:
            allowedImages:
              type: array
              items:
                type: string
  targets:
    - target: admission.k8s.gatekeeper.sh
      rego: |
        package secreviewjobimage

        violation[{"msg": msg}] {
          input.review.kind.kind == "Job"
          containers := array.concat(
            object.get(input.review.object.spec.template.spec, "containers", []),
            object.get(input.review.object.spec.template.spec, "initContainers", [])
          )
          container := containers[_]
          image := container.image
          not image_is_allowed(image)
          msg := sprintf("Image %q not permitted in sec-review namespace", [image])
        }

        image_is_allowed(image) {
          image == input.parameters.allowedImages[_]
        }
---
apiVersion: constraints.gatekeeper.sh/v1beta1
kind: SecReviewJobImage
metadata:
  name: restrict-sec-review-job-images
spec:
  enforcementAction: deny
  match:
    kinds:
      - apiGroups: ["batch"]
        kinds: ["Job"]
    namespaces:
      - sec-review
  parameters:
    allowedImages:
      - "ghcr.io/myorg/sec-review/worker:v1.0.0"
      - "ghcr.io/myorg/sec-review/dataset-builder:v1.0.0"
```

Defense-in-depth: namespace-scoped RBAC (limits what API objects the coordinator can touch) + Gatekeeper policy (limits what those objects may contain). Neither alone is sufficient.

---

## 16. Web Frontend

A lightweight single-page web application served by the coordinator. Thin client — all logic lives in the coordinator API; the frontend is pure presentation and form submission.

### 16.1 Technology

| Choice | Rationale |
|--------|-----------|
| React + TypeScript | Team is technical, React is widely known, TS catches API contract drift |
| Vite | Fast build, no heavy config |
| Tailwind CSS | Utility-first, no design system needed for internal tooling |
| CodeMirror 6 | Syntax-highlighted code viewer for source files, diffs, and conversation transcripts |
| Bundled into coordinator image | Single deployment unit — `Dockerfile.coordinator` builds the frontend and serves static files via FastAPI's `StaticFiles` mount |

No separate frontend deployment, no ingress, no CDN. The coordinator serves the SPA at `/` and the API at `/api/*`. Accessed via the same `kubectl port-forward` or Ingress as the API.

**Dark mode**: Tailwind's `dark:` variant with `class` strategy. Defaults to system preference via `prefers-color-scheme`, togglable via a button in the nav bar. Persisted to `localStorage`. All components use semantic color tokens (`bg-surface`, `text-primary`, `border-subtle`) mapped to light/dark values — no per-component color overrides.

```python
# In coordinator.py
from fastapi.staticfiles import StaticFiles

app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")
```

### 16.2 Pages

#### Dashboard (`/`)

Overview of framework state. First thing a user sees.

- **Active experiments** — table: experiment_id, progress bar (completed/total), running cost, elapsed time, status. Click through to experiment detail.
- **Recent experiments** — last 10 completed experiments with headline metrics (best model, best strategy).
- **System health** — coordinator status, active jobs by model (bar chart from Prometheus metrics), storage usage.

#### Experiment Submission (`/experiments/new`)

Form-driven experiment creation. Replaces the `curl -X POST /experiments` workflow.

- **Dataset selector** — dropdown of available datasets with label count and size.
- **Model picker** — checkboxes for available models. Shows pricing per model.
- **Strategy picker** — checkboxes for strategies. Each expands to show its config (max_turns, skip_patterns, etc.).
- **Profile picker** — radio buttons for review profiles. Shows the prompt modifier text on hover.
- **Dimension toggles** — tool_variants (with/without), verification (none/with), parallel modes.
- **Repetitions** — number input, defaults to 1.
- **Cross-model verification** — optional model selector for verifier.
- **Cost estimate** — live-updating estimate as the user changes selections. Calls `POST /experiments/estimate` on every change (debounced). Shows total runs, estimated cost, breakdown by model.
- **Spend cap** — number input, pre-filled from estimate + 20% buffer.
- **Submit** — creates the experiment. Redirects to experiment detail page.

#### Experiment Detail (`/experiments/:id`)

Real-time progress and results for a single experiment.

**While running:**
- Progress bar with counts: completed / running / pending / failed.
- Running cost vs. spend cap (progress bar with warning color near cap).
- Live table of completed runs: experiment_id, status, precision, recall, cost, duration. Rows appear as jobs complete (poll every 10s).
- Cancel button.

**After completion:**
- **Comparative matrix** — the full matrix table from the report. Sortable by any column. Heatmap coloring on precision/recall/F1 cells (green = good, red = bad).
- **Dimension analysis charts** — bar charts for model comparison, strategy comparison, tool access impact, profile impact, verification impact. Generated client-side from the JSON report data.
- **Cost analysis** — table: model, avg cost/run, total experiment cost, cost per true positive.
- **Evidence quality breakdown** — stacked bar chart per model: strong/adequate/weak.
- **Statistical significance** (when `num_repetitions > 1`) — confidence intervals on metrics, significance markers on pairwise comparisons.
- **Findings explorer** — expandable table of all findings across all runs. Filter by: model, strategy, match_status (TP/FP/FN/unlabeled_real), vuln_class, severity. **Free-text search bar** at the top searches across title, description, recommendation, and raw LLM output (calls `GET /experiments/:id/findings/search?q=...`). Click to expand and see full LLM description, verification evidence, matched label. Expansion renders the LLM description in a CodeMirror instance (read-only, markdown mode) so code blocks and file references are syntax-highlighted.
- **Reclassify button** on each FP finding — opens modal to reclassify as `unlabeled_real` with a note. Calls `POST /experiments/:id/runs/:run_id/reclassify`.
- **Run comparison** — "Compare" button in the comparative matrix. Select any two rows (click first, shift-click second), then "Compare Selected". Opens a side-by-side view (`/experiments/:id/compare?a=run_a&b=run_b`) showing:
  - Header: both runs' config (model, strategy, tools, profile) and metrics side by side with deltas highlighted.
  - Three-column findings table grouped by `FindingIdentity`:
    - **Found by both** — shows both descriptions side by side in CodeMirror. Useful for comparing explanation quality between models.
    - **Only in Run A** — findings that A found but B missed. Each shows the LLM description and the matching ground truth label (if TP) or "not in ground truth" (if FP).
    - **Only in Run B** — same, reversed.
  - Summary: "Run A found 3 vulns that B missed. Run B found 1 vuln that A missed. 12 vulns found by both."
- **Download reports** — button in the experiment header. Calls `GET /experiments/:id/results/download`. Returns a .zip containing `matrix_report.md`, `matrix_report.json`, and per-run `report.md` / `report.json` files.

#### CVE Discovery (`/datasets/discover`)

The workflow that most benefits from a UI. Reviewing ranked CVE candidates in JSON is impractical; a table with filtering and one-click import is essential.

- **Criteria form** — fields matching `CVESelectionCriteria`: language checkboxes, vuln class multiselect, severity range slider, patch size range (min/max lines), repo size range (min/max KLOC), date range picker. "Search" button calls `POST /datasets/discover-cves`.
- **Results table** — sortable, filterable table of candidates:

  | Score | CVE ID | Vuln Class | Severity | Language | Repo | Files Changed | Lines | Importable |
  |-------|--------|------------|----------|----------|------|---------------|-------|------------|
  | 0.92 | CVE-2024-1234 | sqli | high | python | example/project | 2 | 18 | yes |

  Each row expandable to show: description, affected files, score breakdown (radar chart or bar), link to GitHub advisory and fix commit.
- **Bulk select + import** — checkboxes on each row. "Import Selected" button. Optional dataset name prefix. Shows confirmation modal with total count before triggering imports.
- **Resolution tab** — separate tab for manual mode. Single text input for CVE ID, "Resolve" button. Shows resolved metadata. "Import" button if resolution succeeds.

#### Dataset Management (`/datasets`)

- **Dataset list** — table: name, source (CVE/injected/manual), label count, repo size, created date.
- **Dataset detail** — two-panel layout:
  - **Left panel: file tree** — collapsible directory tree fetched from `GET /datasets/{name}/tree`. Files show language icon and size. Files containing labeled vulns are badged with a count.
  - **Right panel: file viewer** — clicking a file loads its content via `GET /datasets/{name}/file?path=...` into a CodeMirror instance with syntax highlighting (language auto-detected from extension), read-only mode, and line numbers. Ground truth labels for the file are rendered as inline annotations — colored gutters marks on labeled line ranges (red for critical/high, yellow for medium, blue for low) with hover tooltips showing the label details (vuln_class, CWE, description, source).
  - **Label table** — below the file viewer. Full label list for the dataset, sortable/filterable. Clicking a label row navigates the file viewer to that file and scrolls to the labeled line range.
- **Inject vulnerability** — multi-step workflow:
  1. **Select template** — templates listed in a table grouped by vuln_class. Each row shows: ID, language, CWE, severity, description, anchor pattern. Click to select.
  2. **Select target file** — interactive repo file tree (fetched from dataset). Files are filtered to those matching the template's language. Clicking a file shows its content in a read-only code viewer with line numbers.
  3. **Substitutions** — if the template has `{{PLACEHOLDER}}` tokens, a form appears with one input per placeholder (e.g., `TABLE_NAME`, `COLUMN`). Defaults are suggested from the template.
  4. **Preview** — clicking "Preview" calls `POST /datasets/{name}/inject/preview`. The result renders as:
     - A side-by-side diff view (before / after) with syntax highlighting. Injected lines highlighted in red/green.
     - The label that will be created: file path, line range, vuln_class, CWE, severity.
     - A warning if the anchor pattern matched multiple locations (shows which one was selected).
  5. **Confirm** — "Inject" button is only enabled after preview. Calls `POST /datasets/{name}/inject`. Success shows a toast with the new label ID and updates the label table.
  6. **Undo** — the injection is a file modification on shared storage. An "Undo last injection" button is available for 60 seconds after injection, calling a revert endpoint that restores the file from the pre-injection snapshot (stored temporarily during injection).
- **Generate diff dataset** — form: select clean repo, select templates to inject, target files. Each template shows a preview. "Generate" triggers `DiffDatasetGenerator` and shows progress.

#### Feedback (`/feedback`)

- **Experiment comparison** — two dropdowns to select experiments A and B. "Compare" button. Results show:
  - Metric deltas table (run_id, precision delta, recall delta, highlighted regressions in red).
  - Persistent FP patterns — table with model, vuln_class, pattern description, count, suggested action.
  - Stability analysis — findings that appeared/disappeared between experiments, grouped by `FindingIdentity`.
- **FP pattern browser** — select an experiment, see recurring false positive patterns. Link to the specific findings.

#### Run Detail (`/experiments/:experiment_id/runs/:run_id`)

Deep dive into a single experiment run.

- **Header** — model, strategy, tool_variant, profile, verification, status, duration, cost. Download button for this run's reports.
- **Prompt snapshot** — collapsible section showing exact system prompt, user message template, profile modifier, finding format used. Rendered in CodeMirror (read-only) with diff highlighting if the prompts differ from the experiment default.
- **Metrics** — precision, recall, F1, FPR, evidence quality breakdown.
- **Findings table** — all findings with match status, free-text search bar. Expandable to show:
  - LLM description and verification evidence (CodeMirror, markdown mode).
  - **Source context** — the actual source file around the finding's line range, loaded from the dataset via `GET /datasets/{name}/file`. Rendered in CodeMirror with the finding's line range highlighted. Ground truth label (if matched) shown as a separate annotation so the user can see how well the model's reported location aligns with the label.
  - Reclassify button (for FP findings).
- **Tool call audit** — table of tool calls with name, input (truncated, expandable), timestamp. Flagged rows for calls with URL-like strings.
- **Conversation transcript** — collapsible, full message-by-message exchange from `conversation.jsonl`. Each message rendered in CodeMirror with role-based coloring (user=blue, assistant=green, tool=gray). Useful for debugging why the model did or didn't find something.

### 16.3 Directory Structure

```
frontend/
├── package.json
├── tsconfig.json
├── vite.config.ts
├── index.html
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/
│   │   └── client.ts            # typed API client (generated from OpenAPI spec)
│   ├── pages/
│   │   ├── Dashboard.tsx
│   │   ├── ExperimentNew.tsx
│   │   ├── ExperimentDetail.tsx
│   │   ├── RunDetail.tsx
│   │   ├── RunCompare.tsx        # side-by-side run comparison
│   │   ├── CVEDiscovery.tsx
│   │   ├── Datasets.tsx
│   │   ├── DatasetDetail.tsx
│   │   └── Feedback.tsx
│   ├── components/
│   │   ├── MatrixTable.tsx       # sortable, heatmapped comparative matrix
│   │   ├── FindingsExplorer.tsx  # filterable findings table with search + expand
│   │   ├── FindingsSearch.tsx    # free-text search bar, calls /findings/search
│   │   ├── CodeViewer.tsx        # CodeMirror wrapper: syntax highlight, annotations, line gutter
│   │   ├── FileTree.tsx          # collapsible directory tree with label badges
│   │   ├── DiffViewer.tsx        # side-by-side diff (for injection preview + run comparison)
│   │   ├── CostEstimate.tsx      # live cost estimate widget
│   │   ├── ProgressBar.tsx
│   │   ├── DimensionChart.tsx    # bar chart for dimension analysis
│   │   ├── CVECandidateTable.tsx # discovery results with bulk select
│   │   ├── ConversationViewer.tsx # message-by-message transcript with role coloring
│   │   ├── ThemeToggle.tsx       # dark/light mode toggle
│   │   └── DownloadButton.tsx    # report download (.zip)
│   └── hooks/
│       ├── useExperiment.ts      # polling hook for experiment progress
│       ├── useEstimate.ts        # debounced cost estimate
│       ├── useTheme.ts           # dark mode state + localStorage persistence
│       └── useSearch.ts          # debounced free-text search
└── dist/                         # built output, copied into coordinator image
```

### 16.4 API Client Generation

The frontend API client is auto-generated from the coordinator's OpenAPI spec (FastAPI provides this at `/openapi.json`). This keeps the frontend in sync with the API without manual type definitions.

```bash
# Generate typed client from running coordinator
npx openapi-typescript-codegen \
  --input http://localhost:8080/openapi.json \
  --output frontend/src/api/generated \
  --client fetch
```

Re-run after API changes. The generated types are committed to the repo so the frontend builds without a running coordinator.

### 16.5 Build and Deployment

The frontend is built during the coordinator Docker image build:

```dockerfile
# Dockerfile.coordinator (updated)
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir ".[coordinator]"
COPY --from=frontend-build /frontend/dist /app/frontend/dist

EXPOSE 8080
ENTRYPOINT ["uvicorn", "sec_review_framework.coordinator:app", "--host", "0.0.0.0", "--port", "8080"]
```

One image, one deployment, one port. The coordinator serves the SPA at `/` and the API at `/api/*`. No CORS issues, no separate frontend ingress.

---

## 17. Extension Points

### Adding a New Model Provider

1. Create `src/sec_review_framework/models/{provider_name}.py`
2. Implement `ModelProvider` ABC
3. Register in `ModelProviderFactory`
4. Create a probe in `src/sec_review_framework/models/probes/{provider_name}_probe.py`
   implementing the `ProviderProbe` protocol and returning a `ProviderSnapshot`
5. Register the probe in `coordinator.py` where `build_probes()` assembles the list
6. Add an entry to `ENV_VAR_FOR_PROVIDER` in `src/sec_review_framework/models/providers.py`
7. Add pricing entry to `config/pricing.yaml`

### Adding a New Strategy

1. Create `src/sec_review_framework/strategies/{strategy_name}.py`
2. Implement `ScanStrategy` ABC — must return `StrategyOutput` (not bare `list[Finding]`)
3. Register in `StrategyFactory`
4. Add to `StrategyName` enum and `experiments.yaml`

### Adding a New Vulnerability Class

1. Add to `VulnClass` enum
2. Add entry to `config/vuln_classes.yaml`
3. Add specialist system prompt to `VULN_CLASS_SYSTEM_PROMPTS`
4. Add mechanism keywords to `EvidenceQualityAssessor._get_mechanism_keywords()`
5. Create injection templates under `datasets/templates/{vuln_class}/`

### Adding a New Injection Template

Templates are pure data — no code changes. Create YAML under `datasets/templates/{vuln_class}/{template_id}.yaml`:

```yaml
id: "sqli_format_string_python"
vuln_class: "sqli"
cwe_id: "CWE-89"
language: "python"
severity: "high"
description: "SQL query via string formatting with unsanitized user input"
confidence: "confirmed"
anchor_pattern: "def \\w+\\(.*request.*\\):"
anchor_mode: "after"
patch_template: |
  +    query = "SELECT * FROM users WHERE username = '%s'" % request.GET.get('username')
  +    cursor.execute(query)
  +    results = cursor.fetchall()
```

### Adding a New Review Profile

Add an entry to `config/review_profiles.yaml` with a `name`, `description`, and `system_prompt_modifier`. Then reference the name in `experiments.yaml`. No code changes needed.

### Adding a New Tool

1. Create `src/sec_review_framework/tools/{tool_name}.py`
2. Implement `Tool` ABC
3. Register in `ToolRegistryFactory` — decide whether it appears in `WITH_TOOLS` only or always

---

## 18. Tool Extensions (MCP-backed)

Tool extensions are an optional, orthogonal experiment axis that adds language-aware and documentation-backed tools to the reviewer. They are implemented via the Model Context Protocol (MCP) using in-pod subprocesses over stdio.

### Overview

The framework defines three optional tool extensions, each exposing a set of MCP tools that augment the base `ToolVariant` (with_tools / without_tools):

- **TREE_SITTER** (`tools/extensions/tree_sitter_ext.py` + `tree_sitter_server.py`): Symbol and AST querying via the tree-sitter parser
  - Tools: `ts_find_symbol`, `ts_get_ast`, `ts_list_functions`, `ts_query`
  - Covers: Python, JavaScript, TypeScript, Go, Java, Ruby, Rust, C/C++, PHP, Bash, YAML, JSON, HCL, Dockerfile, SQL
  
- **LSP** (`lsp_ext.py` + `lsp_server.py`): Language-aware code navigation via LSP (Language Server Protocol)
  - Tools: `lsp_definition`, `lsp_references`, `lsp_hover`, `lsp_document_symbols`, `lsp_workspace_symbols`
  - Multiplexer auto-starts per-language servers: pyright (Python), gopls (Go), typescript-language-server (JS/TS), rust-analyzer (Rust), clangd (C/C++)
  
- **DEVDOCS** (`devdocs_ext.py` + `devdocs_server.py`): Offline API documentation search
  - Tools: `doc_search`, `doc_fetch`, `doc_list_docsets`
  - Docsets synced from DevDocs.io and pre-populated on the shared PVC at `/data/devdocs` by the devdocs-sync Helm job

### Design Rationale

**MCP over loopback-only subprocesses**: Each extension runs as a dedicated stdio MCP server subprocess within the worker pod. This design aligns with the VPC-only constraint (no external network calls) while keeping the worker pod image size bounded — complex runtimes (gopls, clangd, rust-analyzer) run in a subprocess, not in the main Python process. Subprocesses are terminated when the run completes, and audit logging is preserved (MCP tool calls go through `ToolRegistry.invoke()`).

**Why not sidecars?** Separate sidecar pods would require inter-pod networking, breaking the VPC-only model. Subprocesses within the pod are simpler for the K8s-less test environment and local dev.

**Sync Tool ABC preserved**: The `Tool` abstract base class remains synchronous. The `MCPClient` class marshals async MCP SDK calls onto a dedicated background thread's event loop, preserving the sync interface and allowing `ToolRegistry.invoke()` to work unchanged.

### Configuration & Availability

Tool extensions are disabled by default everywhere. To enable:

```yaml
# helm/sec-review/values.yaml
workerTools:
  treeSitter:
    enabled: true
    # grammars listed for frontend awareness
  lsp:
    enabled: true
    # servers listed for frontend awareness
  devdocs:
    enabled: true
    docsetsPath: /data/devdocs
    docsets:
      - python~3.12
      - javascript
      - go
```

The coordinator derives `TOOL_EXT_TREE_SITTER_AVAILABLE`, `TOOL_EXT_LSP_AVAILABLE`, `TOOL_EXT_DEVDOCS_AVAILABLE` env vars from these settings and injects them into worker pod specs. At runtime, `ToolRegistryFactory` checks the env vars and invokes the corresponding extension builder (registered via `register_extension_builder()` in each extension module). Extension builders are guarded with try/except in `tools/registry.py` — a missing optional dependency (e.g., LSP language servers not installed) logs a warning but does not crash the worker.

### Run ID and Experiment Key Format

- **Run ID**: When `tool_extensions` is empty, run IDs are byte-identical to legacy pre-extension runs. Non-empty extension sets append `_ext-<sorted+joined>` (e.g., `_ext-devdocs+lsp`).
- **Experiment key** (for feedback tracking): The 4-element tuple is `(model_id, strategy, tool_variant, tuple(sorted(tool_extensions)))`. Runs differing only in extensions are tracked separately and do not merge across experiments.

### Persistence

The `runs` table gained a `tool_extensions TEXT` column. The value is a comma-separated, lexicographically sorted list of extension names (e.g., `"DEVDOCS,LSP"` for both). The DB migration is idempotent.

### Tool Stub Replacement

The legacy `DocLookupTool` stub is now skipped when `DEVDOCS` is in the active extension set. The MCP-backed `doc_search` and `doc_fetch` tools replace it, offering real offline documentation rather than a placeholder.

### Backwards Compatibility

- Existing runs with `tool_extensions={}` (empty set) produce identical run IDs to pre-Chunk-1 code.
- Runs without extensions do not incur the subprocess overhead.
- All defaults are empty — teams opting not to use extensions see zero impact.
- The database migration is backwards-compatible: old runs lack the `tool_extensions` column value (NULL → treated as empty set on read).

---

## 19. Future Work: Iterative Review

The current framework runs each experiment as a single shot — the model reviews the codebase once and produces findings. Production review workflows are often iterative: an initial review produces findings, the developer fixes some issues, and the reviewer checks again.

### What Iterative Review Would Test

- **Stability**: Does the same model produce the same findings on a second pass? Or does it flip-flop (finding X in run 1, not in run 2, finding it again in run 3)?
- **Convergence**: After N rounds, does the finding count stabilize?
- **Diminishing returns**: Does the second pass find meaningful new issues, or just noise?
- **Context handling**: When given prior findings as context, does the model build on them effectively or get confused?

### Sketch Design

```python
class IterativeExperiment(BaseModel):
    base_experiment: ExperimentRun
    num_passes: int                    # how many review passes to run
    pass_mode: str                     # "fresh" (no context) | "informed" (prior findings as context)

class IterativeResult(BaseModel):
    passes: list[RunResult]            # one per pass
    stability_score: float             # what fraction of findings appear in all passes
    new_findings_per_pass: list[int]   # how many new findings each pass found
    flip_flop_findings: list[Finding]  # findings that appear, disappear, reappear
```

This is deferred because it requires:
1. A clear definition of "same finding" across passes (for stability measurement)
2. A mechanism for passing prior findings as context without biasing the model
3. Careful cost management — iterative review multiplies token usage by `num_passes`

When the team is ready to explore this, it can be added as a new experiment mode alongside the existing single-shot matrix.

---

## Appendix: Key Dependency Flow

```
HTTP POST /experiments → ExperimentCoordinator
    → ExperimentMatrix.expand()
        → list[ExperimentRun]
        → For each run: create K8s Job (respecting per-model concurrency caps)
            → Worker Pod starts: ExperimentWorker.run()
                → ModelProviderFactory.create()    → ModelProvider
            → StrategyFactory.create()         → ScanStrategy
            → ToolRegistryFactory.create()     → ToolRegistry
            → ProfileRegistry.get()            → ReviewProfile
            → LabelStore.load()                → list[GroundTruthLabel]
            → ScanStrategy.run(target, model, tools, config)
                → build_system_prompt(base, config)  ← injects ReviewProfile
                → run_subagents() or run_agentic_loop()
                    → ModelProvider.complete(messages, tools)
                    → ToolRegistry.invoke(name, input, call_id)
                        → ToolCallAuditLog.record(...)
                → FindingParser.parse(raw_output)
                → deduplicate() → StrategyOutput
            → [if verification_variant == WITH_VERIFICATION]
                → Verifier.verify(candidates, target, model, tools)
                    → VerificationResult (verified / rejected / uncertain)
            → Evaluator.evaluate(findings, labels)
                → EvidenceQualityAssessor.assess() per TP
                → EvaluationResult
            → CostCalculator.compute(model_id, tokens)
            → RunResult(experiment, findings, evaluation, cost, ...)
            → Write RunResult + artifacts to shared storage
            → ReportGenerator.render_run(result, output_dir)
    → ExperimentCoordinator detects all jobs complete
        → collect_results() from shared storage
        → ReportGenerator.render_matrix(results, output_root)
        → Update Prometheus metrics
    → [optional] FeedbackTracker.compare_experiments(experiment_a, experiment_b)
```
