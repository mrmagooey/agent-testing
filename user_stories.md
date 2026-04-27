# User Stories — Frontend E2E Coverage

User stories driving the iterative `/loop` e2e build-out for the agent-testing
framework's React frontend. Each story is from the perspective of a security
researcher (the framework's primary user) and is exercised by Playwright
tests in `frontend/e2e/`.

---

## Completed iterations

### 1. Browse and inspect the strategy catalog

**Spec:** `frontend/e2e/strategies-browse.spec.ts` (commit `83c4365`)

> As a security researcher, I want to browse the catalog of review strategies,
> filter to user-authored vs builtin (and by orchestration shape), and drill
> into a specific strategy to inspect its default bundle and per-key overrides
> — so I can decide which strategy to base my next experiment on.

Covers `StrategiesList` filter chips and shape filter, row-to-viewer
navigation, `StrategyViewer` header + default bundle + per-vuln-class
override tabs, and `is_builtin`-gated Delete affordance.

### 2. Fork and customise a builtin strategy

**Spec:** `frontend/e2e/strategy-fork-edit.spec.ts` (commit `4853bc4`)

> As a security researcher, I want to fork a builtin strategy from its viewer
> page, change the strategy name and a key default field, then save — so I
> land on the new strategy's viewer with the updated values persisted.

Covers fork-from-viewer button, editor pre-seeding, name-required save
gating, happy-path POST with body shape assertion, 400 error surfacing,
shape-driven Overrides section reveal, breadcrumb back-nav.

### 3. Import an experiment bundle

**Spec:** `frontend/e2e/experiment-import.spec.ts` (commit `c4879ba`)

> As a security researcher, I want to import a previously exported
> `.secrev.zip` experiment bundle by selecting a file, choosing a conflict
> policy (reject / rename / merge), and clicking Upload — so on success I see
> a summary with the experiment ID linked to the detail page, runs/findings
> counts, dataset rehydration/missing chips, and any warnings; on failure I
> see the API's error message.

Covers the XHR-backed multipart upload, full `ImportSummary` rendering,
`renamed_from` line, error path, and in-progress button gating.

### 4. Author a per_file strategy with override rules

**Spec:** `frontend/e2e/strategy-rule-list.spec.ts` (commit `d8cd5e9`)

> As a security researcher authoring a `per_file` strategy, I want to add
> multiple glob-pattern override rules, edit each rule's key, reorder rules
> with up/down buttons, and remove rules — so I can express first-match-wins
> routing of files to different bundle overrides before saving.

Covers shape switch revealing Overrides, add/remove/move semantics with
boundary-disabled buttons, GlobPreview match counts, and POST body
verification of `overrides[]` order and keys.

### 5. Export an experiment bundle

**Spec:** `frontend/e2e/export-menu.spec.ts` (commit `04c6b14`)

> As a security researcher with a completed experiment, I want to open the
> Download dropdown, choose "Export full bundle (.secrev.zip)", pick between
> `descriptor` (recommended) and `reference` dataset modes, and click Export
> — so the browser downloads a `.secrev.zip` from
> `/api/experiments/<id>/export?dataset_mode=<mode>`.

Covers dropdown disclosure, dialog state machine, dataset-mode reset on
reopen, URL capture via `waitForEvent('download')` (Chromium) and
`context.on('request')` (Firefox), and absence of the Download dropdown
on non-terminal experiments.

### 6. Configure subagents and dispatch caps

**Spec:** `frontend/e2e/subagents-picker.spec.ts` (commit `43bcf8b`)

> As a security researcher authoring a hierarchical strategy, I want to
> select another strategy from the registry as a subagent, set the dispatch
> caps (max depth / invocations / batch size), pick a dispatch-fallback
> policy, and save — so the POST body carries the chosen subagents and caps
> for the supervisor agent to dispatch to.

Covers registry checkbox toggling, "N subagents selected" notice, cap input
defaults, dispatch-fallback options, output-type selection, and full POST
body shape verification.

### 7. Drill from a global finding into its run or source

**Spec:** `frontend/e2e/findings-drilldown.spec.ts` (commit `2daa3f8`)

> As a security researcher browsing the global Findings page, I want to
> click a finding row to expand its description and CWE list, then click
> "Open run" to jump to the originating run (with my finding pre-anchored)
> or "View source" to jump to the dataset source view at the right file and
> line — so I can drill from a list-level finding into context in two
> clicks.

Covers row toggle (single-row expansion), CWE chip rendering, Open run +
View source link href shapes, and verification that the in-row
experiment-name link's `stopPropagation` prevents row expansion.

### 8. Reclassify a false-positive with a note

**Spec:** `frontend/e2e/findings-reclassify-modal.spec.ts` (commit `8ec20e0`)

> As a security researcher reviewing a completed experiment's findings, I
> want to drill into a false-positive finding, click "Reclassify as
> Unlabeled Real" to open the reclassification modal, type a note, and
> click Confirm — so the API receives the new label with my note and the
> modal closes.

Covers the FP-only Reclassify button, modal state, note typing, in-flight
"Saving…" disabled state, POST body shape (`finding_id`, `new_status`,
`note`), Cancel-does-not-fire-POST, and TP-row absence of the button.
Distinct from the simpler inline empty-note path on RunDetail.

### 9. Materialize an unmaterialized dataset

**Spec:** `frontend/e2e/dataset-materialize.spec.ts` (commit `f723681`)

> As a security researcher opening a dataset that hasn't been materialized
> on this deployment, I want to see a yellow banner explaining the dataset
> isn't materialized and click "Materialize now" — so a `POST
> /api/datasets/<name>/rematerialize` fires; on success the banner
> disappears and the page shows a "Materialized" timestamp; on failure the
> error message surfaces with `role="alert"`.

Covers the conditional banner, em-dash timestamp, happy-path POST + banner
detach + date update, in-flight "Materializing…" state, 500 error detail
surfacing, generic abort fallback message, and banner absence when the
dataset is already materialized.

### 10. Audit a run's prompt-injection provenance

**Spec:** `frontend/e2e/prompt-injection-viewer.spec.ts` (commit `97d4b96`)

> As a security researcher reading a run's prompt snapshot, I want to see
> whether vulnerability injection or a profile modifier was applied (with
> the template_id when injection is active), see exactly which lines differ
> between clean and injected prompts, and see a clear "No injection
> applied" note for plain runs — so I can audit experiment provenance.

Covers the three mutually-exclusive `PromptInjectionViewer` paths:
injection-applied (banner with template_id, side-by-side diff with
per-side line filtering), profile-modifier (augmented system_prompt with
`[Profile modifier injected]` separator), and plain italic note. Per-test
route overrides clone the run-full fixture and mutate
`prompt_snapshot`. Also verifies the injection-without-template_id edge
case (DiffPanes render but the amber banner is suppressed).

### 11. Filter dataset labels by CWE, severity, and source

**Spec:** `frontend/e2e/labels-filter.spec.ts` (commit `77ab79f`)

> As a security researcher browsing a dataset's labels, I want to filter
> the labels table by CWE, severity, and source (and combine filters) — so
> a refetch hits `GET /api/datasets/<name>/labels?cwe=…&severity=…&source=…`
> with my chosen values, and a "Clear filters" link appears once any
> filter is active and resets all three when clicked.

Covers all four `LabelsFilterBar` testids (`filter-cwe`,
`filter-severity`, `filter-source`, `filter-clear`): initial no-query
fetch, per-control refetch with the right query param, combined-filter
single request, gated Clear button visibility, and Clear resetting
inputs + firing a clean refetch. The shared `mockApi.ts` labels regex
has a `$` end-anchor so it can't match query'd URLs — the per-test
handler uses a `**/labels*` glob (with trailing `*`) registered after
mockApi for LIFO priority.

### 12. Distinguish message roles in the conversation transcript

**Spec:** `frontend/e2e/conversation-viewer.spec.ts` (commit `7c8c7d2`)

> As a security researcher reading a run's conversation transcript, I want
> each message to be visually distinguished by role — `user` (blue),
> `assistant` (green), `tool` (gray) — with a role pill, role-specific
> border + background colors, and a per-message timestamp when available
> — so I can quickly distinguish tool calls from agent reasoning while
> skimming the transcript.

Covers all three role-style branches in `ConversationViewer.tsx`
(`ROLE_STYLES` + `ROLE_BADGE`): empty-state "No messages recorded.",
all-three-roles render check (count + ordered badge text scoped to
`div.rounded-r-lg`), per-role border/bg class assertion, message
ordering preserved across a non-alphabetical 5-message fixture,
timestamp rendered when present (locale-agnostic `\d{1,2}:\d{2}`
regex), no time text when `timestamp` is absent, unknown-role
fallthrough to `tool` styling, and dynamic `(N messages)` header
count. Each test that mutates `messages` registers a per-test runFull
override BEFORE a fresh `page.goto(BASE_URL)` so the override wins
the initial useEffect fetch.

### 13. Smoke-test failure path renders an error banner and re-enables retry

**Spec:** `frontend/e2e/dashboard-smoke-test-failure.spec.ts` (commit `0978f5e`)

> As a security researcher who has just installed the framework, when I
> click "Run Smoke Test" and the backend returns an error, I want to see
> a clear failure banner with the error reason and have the button
> re-enabled so I can retry — so I know my deployment isn't healthy and
> can attempt a fresh run.

Covers the `smokeTest.status === 'error'` render branch on the
Dashboard: 422 `detail`, 500 `detail`, 400 `message` (apiFetch's
secondary message field), 503 with non-JSON body (the `API error <N>`
fallback), `route.abort('failed')` for connection-failure messages,
failure-then-retry path (error banner replaced by success banner +
"View experiment →" link via `page.unroute(...)` letting the global
mockApi success handler take over), and a fresh-navigation regression
guard (component-local state resets, no stale error banner). The
abort-banner locator is scoped to `section`-filtered-by-button to
avoid colliding with the top-level `error` block at Dashboard.tsx:251
which shares the `text-signal-danger font-mono` classes.

### 14. Surface dataset-injection failures so a broken inject is never committed

**Spec:** `frontend/e2e/dataset-inject-errors.spec.ts` (commit `ce20b13`)

> As a security researcher injecting a vulnerability template into a
> clean dataset, when the preview API or the inject API returns an
> error, I want the failure to surface visibly so I don't accidentally
> commit a broken injection — and I should be able to recover the
> dataset detail by reloading the page.

Covers both failure surfaces (`previewInjection` and `injectVuln`):
422 with `detail`, 500 with `detail`, 400 with `message` (apiFetch's
secondary lookup), 503 with non-JSON body (`API error <N>` fallback),
network abort, and recovery via `page.reload()` (after `page.unroute`
removes the per-test override). The test must capture the production
contract that `setError(...)` short-circuits the entire DatasetDetail
page (lines 484-489) — the wizard modal disappears and the "Inject
Vulnerability" button is gone, leaving only a red page-level error
block. URL discrimination between `/inject/preview` and `/inject` is
handled by Playwright's `**`-prefix-only glob semantics; the existing
happy-path spec's `!url.includes('preview')` guard is not strictly
needed but mirrored where added cost is zero.

### 15. Edit per-vuln-class overrides in the strategy editor

**Spec:** `frontend/e2e/strategy-vulnclass-overrides.spec.ts` (commit `9c66e9b`)

> As a security researcher authoring a `per_vuln_class` strategy, I
> want to switch to the SQLi tab, uncheck "Inherit from default" on
> the model_id field, set a different model, then switch to the XSS
> tab and override `max_turns` — so the resulting POST body carries
> overrides for both classes with only the changed fields, and the
> inactive vuln-class tabs reset to inheriting all fields.

Completes the strategy-editor trilogy alongside iter 1 (browse), iter 2
(fork), and iter 4 (per_file rules). Covers `VulnClassOverrides` Tabs
rendering with one tab per `VULN_CLASSES` entry, sqli-default selection,
amber-dot indicator (`rules.some(r => r.key === vc)`), per-tab Inherit
toggling on `OverrideFieldEditor`, cross-tab state preservation, and
POST body shape (only sqli + xss override entries with only changed
fields). **Production quirk captured**: re-checking Inherit on a field
calls `clear(field)` which sets the field to `null` but does NOT remove
the rule entry from the `rules` array — so the amber tab dot persists
even after all five fields are re-inherited (rule entry remains as
`{key: 'xss', override: {max_turns: null}}`). The test asserts the
actual production behavior, not the wishful "dot disappears". Locators
use position-based `nth(INHERIT_INDEX.field)` indices into the five
checkboxes per panel — order pinned in a frozen `INHERIT_INDEX` map at
the top of the spec.

### 16. Cross-experiment run comparison: tab switching, loading, error, and URL shape

**Spec:** `frontend/e2e/runcompare-render.spec.ts` (commit `c7ae3e0`)

> As a security researcher, I want to pick two runs from different
> experiments via the global `/compare` picker, see the side-by-side
> comparison split into "Found by Both" / "Only in A" / "Only in B"
> tabs, and see clear empty/error/loading states — so I can compare
> strategies across experiments without manually copying URLs.

Complements `global-compare.spec.ts` (which covered picker mechanics +
URL persistence + breadcrumb structure + the default Found-by-Both
render). Adds: no-selection prompt ("Select two runs above to compare
them."), tab switching to "Only in A" (path-traversal finding visible,
SQL-injection finding gone, active tab gets `border-amber-600`), "Only
in B" empty-state ("No findings in this category."), the actual
`/api/compare-runs` URL params captured via `page.waitForRequest`
(asserting `a_experiment != b_experiment`), the loading spinner during
a delayed fetch (uses `waitUntil: 'domcontentloaded'` on `goto` so the
assertion runs while the fetch is still in flight), the red error
block when `/compare-runs` returns 500 with detail, and dynamic tab
counts from a custom-shaped fixture.

**Glob convention reminder**: per-test routes use
`**/api/compare-runs*` (trailing-`*` glob). The bare
`**/api/compare-runs` glob compiles to a `$`-anchored regex that does
NOT match query-stringed URLs — a hazard already documented in
iter-11's labels-filter spec. The author originally used a URL
predicate function as a workaround; the trailing-`*` glob is the
correct sibling-spec convention and is what landed.

### 17. Dataset-mismatch warning + experiment back-link in run comparison

**Spec:** `frontend/e2e/runcompare-extras.spec.ts` (commit `ee54a96`)

> As a security researcher comparing runs from two experiments, I want
> a clear "Dataset mismatch" warning banner with per-run details when
> the runs were executed against different datasets, and inline links
> from each run's metadata to its parent experiment — so I know my
> conclusions don't generalize across the underlying ground truth, and
> I can drill back to either source experiment in one click.

Closes the iter-16 reviewer's noted gap. Two render paths exercised:

`DatasetMismatchBanner` (`role="alert"`,
`data-testid="dataset-mismatch-banner"`) — visible when
`comparison.dataset_mismatch` is truthy; absent on `false` and on
field omission (the banner mount uses `&&` truthy-check). Heading
"Dataset mismatch" is the FIRST `<p>` of the banner, so the child
count is `warnings.length + 1`. Empty `warnings: []` still renders the
banner with just the heading.

Per-card Experiment link — `Run A` card's `<dl>` has an `Experiment`
row with `<Link to=/experiments/<id>>` only when `run.experiment_name`
is truthy; `.filter(([, v]) => v !== null)` at RunCompare.tsx:251
drops the row entirely otherwise (no "—" placeholder). Test 7 scopes
the absence assertion to the Run A card via
`heading.locator('../..')` ancestor traversal so a future move of the
row to Run B wouldn't mask a Run A regression.

### 18. Scan model × strategy accuracy at a glance via the dashboard heatmap

**Spec:** `frontend/e2e/accuracy-heatmap.spec.ts` (commit `133bff7`)

> As a security researcher landing on the dashboard, I want to scan a
> model × strategy accuracy heatmap to identify high-performing
> combinations at a glance — with clear PASS / WARN / FAIL signal
> labels per cell, an em-dash placeholder for missing combinations,
> and meaningful empty / loading / error fallbacks when data is
> unavailable or stale.

Complements `heatmap-contrast.spec.ts` (which covers only color
contrast). Adds: populated table render with exact 4-cell count on
the default fixture, signal-threshold mapping (PASS ≥ 0.8, WARN ≥
0.6, FAIL < 0.6) verified via `data-signal` attribute and the
`heatmap-cell-signal` testid, EmptyCell `—` rendering for missing
model×strategy combinations (exact count = `models × strategies -
populated cells`), the empty-data fallback ("No completed runs with
evaluation data yet."), the error-state paragraph (apiFetch surfaces
the response `detail` as `error.message`, rendered in
`text-signal-danger`), the loading skeleton (scoped to
`.animate-pulse.h-24` to avoid collision with other Tailwind
`animate-pulse` users), and the footer explanatory text. The PASS/WARN
signal tests use `.filter({ hasText: '0.912' })` etc. to scope to
specific cells in the default fixture's distinct accuracy values.

### 19. Browse a dataset's source files via the side file tree

**Spec:** `frontend/e2e/dataset-detail-filetree.spec.ts` (commit `cae91fb`)

> As a security researcher exploring a dataset, I want to navigate the
> dataset's source files via the side file tree — directories expand
> and collapse with arrow indicators, files show ground-truth label
> counts as red badges, the selected file is highlighted, and clicking
> a file loads its content in the viewer panel — so I can read source
> code in context with the labels that mark known vulnerabilities.

Covers the standalone (non-modal) `FileTree` on `/datasets/<name>`,
distinct from the inject-modal scoping in
`dataset-detail-interactions.spec.ts`. Asserts: top-level dirs (`src`,
`tests`) start expanded with `▼` because `useState(depth === 0)` at
FileTree.tsx:22 returns true only for depth-0; nested dirs (`auth`,
`api`, `files`, `search`) at depth=1 start collapsed with `▶`;
click-to-expand reveals children + flips indicator; click-to-collapse
hides children; selecting a file flips its row to `bg-blue-100 |
bg-blue-900` and replaces the "Select a file to view" placeholder
with the path text inside `<p class="text-xs font-mono ...">` (scoped
this way to avoid colliding with the labels-table cell rendering the
same path); red label-count pill (`.bg-red-100 | .bg-red-900`) only
on files with labels (`login.py` has 1; `logout.py` has 0); and dirs
sort before files at every level. The pre-fix author had used
`getByText('src/auth/login.py').first()` for the viewer assertion;
the reviewer noted that could match the labels-table cell too — the
fix scopes via the `<p class="text-xs font-mono">` viewer header.

### 20. Watch experiment cost approach the configured spend cap

**Spec:** `frontend/e2e/experiment-detail-near-cap.spec.ts` (commit `fd492f3`)

> As a security researcher monitoring an experiment, I want to see
> the running cost on the experiment detail page, alongside any
> configured spend cap and a clear warning indicator when actual
> spend exceeds 80% of the cap — so I can decide whether to cancel
> before the experiment hits the cap.

Covers the three rendering branches of the Cost / Cap block in
`ExperimentDetail.tsx:223-238`:

- **Cost** — always shown with `toFixed(2)` format.
- **Cap** — shown only when `experiment.spend_cap_usd` is truthy.
  When `null`, the entire Cap `<span>` is omitted.
- **⚠ Near cap warning** — orange-classed inline span (`text-orange-600
  dark:text-orange-400`) shown only when `total_cost_usd /
  spend_cap_usd > 0.8` (strict inequality — exactly 0.8 does NOT
  trigger).

Per-test override pattern targets `**/api/experiments/<id>*` (trailing
`*` glob for query-string compatibility), with an
`url.pathname.endsWith(/<id>)` guard so the `/results` and other
sub-resource fetches fall through to the global `mockApi` handler.
Boundary tests cover ratios 0.0249, 0.8 exact (no warning), 0.9
(warning fires), and 0.98 (warning fires).

### 21. Track experiment progress at a glance via the colored progress bar

**Spec:** `frontend/e2e/progress-bar.spec.ts` (commit `ec586fd`)

> As a security researcher monitoring an experiment, I want a visual
> progress bar showing run-state counts (completed / running /
> pending / failed) with color-coded segments, an overall
> percentage, and a counts legend — so I can see at a glance how the
> experiment is progressing and whether anything has failed.

Covers the `ProgressBar` rendering paths on ExperimentDetail (only
mount point at `ExperimentDetail.tsx:215`): `total === 0` short-circuit
returns null (no bar, no legend, no percentage), conditional segment
rendering (`{N > 0 && (...)}` per-segment guard means only non-zero
segments mount as `<div style="width:...%">` inside the `.h-4.rounded-full`
track), proportional widths via the inline style, the running
segment's `.animate-pulse` class, the percentage `Math.round(completed /
total * 100)` rendered in `span.w-12.text-right`, the four-item
legend (completed/running/pending always; failed only when > 0), the
"{total} total" right-aligned span, and per-segment `title="N role"`
tooltips.

**Cross-browser style quirk captured**: the `style="width: 100.0%"`
attribute is preserved verbatim by Firefox but normalized to `width:
100%` by Chromium when read via `getAttribute('style')`. Width
assertion uses regex `/width:\s*100(\.0)?%/` to handle both. Other
percentage values like `12.5%` survive intact in both browsers.

### 22. Triage experiment findings via filters, row expand, and dataset back-link

**Spec:** `frontend/e2e/findings-explorer.spec.ts` (commit `744fe24`)

> As a security researcher reviewing an experiment's aggregated
> findings, I want to filter the table by match status, vuln class,
> and severity (with the live count updating), expand any row to read
> the full description and jump to the source dataset, and see a
> clear empty-state when filters return nothing — so I can triage the
> experiment's results without leaving the page.

Covers the `FindingsExplorer` interactive surface on ExperimentDetail
that wasn't already covered by iter-8's reclassify modal spec.
`FindingsExplorer` is mounted only on ExperimentDetail (not RunDetail
— RunDetail has its own inline filter UI). Three local-state filter
selects (match status / vuln class / severity), a live `"{N}
findings"` counter, vuln-class dropdown options derived from
`Array.from(new Set(source.map(f => f.vuln_class))).sort()`, single-row
expansion (state is `expandedId | null`), CodeMirror-backed
description rendering inside the expanded `<tr><td colspan=6>` row, the
"View in dataset" link href shape (`path` %-encoded slashes,
`line=line_start`, `end=line_end`, `from_experiment`, `from_run`), and
`stopPropagation` on the link click preserving row expansion (verified
via the iter-7 capturing-preventDefault listener pattern). Empty state
"No findings match the current filters." renders when `filtered.length
=== 0`.

### 23. Search experiment findings server-side with debounce, spinner, and clear

**Spec:** `frontend/e2e/findings-search.spec.ts` (commit `d5e43ae`)

> As a security researcher reviewing an experiment's findings, I want
> to search across all findings by title / description /
> recommendation with debounced server-side search — so the table
> updates as I type without sending a request for every keystroke,
> and I can clear the query with a single click to restore the full
> list.

Covers the `FindingsSearch` component (mounted inside
`FindingsExplorer` at `FindingsExplorer.tsx:58`, only on
ExperimentDetail). Closes the gap noted at
`run-detail-filters.spec.ts:161` where the existing test was misnamed
and actually exercised RunDetail's inline `<input type="search">`,
not this component.

Asserts: initial render with placeholder + search-icon SVG; typing
fires `GET /api/experiments/<id>/findings/search?q=...` after the
300ms debounce; results replace initial findings via
FindingsExplorer's `setSearchResults(r.length > 0 ? r : null)`; rapid
typing (6 chars at delay=0) coalesces to exactly one API request;
`.animate-spin` spinner during in-flight (delayed-fixture pattern);
mutually-exclusive icon-area states (search-icon ↔ spinner ↔ clear
button); clear button (×, `aria-label="Clear search"`) restores
initial findings and resets input value; empty input
(`!value.trim()`) NEVER fires an API request even after typing-and-
backspacing; API error path triggers `catch` → `onResults([])` →
source falls back to `initialFindings`.

`waitForTimeout(500)` used (vs. 300ms debounce window) on the
"empty input no-request" test for CI slack — `waitForResponse` would
hang forever since no request is expected.

### 24. Onboarding pipeline diagram on the Dashboard empty-state

**Spec:** `frontend/e2e/pipeline-diagram.spec.ts` (commit `86a7908`)

> As a security researcher landing on the dashboard for the first time
> (or whenever no experiments are running), I want to see a clear
> visual pipeline diagram explaining the five experiment stages —
> Configure, Expand Matrix, Schedule, Execute, Aggregate & Report —
> with the Configure stage acting as a quick-start link to
> `/experiments/new` — so I understand what's about to happen before
> I start my first experiment.

Covers the `PipelineDiagram` component (no prior e2e coverage). The
Dashboard mounts it ONLY when `active.length === 0` (active = `running
| pending` experiments at `Dashboard.tsx:347`). Default fixture has
experiment B running, so the diagram is hidden by default; an override
returning only completed experiments brings it back.

Asserts: hidden when running experiment exists (negative case), header
text ("Experiment pipeline" + "No experiments running…"), all 5 stage
labels in document order via the `data-stage-index` walk, each stage's
description text, the Configure stage as the only `<Link>` (href
ends with `/experiments/new`), the other 4 stages render as `<div>`
not `<a>` (verified via `getByRole('link')` count 0 per non-Configure
label), click-navigation to `/experiments/new`, and exactly 4 arrow
separators between the 5 stages (each separator renders BOTH
`<ArrowRight>` and `<ArrowDown>` via CSS-only `md:hidden` /
`hidden md:block`, so `svg.lucide-arrow-right` count = 4 AND
`svg.lucide-arrow-down` count = 4).

### 25. Cost-trend sparkline on the Dashboard Trends section

**Spec:** `frontend/e2e/dashboard-trends.spec.ts` (commit `8533708`)

> As a security researcher returning to the dashboard, I want a Trends
> sparkline showing cost per completed experiment over time, alongside
> the cost-headroom card — so I can see at a glance whether my
> experiment spend is trending upward or downward and decide whether
> to throttle the matrix.

Covers the recharts-backed `SparklineChart` mounted inside the
Dashboard Trends section. The section is conditionally rendered with
`{costSparkData.length >= 2 && <section>...}` (Dashboard.tsx:448),
where `costSparkData` is built from completed experiments only. The
default fixture has 2 completed experiments → section visible by
default; per-test overrides drop to 1 completed (section hidden) and
0 completed (still hidden, plus PipelineDiagram is suppressed by
running experiment B).

Asserts: section heading and "Cost per experiment (USD)" label
visible at the right threshold, conditional hidden states, the SVG
line element renders with `path.recharts-curve`, both axes are
omitted (`hide={true}` produces no `.recharts-xAxis` / `.recharts-yAxis`
SVG groups — assertions scoped to the Trends `<section>` so they don't
break if another recharts user is added elsewhere on the dashboard),
the line stroke is the amber accent `#F5A524` (case-insensitive regex
guards against browser hex normalization), and `CostHeadroomCard`
co-renders in the same grid `<section>`.

### 26. Live cost estimate as you build a new experiment

**Spec:** `frontend/e2e/cost-estimate.spec.ts` (commit `d6b18ef`)

> As a security researcher building a new experiment, I want to see
> the projected total runs and estimated cost (per-model breakdown
> plus a Spend Cap suggestion) update live as I tune the dataset /
> strategy / repetitions controls — so I don't accidentally configure
> a wildly expensive matrix.

Closes the original candidate-J gap. Covers the `CostEstimate`
component on `/experiments/new` (zero prior coverage):

- Idle state: "Configure experiment to see estimate."
- Loading: "Calculating..." with `animate-spin` SVG during in-flight
- Loaded: "Total runs", "Estimated cost: $4.00" amber-text, per-model
  rows (`gpt-4o $2.00` and `claude-3-5-sonnet-20241022 $2.00`)
- Spend Cap input placeholder updates to `Suggested: $4.80` (1.2x of
  the estimated cost) when the estimate resolves; reverts to
  `e.g. 10.00` when cleared
- Estimate gate (`selectedStrategyIds.length > 0 && selectedDataset !== ''`)
  prevents API calls when only one is selected
- Debounce coalescing: 3 rapid clicks within the 400ms window produce
  exactly 1 API request
- API 500 error → catch block sets `estimate=null` → idle state returns

**Critical fixture caveat captured**: the default mockApi `/api/strategies`
returns a string array (`['zero_shot', 'chain_of_thought', ...]`) but
`listStrategiesFull` expects `StrategySummary[]`. With strings,
`<StrategyCard strategy={s}>` accesses `s.id`/`s.name`/etc as
`undefined`, breaking the click-to-select flow. The spec installs a
per-test `**/api/strategies` override returning proper
`StrategySummary[]` (mirroring iter-2's `mockStrategiesRoutes`
pattern) so the strategy-card interactions work.

### 27. Audit dataset provenance via the OriginCard and RecipeSummary

**Spec:** `frontend/e2e/dataset-origin-card.spec.ts` (commit `7958701`)

> As a security researcher reviewing a dataset's provenance, I want to
> see where the dataset came from — for git-origin datasets, the
> upstream URL (clickable link), short commit hash with a one-click
> copy button, optional ref tag, and CVE link; for derived datasets,
> the base-dataset link plus the recipe summary (templates_version,
> applications count with an expandable details list) — so I can
> audit and reproduce the dataset elsewhere.

Covers `OriginCard`, `CopyButton`, and `RecipeSummary` on DatasetDetail
(zero prior e2e coverage; iter-9 only covered the materialize banner
on the same page). Test fixtures override `GET /api/datasets/<name>`
per-test (mockApi has no default handler for the dataset-detail
endpoint — same pattern as iter-9).

Asserts: git-origin URL `<a target="_blank" rel="noopener noreferrer">`
when http(s)-prefixed, plain mono `<span>` otherwise (per the
`/^https?:\/\//` regex in DatasetDetail.tsx:157), 12-char commit slice
+ `data-testid="copy-button"` rendering the FULL hash to clipboard
on click (chromium via grantPermissions; firefox skipped — its
WebDriver-BiDi doesn't expose `clipboard-read`), icon flip ⎘→✓→⎘
verified via a polling `toContainText('⎘', { timeout: 3000 })`
assertion (vs. a brittle `waitForTimeout`), conditional ref/cve_id
rows, derived-kind branch with `base_dataset` Link, RecipeSummary's
templates_version + Applications count + the `<details><summary>Show
applications</summary>` expansion exposing per-app fields including
`seed: 42`, and the invalid-JSON fallback rendering "Invalid recipe
JSON" (Applications line absent).

### 28. Drag-and-drop a `.secrev.zip` bundle onto ExperimentImport

**Spec:** `frontend/e2e/experiment-import-drag-drop.spec.ts` (commit `3192cdc`)

> As a security researcher, I want to drop a `.secrev.zip` experiment
> bundle directly onto the dropzone (without going through a file
> picker), see the dropzone visually highlight while I'm hovering with
> the file, and then see the dropped file's name and size displayed —
> so I can drag-and-drop my way through bundle import without
> clicking through a file dialog.

Closes the original candidate-I gap. Iter-3's `experiment-import.spec.ts`
intentionally tested the file-picker path via `setInputFiles`; the
drag-drop path was uncovered until now.

Asserts: initial placeholder render ("Drop a `.secrev.zip` bundle
here"), `dragover` applies amber-classed highlight (`border-amber-500
bg-amber-50`), `dragleave` reverts to default classes, `drop` sets
the file (name + `2.00 MB — click or drop to change` text shown,
placeholder gone, dragging state cleared), subsequent drops replace
the file, and empty-DataTransfer drops are no-ops (`if (dropped)`
short-circuit) with the placeholder still visible. Uses
`page.evaluateHandle(() => new DataTransfer())` to construct the
DataTransfer object inside the page context, then passes the resulting
`JSHandle` via `locator.dispatchEvent('drop', { dataTransfer: handle })`.
Files are constructed with `'x'.repeat(bytes)` since ASCII strings
have UTF-8 byte length === character count, so `2 * 1024 * 1024`
characters give an exact 2 MiB `File.size`.

### 29. Audit a run's tool calls and flag external URL access

**Spec:** `frontend/e2e/tool-call-audit.spec.ts` (commit `22212a1`)

> As a security researcher reviewing a run's transcript, I want a Tool
> Call Audit table showing every tool invocation with its serialized
> input, timestamp, and a ⚠ URL flag for any tool call whose input
> contains an http(s) URL — so I can audit whether the agent reached
> out to external resources, and click into individual entries to
> inspect the full JSON input.

Covers `RunDetail.tsx:414-461`. The existing `run-detail.spec.ts:68`
only verified the heading existed and the default tool names rendered.

Asserts: dynamic count "Tool Call Audit (N)" in the heading, all rows
rendering (tool name + 100-char truncated input + timestamp), flagged-
row `bg-red-50` styling AND `⚠ URL` badge under BOTH conditions —
URL auto-flag (`/https?:\/\//.test(JSON.stringify(input))`) and
explicit `tc.flagged=true` — non-flagged rows have neither bg nor
badge, 100-char truncation with the full JSON preserved in the
`title` attribute, click-to-expand mounts a `<CodeViewer>` with the
full JSON, click-again collapses, and the single-row expansion
invariant (clicking a different row collapses the previous,
verified via `.cm-editor` count = 1).

The `… ` ellipsis at position 100 is the single Unicode `U+2026`
character; the test compares via `textContent` (not `getByText`)
since the regex matching of multi-codepoint terminators can be
fragile. Locator for row 1 after row 0 expands uses
`.filter({ hasText: 'search_code' }).first()` to dodge the inserted
expansion `<tr>` shifting indices.

### 30. Toggle which builtin tools a strategy gives the agent

**Spec:** `frontend/e2e/strategy-tools.spec.ts` (commit `76b0e11`)

> As a security researcher authoring a strategy, I want to quickly
> toggle which builtin tools (read_file, list_directory,
> search_files, run_command, write_file) are available to the
> agent — with clear visual feedback for selected vs. unselected —
> so the POST body's `tools` array reflects exactly what I picked,
> including pre-existing tools inherited from the fork's parent.

Covers the Tools section in `StrategyEditor.tsx:207-226` (the 5
`COMMON_TOOLS` toggle buttons inside `BundleDefaultForm`). Uses the
same `mockStrategiesRoutes` helper as iters 2 and 15 (worth extracting
to a shared helper at some future point — three duplicates now).

Asserts: exactly 5 buttons render with the right names, parent's
pre-existing `read_file` is `bg-amber-100` active and the other 4
COMMON tools are `bg-white` inactive, click toggles both directions,
multiple selections are independent (`list_directory` + `run_command`
become amber while untouched buttons stay inactive). Save POST body
shape: `default.tools` reflects the toggle state AND preserves
invisible non-COMMON tools — the parent fixture's `search_code` (not
in `COMMON_TOOLS`, never rendered as a button) round-trips on save.
Test 6 uses both `arrayContaining(['write_file', 'search_code'])` and
`toHaveLength(2)` so a leak of extra tools would be caught.

### 31. Live linter for user-prompt-template required placeholders

**Spec:** `frontend/e2e/strategy-placeholder-linter.spec.ts` (commit `6648306`)

> As a security researcher authoring a strategy's user_prompt_template,
> I want a live placeholder linter that flags missing required
> placeholders (`{repo_summary}` and `{finding_output_format}`) in red
> and confirms the ones I've included in green — so I can fix
> prompt-template mistakes before saving instead of seeing runs fail
> at execution time.

Covers `PlaceholderLinter` (StrategyEditor.tsx:81-112). Mounted next
to the user_prompt_template `<textarea>` in BundleDefaultForm.
Production renders RED `missing: {p}` pills for required placeholders
absent from the template, and GREEN pills for everything found —
**including unknown / non-required placeholders**. The `unknown`
variable in production is computed but never rendered distinctly;
the spec captures the actual behavior, not the wishful "unknown is
amber" behavior.

Asserts: empty template → render guard returns null (no pills),
whitespace-only template → same null guard, no-placeholders text →
2 red pills (both required missing), all-required → 2 green pills no
red, mixed required + unknown → 1 red + 2 green (the unknown renders
green), live `fill` updates flip pills red→green reactively, and the
default parent fixture's `{{code}}` template hits the regex
`/\{(\w+)\}/g` matching the inner `{code}` substring → 2 red
required-missing + 1 green `{code}`.

**Helper duplication note**: `mockStrategiesRoutes` is now duplicated
across 4 specs (iters 2, 15, 30, 31). Worth extracting to a shared
`e2e/helpers/mockStrategiesRoutes.ts` in a follow-up iteration.

### 32. Pick Tool Extensions for a strategy with disabled-state safety

**Spec:** `frontend/e2e/strategy-tool-extensions.spec.ts` (commit `3601da9`)

> As a security researcher authoring a strategy, I want checkboxes for
> the available Tool Extensions (Tree-sitter, LSP, DevDocs) — with
> disabled, reduced-opacity styling on unavailable extensions (e.g.,
> DevDocs when the deployment hasn't configured it) — so I can opt
> into compile-time helpers without trying to toggle disabled options,
> and so my POST body's `default.tool_extensions` reflects exactly
> what I selected.

Covers the Tool Extensions section in `StrategyEditor.tsx:228-247`.

**Important context**: the legacy `tool-extensions.spec.ts` was authored
against the old `/experiments/new` Tool Extensions section that has
since been removed in a refactor (the same refactor also removed the
"Search models…" model picker — confirmed broken in iter 13). The
production location of these checkboxes today is StrategyEditor only.

Asserts: all 3 extensions render with the right labels (Tree-sitter,
LSP, DevDocs from default mockApi), available extensions have enabled
checkboxes and labels without `opacity-50`, unavailable DevDocs has
BOTH `disabled` checkbox AND `opacity-50` on its label,
`click({ force: true })` cannot check the disabled DevDocs (HTML5
disabled state is respected even when Playwright's actionability
check is bypassed), Tree-sitter + LSP toggle independently and
populate POST body's `default.tool_extensions` with exactly those
keys (length 2, no DevDocs leak), and pre-existing parent-fixture
`tool_extensions: ['tree_sitter']` is pre-checked on fork-load.

5th spec to copy `mockStrategiesRoutes` — extraction target.

### 33. Server-side validation gates the strategy-save flow

**Spec:** `frontend/e2e/strategy-validation.spec.ts` + `frontend/e2e/helpers/mockStrategiesRoutes.ts` (commit `9778a0d`)

> As a security researcher saving a new or forked strategy, I want
> server-side validation to fire FIRST and surface a list of validation
> errors as a red error block — so I can fix mistakes (missing required
> placeholders, invalid model_id, etc.) before the strategy commits.
> AND if the validation endpoint itself fails or is unavailable, I
> want the save to fall through to the create POST so a transient
> validation outage doesn't block me.

Covers the save flow at `StrategyEditor.tsx:845-868`. When
`validateStrategy('__new__')` returns `{valid: false, errors: [...]}`,
the red error block renders with all errors as `<li>` items and the
create POST is short-circuited. When the validation endpoint network-
aborts or returns 500, the production `.catch(() => null)` silently
swallows the failure and the save proceeds to `createStrategy`. The
button shows "Saving…" while in-flight. `createStrategy` 400 surfaces
`err.message` in `saveErrors`.

**Critical reviewer-caught semantic**: Test 8 verifies the
second-click error-clear flow using `page.unroute(pattern, handler)`
with a NAMED handler reference. The bare `page.unroute(pattern)`
form would remove BOTH the per-test failing override AND the base
helper's `{valid:true}` handler — the unmatched validation request
would then fall through to apiFetch's `.catch(() => null)`, silently
bypassing validation and proceeding to the create POST for the wrong
reason. The named-handler form is the canonical Playwright pattern.

**Helper extraction**: this iteration also creates
`frontend/e2e/helpers/mockStrategiesRoutes.ts` exporting
`mockStrategiesRoutes`, `STRATEGY_BUILTIN_SINGLE`,
`FULL_BUILTIN_SINGLE`, and `CREATED_FORK`. Five existing specs (iters
2, 15, 30, 31, 32) keep their inline copies — migrating them is a
separate refactor. Going forward, new strategy specs should import
from the helper.

### 34. Trends filter selections persist across visits via localStorage

**Spec:** `frontend/e2e/feedback-localstorage.spec.ts` (commit `b1311bf`)

> As a security researcher returning to the Feedback page after a
> session, I want my Trends filter selections (dataset, last-N limit,
> tool-extension filter, since, until dates) to be remembered across
> reloads so I don't have to re-set them on every visit — and I want
> a fresh visit with cleared localStorage to default to the original
> empty / `10` defaults.

Covers the `useTrendFilters` hook in `Feedback.tsx:37-93`. Five keys
(`feedback_trend_dataset`, `_limit`, `_tool_ext`, `_since`, `_until`)
round-trip through the trends-section inputs: clean storage produces
empty / 10 defaults; each individual setter writes to localStorage
immediately (the production `setAndPersist*` calls
`localStorage.setItem` inside the synchronous React state update);
each value survives a page reload because `useState(() =>
localStorage.getItem(...))` re-initializes from storage on mount; all
5 set together persist together; `addInitScript`-pre-seeded values
populate inputs on first mount. Limit is stored as a string (`'50'`
not `50`) per the production `String(v)` call at line 66.

Two patterns used:
- **Navigate-clear-navigate** for "clean state" tests: same-origin
  context required before `localStorage.clear()`.
- **`addInitScript` BEFORE `page.goto`** for pre-seeding tests:
  init-script runs before the page's JavaScript on every navigation,
  so storage values are present when `useState` initializers fire.

---

## Candidate stories for future iterations

Listed roughly in order of estimated value vs implementation effort. Each
points at production code or testids that have no e2e coverage today.

### A. Edit per-vuln-class overrides in the strategy editor

~~Covered in iteration 15~~ — see `strategy-vulnclass-overrides.spec.ts`.

### B. Tool extensions matrix selection

~~Candidate retired in iteration 10~~ — re-checking
`frontend/e2e/tool-extensions.spec.ts` shows it actually covers selection
→ POST body shape (test at line 79), the empty-set case (line 188), and
matrix-table badge rendering (line 135). My initial flag was wrong.

### C. Cross-experiment run comparison

~~Covered in iteration 16~~ — see `runcompare-render.spec.ts`. The
remaining gap noted by the iter-16 reviewer is the
`DatasetMismatchBanner` render path (a `data-testid="dataset-mismatch-banner"`
warning shown when `comparison.dataset_mismatch === true`). Worth a
follow-up iteration if it ever becomes load-bearing.

### D. ConversationViewer message-type rendering

~~Covered in iteration 12~~ — see `conversation-viewer.spec.ts`. The
per-role styling and badge variants are now exercised; the `language`
prop forwarded to `CodeViewer` (`json` for tool, `markdown` for
others) is intentionally NOT asserted because `CodeViewer` registers
no syntax extensions for either, so the `language` flow is not
user-visible.

### E. ModelSearchPicker keyboard interactions

~~Candidate dead — picker no longer mounted~~. As of iteration 13's
investigation, `ModelSearchPicker.tsx` is imported only by its own unit
test. `/experiments/new` (`ExperimentNew.tsx`) no longer renders a
Models section — model id is baked into the strategy. The legacy
`experiment-new.spec.ts` "search models" tests fail (21/28) for the
same reason. Treat this picker as unmounted dead code unless/until it
returns to a real page; do NOT spend an iteration on it.

### F. Hardening: switch `route.continue()` to `route.fallback()` in iter-1 spec

`strategies-browse.spec.ts` from iteration 1 uses `route.continue()` in
its mock fallthroughs. Subsequent iterations established `route.fallback()`
as the right pattern (it chains to the next handler instead of leaking to
the network). Iteration 4's reviewer reconfirmed this. This is a small
hardening pass, not a new user story — fold into a quiet iteration if no
fresh gap emerges.

### G. Smoke test failure path

~~Covered in iteration 13~~ — see `dashboard-smoke-test-failure.spec.ts`.

### H. Dataset injection wizard step error states

~~Covered in iteration 14~~ — see `dataset-inject-errors.spec.ts`. Note
the actual production contract is harsher than the original story
described: a `setError(...)` short-circuits the entire DatasetDetail
page rather than letting the wizard surface the error inline. The
spec faithfully captures that contract — fixing the UX (so the wizard
stays mounted on error) is a separate product change.

### I. ExperimentImport drag-and-drop

~~Covered in iteration 28~~ — see `experiment-import-drag-drop.spec.ts`.
The "inherently flaky in headless browsers" concern proved unfounded
when using `page.evaluateHandle(() => new DataTransfer())` +
`dispatchEvent` — both Chromium and Firefox handle it reliably.

### J. ExperimentNew "estimate" preview

~~Covered in iteration 26~~ — see `cost-estimate.spec.ts`. Note: the
spec captured a fixture-mismatch caveat that any future ExperimentNew
spec must work around — `/api/strategies` mockApi default returns
`string[]`, not `StrategySummary[]`.

---

## Conventions for future loop iterations

Established during iterations 1–9; future agents should mirror these:

- **Worktree-isolated:** spawn the author subagent on a `/tmp/loop-<slug>`
  worktree branched from the current `HEAD`. Symlink `frontend/node_modules`
  from the main checkout to avoid a full reinstall. Merge with a
  `merge: worktree-agent-…` commit and remove the worktree on success.
- **Subagent decomposition:** Sonnet for spec authorship and review (these
  decisions need judgment); Haiku only for purely mechanical fix-ups (rare
  — most fixes have surfaced real semantic issues that need investigation).
- **Independent reviewer:** spawn a separate Sonnet reviewer that has not
  seen the author's reasoning. Feedback has caught real correctness issues
  twice (iter 7's vacuous stopPropagation assertion, iter 8's URL pattern
  mismatch).
- **Mocking pattern:** call `mockApi(page)` in `beforeEach`, then register
  per-test routes after it. Playwright route handlers are LIFO. Use
  `route.fallback()` (chains to next handler) rather than
  `route.continue()` (leaks to network).
- **HTML5 `min` validation gotcha:** numeric inputs with `min={1}` silently
  block form submit when seeded with values below the minimum. Several iter
  fixtures had to bump `max_subagent_*` defaults from 0 to sane positives.
- **Anchor-triggered downloads:** Playwright `page.route` does not reliably
  intercept `<a download>` clicks. Use `page.waitForEvent('download')` for
  Chromium + WebKit; Firefox needs `context.on('request')` capture
  registered before the click.
- **Two browsers per spec:** Playwright config runs both Chromium and
  Firefox. A spec is "complete" only when both projects are green.
- **POST body assertions:** capture via
  `route.request().postDataJSON()` and assert exact field values
  (`toBe`/`toContain`) — array length alone is insufficient.
