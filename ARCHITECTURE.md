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
16. [Extension Points](#16-extension-points)
17. [Future Work: Iterative Review](#17-future-work-iterative-review)

---

## 1. System Overview

### System Diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  COORDINATOR SERVICE (K8s Deployment, 1 replica)                            │
│                                                                             │
│  HTTP API — the single management interface for the entire framework        │
│                                                                             │
│  Batches:                                                                   │
│    POST /batches                      — submit new batch (config as body)   │
│    GET  /batches                      — list all batches                    │
│    GET  /batches/{id}                 — status + progress                   │
│    GET  /batches/{id}/results         — matrix report (JSON)               │
│    GET  /batches/{id}/results/markdown — matrix report (Markdown)          │
│    GET  /batches/{id}/runs            — list all runs                      │
│    GET  /batches/{id}/runs/{run_id}   — single run result + findings       │
│    POST /batches/{id}/cancel          — cancel remaining jobs              │
│                                                                             │
│  Findings:                                                                  │
│    POST /batches/{id}/runs/{run_id}/reclassify — reclassify a finding      │
│    GET  /batches/{id}/runs/{run_id}/tool-audit — tool call audit report    │
│                                                                             │
│  Datasets:                                                                  │
│    GET  /datasets                     — list available datasets            │
│    POST /datasets/import-cve          — import a CVE-patched repo          │
│    POST /datasets/{name}/inject       — inject a vuln from template        │
│    GET  /datasets/{name}/labels       — view ground truth labels           │
│                                                                             │
│  Feedback:                                                                  │
│    POST /feedback/compare             — compare two batches                │
│    GET  /feedback/patterns/{batch_id} — FP patterns from a batch           │
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
│  │    - /data/outputs/{batch_id}/{experiment_id} (ReadWriteOnce PV)     │  │
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
│  ├── config/                    (ReadOnlyMany — experiments, models, etc.)  │
│  └── outputs/                   (ReadWriteMany — workers write, coord reads)│
│      └── {batch_id}/                                                        │
│          ├── {experiment_id}/   (one dir per job)                           │
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

**Dimensions are opt-in.** Not every batch needs to vary every dimension. The matrix expands only the dimensions listed in the config. A batch testing review profiles might fix the model and strategy and only vary profiles. This keeps run counts manageable.

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

class ModelProvider(ABC):
    """Unified interface over any LLM API."""

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system_prompt: str | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.2,
    ) -> ModelResponse:
        ...

    @abstractmethod
    def model_id(self) -> str:
        """Canonical identifier used in experiment records."""
        ...

    def supports_tools(self) -> bool:
        """Override to False for providers that don't support function calling."""
        return True
```

The five concrete providers (`OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`, `MistralProvider`, `CohereProvider`) each implement this interface. The `complete()` method handles provider-specific message formatting internally. No strategy code touches provider-specific APIs.

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
        """Write the comparative matrix across all runs in a batch."""
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
├── Dockerfile.coordinator        # coordinator service image
│
├── k8s/                          # Kubernetes manifests
│   ├── namespace.yaml
│   ├── coordinator-deployment.yaml
│   ├── coordinator-service.yaml
│   ├── shared-pvc.yaml           # PersistentVolumeClaim for shared storage
│   ├── network-policy.yaml       # egress rules for worker pods
│   ├── secrets.yaml              # placeholder — actual keys via sealed-secrets or external
│   ├── servicemonitor.yaml       # Prometheus ServiceMonitor for coordinator
│   └── grafana-dashboard.json    # pre-built dashboard
│
├── config/
│   ├── experiments.yaml          # primary experiment definition
│   ├── models.yaml               # model provider credentials/settings
│   ├── concurrency.yaml          # per-model concurrency caps
│   ├── pricing.yaml              # per-model token pricing for cost estimates
│   ├── review_profiles.yaml      # pre-canned review profile definitions
│   └── vuln_classes.yaml         # canonical vulnerability class definitions
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
│   └── {batch_id}/
│       ├── matrix_report.md
│       ├── matrix_report.json
│       ├── feedback/             # cross-batch feedback analysis
│       │   └── batch_comparison.json
│       └── {experiment_id}/
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
│       ├── coordinator.py        # BatchCoordinator + FastAPI app (HTTP API)
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
│       │   └── vuln_injector.py  # VulnInjector, InjectionTemplate
│       │
│       ├── evaluation/
│       │   ├── __init__.py
│       │   ├── evaluator.py      # Evaluator ABC, FileLevelEvaluator
│       │   ├── metrics.py        # precision/recall/FP computation
│       │   └── evidence.py       # EvidenceQualityAssessor
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
│       │   └── tracker.py        # FeedbackTracker, cross-batch analysis
│       │
│       ├── profiles/
│       │   ├── __init__.py
│       │   └── review_profiles.py  # ReviewProfile, BUILTIN_PROFILES
│       │
│       └── data/                 # Shared Pydantic/dataclass models
│           ├── __init__.py
│           ├── findings.py       # Finding, Severity, VulnClass
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

### 4.1 Finding

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

### 4.2 Ground Truth Label

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

### 4.3 Strategy Output

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

### 4.4 Verification Result

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

### 4.5 Experiment Configuration and Run

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
    batch_id: str
    model_id: str
    strategy: StrategyName
    tool_variant: ToolVariant
    review_profile: "ReviewProfileName"
    verification_variant: VerificationVariant
    dataset_name: str
    dataset_version: str
    strategy_config: dict          # strategy-specific params
    model_config: dict             # temperature, max_tokens, etc.
    parallel: bool = False         # whether subagents run in parallel
    created_at: datetime | None = None

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

### 4.6 Evaluation Result

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
    batch_id: str
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

    def expand(self) -> list[ExperimentRun]:
        """
        Cartesian product of all active dimensions.

        Default: 5 models × 5 strategies × 2 tool variants × 2 verification = 100 runs
        With all dimensions active:
          5 models × 5 strategies × 2 tool × 5 profiles × 2 verification = 500 runs
        Use single-element lists to fix dimensions you don't want to vary.
        """
        runs = []
        for model_id in self.model_ids:
            for strategy in self.strategies:
                for tool_variant in self.tool_variants:
                    for profile in self.review_profiles:
                        for verif in self.verification_variants:
                            for parallel in self.parallel_modes:
                                run_id = (
                                    f"{self.batch_id}_{model_id}_{strategy}"
                                    f"_{tool_variant}_{profile}_{verif}"
                                )
                                runs.append(ExperimentRun(
                                    id=run_id,
                                    batch_id=self.batch_id,
                                    model_id=model_id,
                                    strategy=strategy,
                                    tool_variant=tool_variant,
                                    review_profile=profile,
                                    verification_variant=verif,
                                    parallel=parallel,
                                    dataset_name=self.dataset_name,
                                    dataset_version=self.dataset_version,
                                    strategy_config=self.strategy_configs.get(strategy, {}),
                                    model_config=self.model_configs.get(model_id, {}),
                                ))
        return runs
```

### 5.2 Coordinator Service (BatchCoordinator)

The coordinator is a FastAPI service running as a single-replica K8s Deployment. It is the sole management interface for the framework — all operations (submitting batches, managing datasets, viewing results, reclassifying findings) happen through its HTTP API. There is no CLI; users interact via `curl`, a browser, or any HTTP client.

```python
class BatchCoordinator:
    """
    Manages the lifecycle of an experiment batch:
    1. Receives a batch submission (ExperimentMatrix + config)
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

    def submit_batch(self, matrix: ExperimentMatrix) -> str:
        """
        Expand matrix, persist batch metadata, start scheduling jobs.
        Returns batch_id for tracking.
        """
        runs = matrix.expand()
        batch_id = matrix.batch_id
        self._persist_batch_metadata(batch_id, runs)

        # Start the scheduling loop (runs in background)
        self._schedule_jobs(batch_id, runs)
        return batch_id

    def _schedule_jobs(self, batch_id: str, runs: list[ExperimentRun]) -> None:
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
            running_by_model = self._count_running_by_model(batch_id)

            scheduled_this_round = []
            for run in pending:
                cap = self.concurrency_caps.get(run.model_id, self.default_cap)
                current = running_by_model.get(run.model_id, 0)
                if current < cap:
                    self._create_k8s_job(batch_id, run)
                    running_by_model[run.model_id] = current + 1
                    scheduled_this_round.append(run)

            for run in scheduled_this_round:
                pending.remove(run)

            if pending:
                time.sleep(10)  # poll interval

    def _create_k8s_job(self, batch_id: str, run: ExperimentRun) -> None:
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
                    "batch": batch_id,
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
                                "--output-dir", f"/data/outputs/{batch_id}/{run.id}",
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

    def get_batch_status(self, batch_id: str) -> "BatchStatus":
        """
        Polls K8s for job statuses and reads completed RunResults from storage.
        """
        jobs = self.k8s_client.list_namespaced_job(
            self.namespace, label_selector=f"batch={batch_id}"
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
        return BatchStatus(
            batch_id=batch_id,
            total=total,
            completed=completed,
            failed=failed,
            running=running,
            pending=pending,
        )

    def collect_results(self, batch_id: str) -> list[RunResult]:
        """
        Reads all run_result.json files from shared storage for this batch.
        Called when all jobs are complete.
        """
        results = []
        batch_dir = self.storage_root / "outputs" / batch_id
        for run_dir in batch_dir.iterdir():
            result_file = run_dir / "run_result.json"
            if result_file.exists():
                results.append(RunResult.model_validate_json(result_file.read_text()))
        return results

    def finalize_batch(self, batch_id: str) -> None:
        """
        Called when all jobs complete. Collects results and generates matrix report.
        """
        results = self.collect_results(batch_id)
        output_dir = self.storage_root / "outputs" / batch_id
        self.reporter.render_matrix(results, output_dir)
```

#### FastAPI Application

```python
# src/sec_review_framework/coordinator.py

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Security Review Framework", version="1.0.0")
coordinator: BatchCoordinator = None  # initialized at startup

# --- Batches ---

@app.post("/batches", status_code=201)
def submit_batch(matrix: ExperimentMatrix) -> dict:
    """Submit a new experiment batch. Returns batch_id."""
    batch_id = coordinator.submit_batch(matrix)
    return {"batch_id": batch_id, "total_runs": len(matrix.expand())}

@app.get("/batches")
def list_batches() -> list[dict]:
    """List all batches with summary status."""
    return coordinator.list_batches()

@app.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict:
    """Batch status: total/completed/failed/running/pending counts, cost so far."""
    return coordinator.get_batch_status(batch_id).model_dump()

@app.get("/batches/{batch_id}/results")
def get_results_json(batch_id: str) -> dict:
    """Matrix report as JSON. Available after batch completes."""
    return coordinator.get_matrix_report_json(batch_id)

@app.get("/batches/{batch_id}/results/markdown")
def get_results_markdown(batch_id: str) -> str:
    """Matrix report as Markdown. Available after batch completes."""
    return coordinator.get_matrix_report_markdown(batch_id)

@app.get("/batches/{batch_id}/runs")
def list_runs(batch_id: str) -> list[dict]:
    """List all runs in a batch with status and summary metrics."""
    return coordinator.list_runs(batch_id)

@app.get("/batches/{batch_id}/runs/{run_id}")
def get_run(batch_id: str, run_id: str) -> dict:
    """Full RunResult including findings, evaluation, and verification details."""
    return coordinator.get_run_result(batch_id, run_id)

@app.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str) -> dict:
    """Cancel pending jobs. Running jobs complete but no new ones are scheduled."""
    cancelled = coordinator.cancel_batch(batch_id)
    return {"cancelled_jobs": cancelled}

# --- Findings ---

class ReclassifyRequest(BaseModel):
    finding_id: str
    status: str  # "unlabeled_real"
    note: str | None = None

@app.post("/batches/{batch_id}/runs/{run_id}/reclassify")
def reclassify_finding(batch_id: str, run_id: str, req: ReclassifyRequest) -> dict:
    """Reclassify a false positive as unlabeled_real. Recomputes metrics."""
    return coordinator.reclassify_finding(batch_id, run_id, req)

@app.get("/batches/{batch_id}/runs/{run_id}/tool-audit")
def tool_audit(batch_id: str, run_id: str) -> dict:
    """Tool call audit: counts by tool, any calls with URL-like strings in inputs."""
    return coordinator.audit_tool_calls(batch_id, run_id)

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

@app.post("/datasets/{dataset_name}/inject", status_code=201)
def inject_vuln(dataset_name: str, req: "InjectionRequest") -> dict:
    """Inject a vulnerability from a template into a dataset."""
    label = coordinator.inject_vuln(dataset_name, req)
    return {"label_id": label.id, "file_path": label.file_path}

@app.get("/datasets/{dataset_name}/labels")
def get_labels(dataset_name: str, version: str | None = None) -> list[dict]:
    """View ground truth labels for a dataset."""
    return coordinator.get_labels(dataset_name, version)

# --- Feedback ---

class CompareRequest(BaseModel):
    batch_a_id: str
    batch_b_id: str

@app.post("/feedback/compare")
def compare_batches(req: CompareRequest) -> dict:
    """Compare two batches: metric deltas, persistent FP patterns, regressions."""
    return coordinator.compare_batches(req.batch_a_id, req.batch_b_id)

@app.get("/feedback/patterns/{batch_id}")
def get_fp_patterns(batch_id: str) -> list[dict]:
    """Extract recurring false positive patterns from a batch."""
    return coordinator.get_fp_patterns(batch_id)

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
/data/outputs/{batch_id}/{experiment_id}/
    findings.jsonl                    — final findings (post-verification)
    findings_pre_verification.jsonl   — candidates before verification (if verification enabled)
    verification_log.jsonl            — verify/reject decisions with evidence
    tool_calls.jsonl                  — one ToolCallRecord per line from audit log
    run_result.json                   — full RunResult including EvaluationResult
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

### 6.2 CVE Importer

The CVE importer handles two tasks: fetching the before-fix state of a repository, and translating patch metadata into ground truth labels.

```python
@dataclass
class CVEImportSpec:
    cve_id: str                      # e.g. "CVE-2021-44228"
    repo_url: str                    # GitHub or similar
    fix_commit_sha: str              # the commit that patched the vuln
    dataset_name: str                # name for the local dataset directory
    cwe_id: str                      # manually provided
    vuln_class: VulnClass
    severity: Severity
    description: str
    affected_files: list[str] | None = None

class CVEImporter:
    def __init__(self, datasets_root: Path):
        self.datasets_root = datasets_root

    def import_cve(self, spec: CVEImportSpec) -> list[GroundTruthLabel]:
        """
        1. Clone repo at fix_commit_sha~1 (the vulnerable state).
        2. Run `git diff fix_commit_sha~1 fix_commit_sha` to get changed files.
        3. Parse diff to extract file paths and line ranges of removed lines
           (removed lines in the fix = the vulnerable code locations).
        4. Create GroundTruthLabel records.
        5. Copy repo into datasets/targets/{dataset_name}/repo/.
        6. Append labels to datasets/targets/{dataset_name}/labels.jsonl.
        """
        repo_path = self._clone_at_parent(spec.repo_url, spec.fix_commit_sha)
        affected = spec.affected_files or self._parse_diff(repo_path, spec.fix_commit_sha)
        labels = self._build_labels(spec, affected)
        self._install_repo(repo_path, spec.dataset_name)
        return labels

    def import_cve_with_diff(self, spec: CVEImportSpec) -> list[GroundTruthLabel]:
        """
        Same as import_cve, but also generates a diff_spec.yaml for diff-mode testing.
        Clones repo at fix_commit_sha (the fixed state) and records the diff
        between fix_commit_sha~1 and fix_commit_sha.

        Labels are tagged with introduced_in_diff=True since the CVE was present
        in the before-fix state (the base of the diff).
        """
        ...
```

**Operational note**: `fix_commit_sha~1` gives the last vulnerable state. Diff parsing extracts the removed lines (the vulnerable code) not the added lines (the fix).

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

    def inject(
        self,
        repo_path: Path,
        template_id: str,
        target_file: str,
        substitutions: dict[str, str] | None = None,
    ) -> InjectionResult:
        """Applies a template to a specific file in the repo."""
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

---

## 9. Evaluation Pipeline

### 9.1 Matching Algorithm

```python
class FileLevelEvaluator(Evaluator):
    """
    Primary matching: exact file path match between finding and label.
    Line overlap is computed and stored but does not affect TP/FP classification.
    """

    def evaluate(self, findings: list[Finding], labels: list[GroundTruthLabel]) -> EvaluationResult:
        matched_findings: list[MatchedFinding] = []
        consumed_label_ids: set[str] = set()

        for finding in findings:
            matching_labels = [
                l for l in labels
                if l.file_path == finding.file_path
                and l.id not in consumed_label_ids
            ]

            if matching_labels:
                best = self._select_best_label(finding, matching_labels)
                consumed_label_ids.add(best.id)
                line_overlap = self._check_line_overlap(finding, best)
                matched_findings.append(MatchedFinding(
                    finding=finding,
                    matched_label=best,
                    match_status=MatchStatus.TRUE_POSITIVE,
                    file_match=True,
                    line_overlap=line_overlap,
                ))
            else:
                matched_findings.append(MatchedFinding(
                    finding=finding,
                    matched_label=None,
                    match_status=MatchStatus.FALSE_POSITIVE,
                    file_match=False,
                    line_overlap=False,
                ))

        unmatched_labels = [l for l in labels if l.id not in consumed_label_ids]

        # Assess evidence quality for true positives
        evidence_assessor = EvidenceQualityAssessor()
        for mf in matched_findings:
            if mf.match_status == MatchStatus.TRUE_POSITIVE:
                mf.evidence_quality = evidence_assessor.assess(mf.finding, mf.matched_label)

        return self._compute_metrics(matched_findings, unmatched_labels, labels)

    def _select_best_label(self, finding, candidates):
        """Prefer matching vuln_class, then any."""
        class_matches = [l for l in candidates if l.vuln_class == finding.vuln_class]
        return class_matches[0] if class_matches else candidates[0]

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

### 9.4 Reclassification Workflow

After an experiment run, team members can reclassify false positives to `UNLABELED_REAL` via the coordinator API:

```
POST /batches/{batch_id}/runs/{run_id}/reclassify
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
# Security Review Framework — Batch {batch_id} Results

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

| Model | Avg Cost/Run | Total Batch Cost | Cost per True Positive |
|-------|-------------|------------------|----------------------|
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
  "batch_id": "...",
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
    "total_batch_cost": 0.0,
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

The feedback loop enables learning across experiment batches. It does not automatically retrain or modify prompts — it surfaces patterns that the team uses to manually refine strategies and profiles.

### 12.1 FeedbackTracker

```python
class FeedbackTracker:
    """
    Compares results across batches to surface patterns in model behavior.
    Produces a structured comparison that the team reviews to refine
    prompts, profiles, and strategies.
    """

    def compare_batches(
        self,
        batch_a_results: list[RunResult],
        batch_b_results: list[RunResult],
    ) -> "BatchComparison":
        """
        For runs that share (model, strategy, tool_variant):
        - Did metrics improve or regress?
        - Which false positive patterns are persistent across batches?
        - Which vulnerabilities are consistently missed by a given model?
        """
        ...

    def extract_fp_patterns(
        self,
        results: list[RunResult],
    ) -> list["FalsePositivePattern"]:
        """
        Across all runs in a batch, find recurring false positive patterns:
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
class BatchComparison:
    batch_a_id: str
    batch_b_id: str
    metric_deltas: dict[str, dict]    # experiment_id → {precision: +0.05, recall: -0.02, ...}
    persistent_false_positives: list[FalsePositivePattern]
    persistent_misses: list[dict]     # labels missed in both batches
    improvements: list[dict]          # findings that went from FP→TP or FN→TP
    regressions: list[dict]           # findings that went from TP→FP or TP→FN
```

### 12.2 Workflow

```
# Compare two batches:
POST /feedback/compare
{
  "batch_a_id": "batch_2026_04_16_v1",
  "batch_b_id": "batch_2026_04_20_v2"
}
# Returns: metric deltas, persistent FP patterns, regressions

# Extract FP patterns from a single batch:
GET /feedback/patterns/batch_2026_04_16_v1
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

Deploy within a VPC with no outbound internet except explicitly allowlisted endpoints:
- LLM provider API endpoints (one per provider in use)
- Internal artifact storage
- Semgrep update endpoint (or use offline rules only)

All other outbound traffic blocked at the network layer. This is the highest-value control because it is independent of application code correctness.

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
GET /batches/{batch_id}/runs/{run_id}/tool-audit
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

batch_id: "batch_2026_04_16_v1"
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

# Global cap on concurrent experiment runs across all models.
# Per-model caps in concurrency.yaml further constrain this.
max_parallel_runs: 10

output_root: "outputs/"
```

### 14.2 Model Provider Configuration

```yaml
# config/models.yaml

providers:
  gpt-4o:
    provider_class: "OpenAIProvider"
    model_name: "gpt-4o"
    temperature: 0.2
    max_tokens: 8192
    api_key_env: "OPENAI_API_KEY"

  claude-opus-4:
    provider_class: "AnthropicProvider"
    model_name: "claude-opus-4"
    temperature: 0.2
    max_tokens: 8192
    api_key_env: "ANTHROPIC_API_KEY"

  gemini-2.0-pro:
    provider_class: "GeminiProvider"
    model_name: "gemini-2.0-pro"
    temperature: 0.2
    max_tokens: 8192
    api_key_env: "GEMINI_API_KEY"

  mistral-large:
    provider_class: "MistralProvider"
    model_name: "mistral-large-latest"
    temperature: 0.2
    max_tokens: 8192
    api_key_env: "MISTRAL_API_KEY"

  command-r-plus:
    provider_class: "CohereProvider"
    model_name: "command-r-plus"
    temperature: 0.2
    max_tokens: 8192
    api_key_env: "COHERE_API_KEY"
```

### 14.3 Vulnerability Class Definitions

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

### 14.4 Review Profiles

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

1. **Coordinator Service** — single-replica Deployment (FastAPI). Manages batch lifecycle, schedules K8s Jobs, enforces concurrency caps, aggregates results, exposes Prometheus metrics.
2. **Worker Pods** — one K8s Job per experiment run. Each runs a standalone container that executes one experiment and writes results to shared storage.
3. **Shared Storage** — NFS or EFS PersistentVolume. Holds datasets (read-only to workers), config, and outputs (read-write by workers, read by coordinator).

All interaction happens through the coordinator's HTTP API — there is no CLI. Users submit batches, manage datasets, view results, and reclassify findings via HTTP requests.

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

# Batch metrics
batches_submitted = Counter("sec_review_batches_submitted_total", "Total batches submitted")
batches_completed = Counter("sec_review_batches_completed_total", "Total batches completed")
batch_runs_total = Gauge("sec_review_batch_runs_total", "Total runs in current batch",
                         ["batch_id"])

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
                      ["batch_id", "experiment_id"])
run_recall = Gauge("sec_review_recall", "Recall per run",
                   ["batch_id", "experiment_id"])
run_f1 = Gauge("sec_review_f1", "F1 per run",
               ["batch_id", "experiment_id"])

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

- Batch progress (runs completed / total over time)
- Active jobs by model (stacked area)
- Run duration distribution (heatmap by model × strategy)
- Cost accumulation (running total per batch)
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

**Submit a batch:**

```bash
curl -X POST $COORD/batches \
  -H "Content-Type: application/json" \
  -d @config/experiments.yaml.json

# Response:
# {"batch_id": "batch_2026_04_16_v1", "total_runs": 100}
```

**Check progress:**

```bash
curl $COORD/batches/batch_2026_04_16_v1

# Response:
# {
#   "batch_id": "batch_2026_04_16_v1",
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
curl $COORD/batches/batch_2026_04_16_v1/results

# Markdown matrix report
curl $COORD/batches/batch_2026_04_16_v1/results/markdown

# Single run detail
curl $COORD/batches/batch_2026_04_16_v1/runs/gpt-4o_single_agent_with_tools_default_none
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
curl -X POST $COORD/batches/batch_2026_04_16_v1/runs/gpt-4o_single_agent_with_tools_default_none/reclassify \
  -H "Content-Type: application/json" \
  -d '{"finding_id": "abc123", "status": "unlabeled_real", "note": "Real path traversal"}'
```

**Compare batches:**

```bash
curl -X POST $COORD/feedback/compare \
  -H "Content-Type: application/json" \
  -d '{"batch_a_id": "batch_2026_04_16_v1", "batch_b_id": "batch_2026_04_20_v2"}'
```

**Browse reference data:**

```bash
curl $COORD/models      # available model providers
curl $COORD/strategies  # available scan strategies
curl $COORD/profiles    # review profiles with descriptions
curl $COORD/templates   # vuln injection templates
```

All state lives in K8s (job status) and shared storage (results). The coordinator is stateless — it can be restarted without losing batch progress.

### 15.6 Scaling Considerations

| Factor | Constraint | Mitigation |
|--------|-----------|------------|
| LLM rate limits | Provider-specific RPM/TPM limits | Per-model concurrency caps in `concurrency.yaml` |
| Node resources | Each worker needs ~1-2GB RAM for conversation history | Set pod resource limits, use cluster autoscaler |
| Shared storage IOPS | Many workers writing simultaneously | Use EFS with bursting or provisioned throughput |
| Job scheduling | K8s Job creation overhead at scale | Coordinator batches Job creation, 10s poll interval |
| Provider outages | One provider down shouldn't block the whole batch | Coordinator continues scheduling other models; failed jobs recorded, retryable |

For a 100-run batch with `max_parallel_runs: 10` and average run duration of 10 minutes, expected wall-clock time is ~100 minutes (10 waves of 10). With concurrency caps, some models may take longer if their cap is lower than the global cap.

---

## 16. Extension Points

### Adding a New Model Provider

1. Create `src/sec_review_framework/models/{provider_name}.py`
2. Implement `ModelProvider` ABC
3. Register in `ModelProviderFactory`
4. Add entry to `config/models.yaml`
5. Add pricing entry to `config/pricing.yaml`

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

## 17. Future Work: Iterative Review

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
HTTP POST /batches → BatchCoordinator
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
    → BatchCoordinator detects all jobs complete
        → collect_results() from shared storage
        → ReportGenerator.render_matrix(results, output_root)
        → Update Prometheus metrics
    → [optional] FeedbackTracker.compare_batches(batch_a, batch_b)
```
