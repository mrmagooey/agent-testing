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

### 35. Download a run's full results bundle from inside the run-detail page

**Spec:** `frontend/e2e/run-detail-download.spec.ts` (commit `22749dd`)

> As a security researcher reviewing a single run, I want a "Download
> Run" button that initiates a browser download of the experiment's
> full results bundle — sharing the experiment's results-download
> endpoint but reachable from inside any of its runs — so I can grab
> the artifact without navigating back up to the experiment detail
> page first.

Covers the click flow on `RunDetail.tsx:191`'s `<DownloadButton>`.
The existing `run-detail-interactions.spec.ts:391-393` only checks
visibility; this spec adds:

- Suggested filename is `experiment-<expId>-reports.zip` (the
  EXPERIMENT id, not the run id — even though we're on a run page —
  because RunDetail passes `experimentId` from URL params, not
  `runId`)
- Download URL targets `/api/experiments/<expId>/results/download`
  with NO `/runs/<runId>` segment (regression guard against future
  wiring drift)
- Button click does not navigate the page (the `<a download>`
  attribute prevents navigation)
- The temporary `<a>` created in `handleDownload` is cleaned up from
  the DOM after click via `document.body.removeChild`

**Browser caveat captured**: Firefox skips 4 of 5 tests (via
`test.skip(browserName === 'firefox', ...)`) because `<a download>`
clicks don't reliably fire Playwright's `download` event in Gecko —
same precedent as iter-5's `export-menu.spec.ts`. The browser-
agnostic test 3 uses `context.on('request')` as the firefox-safe
path so the URL contract still has at least one assertion running
on both browsers.

### 36. Dashboard auto-refreshes the experiments list every 15 seconds

**Spec:** `frontend/e2e/dashboard-polling.spec.ts` (commit pending)

> As a security researcher watching a long-running experiment, I want
> the Dashboard's experiments list to refresh automatically every 15
> seconds so I see live progress (running → completed transitions)
> without manually reloading the page.

Covers `Dashboard.tsx:23` (`POLL_INTERVAL_MS = 15_000`) and the
`useEffect`/`setInterval` polling loop at lines 228–234. Five tests:

- Initial mount fires GET `/api/experiments` and stabilises before
  the 15 s interval (assertion uses `toBeGreaterThanOrEqual(1)`
  because `<StrictMode>` double-mounts `useEffect` in dev — see
  caveat below)
- After advancing 15 s of fake clock time, a second GET fires
- Polled response replaces the table — an experiment whose status
  flips from `running` → `completed` between fetches disappears
  from the "Active experiments" section and appears in "Recent
  experiments" (the page splits experiments into two tables, not
  one row that mutates)
- Polling continues — a third GET fires after 30 s total
- Navigating away from `/` (e.g. to `/findings`) unmounts the
  page and the cleanup tears down `setInterval`; no further GETs
  fire even after advancing the fake clock 30 s

**Technique**: Uses Playwright's `page.clock.install({ time })` +
`page.clock.runFor(15_000)` to deterministically advance time. No
`waitForTimeout`-based real-time waits — baseline `callCount`
captures use `expect.poll(() => callCount).toBeGreaterThanOrEqual(1)`
so they settle on whatever StrictMode's actual call count happens
to be without racing on slow CI. Test 5 guards against vacuous
pass with explicit `toHaveURL(/\/findings/)` + Findings-page
heading visibility before capturing the post-nav baseline.

**StrictMode caveat captured**: dev mode mounts `useEffect` twice
so the initial `fetchExperiments(true)` fires 2x and 2 setInterval
instances are created (the first cleared immediately by cleanup).
Tests treat this as opaque by capturing `baseline` post-mount
rather than asserting `count == 1`; production users on a
`vite preview`-style build see only 1 fetch, so the user-visible
behaviour is unaffected.

**Result**: 10/10 pass on chromium and firefox.

### 37. GlobPreview shows match count and example files for per_file rules

**Spec:** `frontend/e2e/glob-preview.spec.ts` (commit pending)

> As a security researcher authoring a per_file strategy override,
> I want a live glob-match preview that shows exactly how many of
> the sample files my pattern matches, lists up to 3 examples (with
> a +N-more suffix when there are more), and uses singular/plural
> grammar correctly — so I can validate my pattern before saving.

Covers `StrategyEditor.tsx:114-127`'s `GlobPreview` component
running against the 10-file `GLOB_PREVIEW_SAMPLE_FILES` constant.
Eight tests:

- Empty pattern → component returns null (no DOM node)
- Whitespace-only pattern → null
- Singular grammar: `README.md` → "Matches 1 sample file: README.md"
  (no trailing `s`; the `: README.md` suffix prevents the assertion
  from accidentally matching the plural form)
- Plural with 0 matches: `nonexistent/*.go` → "Matches 0 sample
  files" with NO listing colon
- Plural with 2 matches: `**/*.tsx` → 2 files fully listed
- Truncation: `**/*.py` → first 3 listed + `+4 more` suffix
- Live update: pattern change replaces preview text without reload
- `*` does NOT cross `/`: `src/*.py` → 0; `src/*/*.py` → 5 — the
  globMatches translation `*` → `[^/]*` is the source of this.

**Result**: 16/16 pass across chromium and firefox.

### 38. Browser back/forward restores Findings filter state

**Spec:** `frontend/e2e/findings-back-forward.spec.ts` (commit pending)

> As a security researcher exploring findings across runs, I want my
> browser Back and Forward buttons to restore the filter state of my
> previous and next views — so I can compare two filter sets without
> re-typing them or losing my context.

Covers `Findings.tsx`'s URL-as-source-of-truth filter model. Unlike
RunDetail (which uses `setSearchParams(..., { replace: true })`),
the global Findings page uses default push semantics so each filter
toggle creates a history entry. Five tests:

- `goBack()` after applying `severity=critical` → URL clears the
  param, the useEffect re-fires GET `/api/findings` without it, and
  the chip class returns to the unselected state
- `goForward()` re-applies the dropped filter and re-fires the GET
- Two filter changes accumulate (different keys merge, same key
  replaces); `goBack()` twice unwinds them step-by-step
- Deep-linked URL `?severity=critical&vuln_class=sqli` renders both
  chips as selected on mount and the initial GET carries both params
- Deep-linked URL → `goto('/')` → `goBack()` correctly remounts the
  page with the filter restored (regression guard for stack restore
  after a remount, not just within one mount)

**Technique**: passive request capture via `page.on('request', …)`
into a `requestUrls[]` array — does NOT register a second
`page.route` so `mockApi`'s base handler keeps serving findings.
Each transition uses `expect.poll(() => urls.length).toBeGreaterThan(prev)`
to wait for the post-back/forward re-fetch, then asserts on
`urls.at(-1)`. Selected-chip detection uses class match
(`bg-amber-600`) since the production code does not set
`aria-pressed`.

**Result**: 10/10 pass across chromium and firefox.

### 39. Severity-sorted findings reflect semantic order, not alphabet

**Backend fix:** `src/sec_review_framework/db.py` (commit pending)
**Backend tests:** `tests/unit/test_coordinator_findings_search.py` (+4 tests, +2 fixtures)

> As a security researcher reviewing findings on the Findings page,
> when I sort by "Severity (high→low)" I expect critical findings
> before high, high before medium, medium before low — matching the
> semantic order I think in, not the alphabetical order of the
> string column.

**The bug**: `search_findings_global` used `ORDER BY f.{sort_col} {sort_dir}`
directly. With `sort_col = severity` SQLite ordered the TEXT column
alphabetically: `critical < high < low < medium`. So
`?sort=severity+desc` returned `medium → low → high → critical`,
which contradicts the dropdown label "Severity (high→low)" the
frontend exposes (`Findings.tsx:238`).

**The fix**: branch on `sort_col == "severity"` and emit a CASE rank
in the ORDER BY: `critical=3, high=2, medium=1, low=0, ELSE=-1`. The
existing `sort_dir` allow-list (`asc`/`desc`) is unchanged. SQL
injection surface is unchanged — the rank values are SQL constants,
the column name still allow-listed. Other sortable columns
(`created_at`, `confidence`, `vuln_class`, etc.) keep direct
`ORDER BY f.{col}` since their natural ordering is correct.

**Tests added**:
- Severity-rank desc with critical/high/medium/low seeded → asserts
  `[critical, high, medium, low]`. Confirmed to fail on pre-fix code
  (returns `[medium, low, high, critical]`).
- Severity-rank asc → `[low, medium, high, critical]`.
- Date-range filter (`created_from` + `created_to`) — first test
  exercising the previously-uncovered SQL date-range branch.
  Documents the lexicographic-truncation gotcha: `created_to=YYYY-MM-DD`
  silently excludes `YYYY-MM-DDT00:00:00+00:00` because `T` > nothing
  in string comparison; tests use `T23:59:59` to be inclusive.
- Combined filters AND together: `?severity=high&vuln_class=sqli`
  returns only the finding satisfying BOTH (intersection, not union).

**Frontend coverage**: already in place. `findings-extended.spec.ts:163`
verifies the dropdown sends `?sort=severity+desc`; the rendering path
trusts whatever order the backend returns. The fix here makes the
backend honour the user-visible promise.

**Result**: 28/28 unit tests pass; broader coordinator-API slice
(107 tests) green; first cross-stack iteration of this loop.

### 40. Mistyped or stale URLs render a clear 404 page

**Frontend feature + spec:** `frontend/src/pages/NotFound.tsx` (new),
`frontend/src/App.tsx` (wired), `frontend/e2e/not-found.spec.ts`
(5 tests / 10 across browsers) — commit pending.

> As a user mistyping a URL or following a stale link, I want a
> clear 'Page not found' message instead of an empty page, so I
> know what happened and have a path back to the app's main routes.

**The bug**: `App.tsx` (lines 105-135) defines explicit routes but
no `<Route path="*">`. The backend (`coordinator.py:3169-3183`)
serves the SPA shell for any `Accept: text/html` GET, so unknown
URLs reach React Router and silently render an empty `<main>` under
the persistent NavBar. Users saw nav chrome with nothing inside it.

**The fix**: a 32-line `NotFound` component that uses
`useLocation()` to render the path the user actually typed, plus
two react-router `<Link>`s (Back to dashboard, Findings) for client-
side navigation. Wired as the LAST `<Route path="*">` in App.tsx.

**Tests**:
- Unknown path renders the heading
- The current path is reflected back in the message body
- "Back to dashboard" link navigates to `/` and shows the dashboard
- NavBar is still visible on the 404 page (layout shell preserved)
- Deep nested unknown path (`/experiments/abc/runs/xyz/zzz-not-real`)
  also falls through to the catch-all — guards against a future bug
  where someone collapses a route into a wildcard segment

**Result**: 10/10 pass on chromium and firefox; typecheck clean;
no regressions in dashboard/nav smoke slice.

### 41. Date-only `created_to` filter is end-of-day inclusive

**Backend fix:** `src/sec_review_framework/coordinator.py`
**Backend tests:** `tests/unit/test_coordinator_findings_search.py`
(+2 tests; commit pending)

> As a security researcher filtering findings by date on the
> Findings page, when I set 'Created to: April 20' I expect findings
> created at any time on April 20 to be included in the results —
> not silently dropped because the backend treats my bare-date input
> as midnight.

**The bug**: The frontend uses `<input type="date">` (`FindingsFilterBar.tsx:141-148`)
which produces values like `2026-04-20` (no time component). The backend
ran `WHERE f.created_at <= '2026-04-20'`, but `created_at` is stored as
full ISO-8601 (e.g. `2026-04-20T14:30:00+00:00`). SQLite TEXT comparison
is lexicographic: any same-day timestamp sorts GREATER than the bare date,
so a finding created on April 20 was silently EXCLUDED when the user
asked for "up to April 20." Iter 39's `test_date_range_filter` worked
around this with `T23:59:59`; this iteration is the actual fix.

**Asymmetry note**: `created_from=YYYY-MM-DD` was already correct.
`'2026-04-20T00:00:00' >= '2026-04-20'` is TRUE (longer string with content
after the prefix sorts greater). Only `created_to` needed normalisation.

**The fix**: in `coordinator.search_findings_global`, when `created_to`
matches `_DATE_ONLY_RE` (`^\d{4}-\d{2}-\d{2}$`), extend it to
`{date}T23:59:59.999999` before passing to the DB layer. Full-timestamp
inputs pass through verbatim so callers can still do strict sub-day
filtering. Fix lives at the API-contract boundary in `coordinator.py`,
not in `db.py` — keeps the DB layer purely mechanical.

**Tests added**:
- `test_date_range_to_bare_date_inclusive` — `created_to=2026-04-20`
  must include `date-in2` (Apr 20 at midnight). Demonstrably fails on
  pre-fix code.
- `test_date_range_to_full_timestamp_unchanged` — `created_to=2026-04-19T23:59:59`
  must EXCLUDE Apr 20 findings. Verifies the regex doesn't trigger on
  full timestamps and strict comparison still works.

**Result**: 30/30 unit tests pass; iter 39's existing `test_date_range_filter`
(which used the verbose workaround) continues to pass since full-timestamp
inputs aren't normalised.

### 42. Reclassify endpoint rejects invalid match_status values

**Backend fix:** `src/sec_review_framework/coordinator.py`
**Backend test:** `tests/integration/test_experiment_lifecycle_api.py` (+1)

> As a security researcher reclassifying a finding, I want the API
> to reject invalid status values so a buggy or malicious client
> can't write arbitrary strings into our findings index — which
> would silently break the filter UI's match_status chips.

**The bug**: `ReclassifyRequest.status: str` (no validation). A request
like `{"finding_id": "f1", "status": "pinkflamingos"}` was accepted and
written verbatim into `findings.match_status`. The filter chips
(`match_status=tp|fp|fn|unlabeled_real`) would never match the rogue
row, effectively making it invisible under any filter — silent data
corruption.

**The fix**: tighten the field to `Literal["tp", "fp", "fn", "unlabeled_real"]`.
Pydantic 422s any other value before it reaches the coordinator. The
allow-list mirrors the short codes already stored by `_infer_match_status`
in `db.py:1392`. The long-form `MatchStatus` enum in
`data/evaluation.py:43` (`true_positive`, `false_positive`, etc.) is NOT
what's stored in the DB; that mismatch is intentionally out of scope
here — the system relies on short codes everywhere.

**Tests**:
- New: `test_reclassify_invalid_status_returns_422` posts a rogue
  status value and asserts 422 + that the error mentions the field
  name and at least one allowed value.
- Existing 6 reclassify tests continue to pass — the only client
  (`FindingsExplorer.tsx`) hardcodes `"unlabeled_real"`, which is in
  the allow-list.

**Result**: 23/23 lifecycle tests pass on main; the new test was
verified to fail without the fix (the rogue status currently passes
Pydantic and falls through to a 404 from the run-lookup, not a 422
— exactly what the validation gap looks like).

### 43. Cancel endpoint preserves terminal experiment status

**Backend fix:** `src/sec_review_framework/coordinator.py`
**Backend tests:** `tests/integration/test_experiment_lifecycle_api.py` (+3)

> As a researcher whose dashboard briefly shows stale state, or as a
> direct API consumer, the cancel endpoint must not clobber an
> already-terminal experiment's status — a completed experiment
> should remain 'completed' regardless of how many cancel requests
> arrive.

**The bug**: `coordinator.cancel_experiment` (line ~909) ran
`update_experiment_status(experiment_id, "cancelled")` unconditionally.
Even for an experiment already in `completed` or `failed` state, a
stray `POST /experiments/<id>/cancel` flipped the DB status to
`cancelled` — silently destroying the result history's labelling.

The frontend gates the Cancel button via `!isTerminal`
(`ExperimentDetail.tsx`), so the realistic exposure is direct API
consumers and a brief frontend race during status transitions.

**The fix**: at the top of `cancel_experiment`, look up the
experiment via `db.get_experiment`. If it doesn't exist, or its
status is in `{completed, failed, cancelled}`, return `0` early
and skip the K8s job listing, run-status flips, and experiment-
status update. Existing non-terminal cancellation still works.

**Tests added** (3, all `pytest.mark.asyncio` since they need to
seed DB rows directly):
- `test_cancel_terminal_experiment_is_noop` — seed completed,
  cancel, verify status stays `completed`
- `test_cancel_already_cancelled_is_idempotent` — seed cancelled,
  cancel again, verify status stays `cancelled`
- `test_cancel_failed_experiment_keeps_failed_status` — seed failed,
  cancel, verify status stays `failed`

**`delete_experiment` side-effect note**: `delete_experiment` calls
`cancel_experiment` first then deletes files/DB rows. After the fix,
terminal experiments skip the status flip in the cancel call but
deletion proceeds normally — no test relied on the side-effect.

**Result**: 26/26 lifecycle tests pass; broader slice (89 tests
across lifecycle + routes_extended + coordinator_api) green; tests
demonstrably fail without the fix.

### 44. Cancel-modal surfaces backend errors instead of failing silent

**Frontend fix:** `frontend/src/pages/ExperimentDetail.tsx`
**Frontend spec:** `frontend/e2e/experiment-detail-cancel-error.spec.ts` (4 tests)

> As a security researcher cancelling a long-running experiment, I
> want clear feedback if the cancel request fails — currently the
> modal closes silently with no indication, leaving me to wonder if
> it worked.

**The bug**: `handleCancelConfirm` had `try { await cancelExperiment } finally { setShowCancelModal(false) }` — the modal closed in the `finally` block regardless of whether the API succeeded or threw. A network blip, 500, or backend reject left the user with NO indication that anything went wrong.

**The fix**:
- Add `cancelError: string | null` state.
- On success: close modal as before.
- On error: capture `(err instanceof Error ? err.message : 'Cancel failed')` into `cancelError`, KEEP the modal open, re-enable the buttons. The user sees the error and can retry or dismiss.
- Dismissing the modal (the modal's own Cancel button) resets both `showCancelModal` AND `cancelError` so a re-open starts fresh.
- Render the error inline above the buttons in `CancelConfirmModal` with `role="alert"` (live-announced for screen readers; also a stable selector for tests via `getByRole('alert')`).
- The `error` prop on `CancelConfirmModal` is `string | null | undefined` (optional) so the component remains future-portable to other call sites without a forced prop.

**Tests** (4):
- 500 + `{detail: "Cancel rejected: K8s unreachable"}` → exact error visible inside modal, modal stays open, buttons re-enabled
- 500 + empty body → fallback error message visible (`role="alert"` selector)
- 200 success → modal closes (sanity)
- Dismiss after error → re-opening modal starts with no error shown (state-leak guard)

**Result**: 8/8 tests pass on chromium and firefox; typecheck clean.

### 45. DELETE experiment actually removes the DB rows

**Backend fix:** `src/sec_review_framework/db.py`,
`src/sec_review_framework/coordinator.py`
**Backend tests:** `tests/integration/test_experiment_routes_extended.py` (+3, docstring update)

> As an admin cleaning up old experiments, I want
> DELETE /experiments/<id> to actually remove the experiment from
> the database — not just delete its files. Otherwise the
> experiment continues to appear in the dashboard list with
> broken/empty data.

**The bug**: `coordinator.delete_experiment` cleaned up files on
disk and revoked upload tokens but never removed the experiment,
run, or finding rows from SQLite. After `DELETE /experiments/<id>`:
- `GET /experiments` still returned the row
- `GET /experiments/<id>` still returned 200 with metadata
- `GET /experiments/<id>/results` 404'd (files gone)
- `GET /findings` still included findings from the deleted experiment

The pre-existing test `test_delete_existing_experiment_removes_it`
even acknowledged this with a docstring saying "the current
implementation cancels jobs and removes output files but does not
purge the DB row." Documented as a known gap.

**The fix**: add `Database.delete_experiment(experiment_id)` that
deletes child-first within a single connection:
1. `DELETE FROM findings WHERE experiment_id = ?` (FTS index stays
   in sync via the existing `findings_fts_ad` trigger)
2. `DELETE FROM run_upload_tokens WHERE run_id IN (SELECT id FROM
   runs WHERE experiment_id = ?)`
3. `DELETE FROM runs WHERE experiment_id = ?`
4. `DELETE FROM experiments WHERE id = ?`

Wire it in at the END of `coordinator.delete_experiment`, after
the file cleanup. Pre-existing FTS5 trigger handles findings_fts
consistency automatically — no manual rebuild needed.

**Tests added** (3 + docstring update):
- `test_delete_experiment_removes_db_row` — submit, delete, assert
  not in `GET /experiments` and `GET /experiments/<id>` is 404
- `test_delete_experiment_removes_runs_and_findings` — seed a
  finding via `db.upsert_findings_for_run`, delete, assert
  `db.list_runs` is empty AND `db.query_findings` returns 0
- `test_delete_experiment_idempotent_for_missing` — DELETE on a
  nonexistent id returns 204 with no error

**Result**: 15/15 pass on the routes file; broader regression slice
(118 tests) all green; new tests demonstrably fail without the fix.
The pre-existing test's outdated docstring was also updated to
reflect the new contract.

### 46. Strategy delete honours frontend's "referenced by runs" warning

**Backend fix:** `src/sec_review_framework/db.py`
**Backend tests:** `tests/api/test_strategies_api.py` (+3),
`tests/unit/test_db_user_strategies.py` (replaced placeholder)

> As a strategy author cleaning up old strategies, I expect the API
> to honour the frontend's claim that 'strategies referenced by
> existing runs cannot be deleted' — currently the backend rubber-
> stamps the delete regardless, leaving the frontend's warning a lie.

**The bug**: `Database.strategy_is_referenced_by_runs` was a stub
that always returned `False` with a TODO ("runs table does not yet
have a strategy_id column"). The 409 branch in `delete_strategy`
(`coordinator.py:4011`) was therefore unreachable, so any user
strategy could be deleted including ones in active use. Meanwhile
`StrategyViewer.tsx:317` told users:
> "Strategies referenced by existing runs cannot be deleted."

That message was a lie — and orphaned strategies could leave the
detail page 404'ing for any user who follows a stale link.

**The fix**: replace the stub with a SQLite JSON1 query against
`experiments.config_json`:

```sql
SELECT EXISTS(
    SELECT 1 FROM experiments
    WHERE EXISTS(
        SELECT 1 FROM json_each(json_extract(config_json, '$.strategy_ids'))
        WHERE value = ?
    )
)
```

The runs table itself doesn't have a strategy_id column, but the
strategy_ids that the experiment was submitted with are embedded
in `experiments.config_json["strategy_ids"]` (a list). The function
name keeps "_by_runs" because that's how users think about it
(every experiment has runs that USE the strategy).

NULL-safe: if `config_json` lacks a `strategy_ids` key or fails to
parse, `json_extract` returns NULL → `json_each` yields no rows →
returns False. JSON1 is shipped with stock CPython 3.9+, so no
build flag concern.

**Tests added** (3 + 1 unit replacement):
- API: `test_delete_strategy_referenced_by_experiment_returns_409`
- API: `test_delete_strategy_unreferenced_succeeds_204`
- API: `test_delete_strategy_referenced_by_terminal_experiment_returns_409`
  — completed experiments still hold a historical reference; delete
  must still 409
- Unit: replaced the old always-True placeholder with a method test
  covering exact match, no match, and substring-of-id (proves the
  JSON1 whole-element match, not LIKE-style substring)

**Result**: 35/35 strategy + DB-strategy tests pass; new tests
demonstrably fail on the unfixed stub.

### 47. Strategy delete dialog surfaces 409 + role=alert hardening

**Frontend small-fix:** `frontend/src/pages/StrategyViewer.tsx`
**Frontend spec:** `frontend/e2e/strategy-delete.spec.ts` (3 tests)

Complementary to iter 46. Now that the backend's
`strategy_is_referenced_by_runs` returns truthful results and the
DELETE endpoint can really 409, e2e coverage for the user-facing
dialog flow.

> As a strategy author, when I try to delete a strategy that's
> still referenced by an experiment, I want to see a clear error
> message in the confirmation dialog instead of having the delete
> silently fail or appear to succeed.

**Source change**: one line — add `role="alert"` to the inline
deleteError block (`StrategyViewer.tsx:321`). The frontend already
captured `e.message` from the API error into `deleteError` state
and rendered it in the dialog; the only gap was screen-reader
announcement and a stable selector for tests. Mirrors iter 44's
fix to the cancel dialog.

**Tests** (3):
- Successful 204 delete navigates to `/strategies`.
- 409 with `detail` from the backend → exact text visible in
  `getByRole('alert')`, dialog stays open, Delete button re-enables
  (no longer "Deleting…").
- 500 fallback → some error message visible, dialog stays open.

The spec uses an inline `FULL_USER_STRATEGY` with `is_builtin: false`
so the Delete affordance renders. Per-test DELETE override sits on
top of `mockApi`'s default GET handler via LIFO routing and
`route.fallback()` for non-DELETE methods.

**Result**: 6/6 tests pass on chromium and firefox; 140/140 across
all strategy specs; typecheck clean.

### 48. Reclassify modal surfaces errors instead of failing silent

**Frontend fix:** `frontend/src/components/FindingsExplorer.tsx`
**Frontend tests:** `frontend/e2e/findings-reclassify-modal.spec.ts` (+2)

> As a security researcher reclassifying a finding, when the API
> rejects my request, I want clear feedback in the modal —
> currently a 500 or validation error leaves the modal stuck open
> with no indication of what went wrong.

Third application of the same UX pattern fixed in iter 44 (cancel
modal) and iter 47 (strategy delete). The reclassify modal had:

```tsx
try {
  await reclassifyFinding(...)
  setReclassifyModal(null)  // close on success only
} finally {
  setReclassifyLoading(false)
}
```

If the API throws, `setReclassifyModal(null)` is correctly skipped
(modal stays open) but no error reaches the user — the modal sits
there with no feedback and the rejected promise propagates as an
unhandled rejection.

**The fix**: same pattern as iter 44/47 — add `reclassifyError`
state, capture the message on error, render an inline
`<div role="alert">` above the modal buttons, reset the error on
modal open and dismiss so re-opens start fresh.

**Tests** (2 added):
- 422 from backend with `{"detail": "Reclassification rejected: …"}`
  → `getByRole('alert')` shows the message, modal heading still
  visible, Confirm button re-enabled.
- Dismiss-after-error → re-open shows no alert (state-leak guard).

**Result**: 20/20 reclassify-modal tests pass; broader
`--grep="reclassify"` slice (36/36) green; typecheck clean.

This iteration also retroactively gives iter 42's
`Literal["tp","fp","fn","unlabeled_real"]` validation a user-visible
surface — a buggy client now sees the Pydantic error inline instead
of the modal silently sticking.

### 49. Dataset file viewer line_count is off-by-one

**Backend fix:** `src/sec_review_framework/coordinator.py`
**Backend tests:** `tests/integration/test_datasets_extra_api.py` (+1 parametrized, 6 cases)

> As a security researcher viewing a dataset source file, I want
> the line count to reflect the actual number of lines in the file
> — currently a typical 2-line file with a trailing newline shows
> as '3 lines' and an empty file shows '1 line'.

**The bug**: `get_file_content` (`coordinator.py:2226`) computed:
```python
"line_count": content.count("\n") + 1,
```
This is wrong for the common case of files ending in a trailing
newline (which most editors add by default):
- `""` → 1 (should be 0)
- `"hello\n"` → 2 (should be 1)
- `"a\nb\n"` → 3 (should be 2)
- `"a\nb\nc\n"` → 4 (should be 3)

**The fix**: replace with `len(content.splitlines())`. Python's
`str.splitlines()` correctly treats a trailing `\n` as a terminator
rather than a separator that introduces a new empty line — the
canonical line-count idiom.

**Tests added** (1 parametrized, 6 cases): empty file, single line
with/without trailing newline, two/three lines with/without
trailing newline. Demonstrably fails on 4 of 6 cases without the
fix; the two cases without trailing newlines coincidentally passed
under the old formula.

**Result**: 36/36 dataset-API tests pass; broader 79-test slice
across dataset routes/security/extra green.

### 50. Estimate endpoint rejects zero / negative target_kloc

**Backend fix:** `src/sec_review_framework/coordinator.py`
**Backend tests:** `tests/integration/test_coordinator_api.py` (+2)

> As a security researcher previewing experiment cost, I want the
> API to reject zero or negative target_kloc values so my estimate
> doesn't return a misleadingly low (or negative) cost that lets me
> submit a real experiment with no proper budget check.

**The bug**: `EstimateRequest.target_kloc: float` had no
validation. The endpoint computes
`tokens = int(target_kloc * AVG_TOKENS_PER_KLOC)` and feeds tokens
into `cost_calculator.compute(...)` which is a linear function. So:
- `target_kloc=0` → tokens=0 → estimate `$0.00` (misleading: the
  experiment will still hit the model with non-zero per-call cost)
- `target_kloc=-100` → negative tokens → negative cost (nonsensical)

The frontend hardcodes `target_kloc: 10.0` (`ExperimentNew.tsx:124`)
so users can't trigger this via the UI today, but the API surface
should be defensive — direct API consumers and any future UI that
exposes the field would otherwise silently produce wrong numbers.

**The fix**: `target_kloc: float = Field(gt=0)`. Pydantic 422s any
non-positive value before it reaches the computation. The existing
3 estimate tests (with positive target_kloc) continue to pass.

**Tests added** (2):
- 0 → 422, error mentions `target_kloc`
- -100 → 422, error mentions `target_kloc`

**Result**: 53/53 coordinator-API tests pass on main.

### 51. Delete experiment invalidates the trends cache

**Backend fix:** `src/sec_review_framework/coordinator.py`
**Backend test:** `tests/integration/test_experiment_routes_extended.py` (+1)

> As an admin who just deleted a stale experiment, I want the
> trends graph to immediately stop showing data from that
> experiment — currently the trends are cached for up to 60 seconds
> and a deleted completed experiment continues to appear until the
> cache expires.

**The bug**: `coordinator.delete_experiment` (after iter 45's DB
cleanup) tore down filesystem outputs, run rows, and findings — but
never called `_invalidate_trends_cache()`. The trends endpoint
caches `(dataset, limit, tool_ext, since, until) → result` for
`_TRENDS_CACHE_TTL_S = 60s`. So a freshly-deleted completed
experiment continued to appear in the trends graph until the cache
expired naturally. Lower-priority than data correctness but a real
"why is the deleted thing still there" UX surprise.

**The fix**: one-line — call `self._invalidate_trends_cache()` at
the end of `delete_experiment`, alongside the existing call site in
`finalize_experiment` (line 907).

**Test**: pre-seed the cache with a sentinel entry, DELETE any id,
assert the cache is empty afterwards. Demonstrably fails on the
unfixed code (sentinel survives the delete).

**Result**: 16/16 routes-extended tests pass.

### 52. Import CVE by id alone now actually works

**Backend fix:** `src/sec_review_framework/coordinator.py`
**Backend tests:** `tests/integration/test_datasets_extra_api.py` (+2),
`tests/unit/test_coordinator_cve_injection.py` (1 updated)

> As a security researcher who just resolved a CVE on the CVE
> Discovery page, I want to click Import and have the dataset
> created — currently the backend rejects the request because the
> frontend sends only the cve_id but the API requires the full
> CVEImportSpec, breaking the user flow.

**The bug**: `frontend/src/api/client.ts` `importCVE(cveId, datasetName?)`
sent at most `{cve_id, dataset_name}`. The backend `coordinator.import_cve`
called `CVEImportSpec(**spec)` directly. `CVEImportSpec` requires
`cve_id`, `repo_url`, `fix_commit_sha`, `dataset_name`, `cwe_id`,
`vuln_class`, `severity`, `description` — so any id-only request
400'd with "field required" errors. The frontend's
`handleImportResolved` had no `.catch()`, so the error propagated
as an unhandled rejection and the user saw no feedback.

The CVEImporter class already had `import_from_id(cve_id, dataset_name)`
that resolved via `CVEResolver` and dispatched to `import_from_spec` —
the coordinator just wasn't using it.

**The fix**: in `coordinator.import_cve`, detect id-only requests
(`set(spec.keys()) <= {"cve_id", "dataset_name"}`) and dispatch to
the resolver-then-importer path: build a `CVEImporter`, call
`importer.resolver.resolve(cve_id)`, derive a `dataset_name` if
not provided, and call `import_from_id`. Persist the resulting
dataset row + labels using the same DB calls as the spec path.
Returns 502 on resolver exceptions, 404 if the resolver returns
None ("can't resolve to a GitHub repo + fix commit").

The pre-existing full-spec path is unchanged — direct API consumers
that already build a complete spec hit the same code as before.

**Tests**:
- `test_import_cve_id_only_resolves_and_imports` — POST `{cve_id}`,
  mock `_build_cve_importer` to return a fake importer with stubbed
  resolver + `import_from_id`. Asserts 201 + `labels_created` count.
- `test_import_cve_id_only_unresolvable_returns_404` — resolver
  returns None → 404 with "resolve" in detail.
- Updated `test_import_cve_invalid_spec_raises_400` — id-only
  requests with an unresolvable id now 404 (not 400 for missing
  fields), reflecting the new contract.

**Result**: 62/62 dataset-API + CVE-injection tests pass; new tests
demonstrably fail on the unfixed code.

### 53. CVE Discovery import buttons surface errors instead of swallowing

**Frontend fix:** `frontend/src/pages/CVEDiscovery.tsx`
**Frontend tests:** `frontend/e2e/cve-discovery-import-errors.spec.ts` (3)

> As a security researcher importing a CVE, when the resolver fails
> or the import errors, I want clear feedback in the UI — currently
> the import buttons silently swallow the error and just stop being
> 'Importing…' with no indication of what went wrong.

Fourth application of the silent-error UX fix pattern (iters 44, 47,
48). Both `handleImport` and `handleImportResolved` had `try { … }
finally { setImporting(false) }` with no catch — so iter 52's
backend errors (404 unresolvable, 502 resolver-failed, 400 git-
failure) propagated as unhandled rejections.

**The fix**: same pattern as the prior three iterations.
`importError: string | null` state; both handlers capture
`err.message` (fallback `'Import failed'`); reset on tab switch,
on fresh search, and on Resolve. Render inline `<div role="alert">`
in both tabs.

**Tests** (3):
- Resolve tab: 404 with `{detail: "Could not resolve …"}` →
  `getByRole('alert')` shows detail; "Imported successfully" NOT
  visible; button re-enables.
- Tab-switch state reset: error visible → switch to Search → switch
  back to Resolve → no alert.
- Search tab: 500 from a row Import → alert visible above the
  candidate table.

**Result**: 6/6 tests pass on chromium + firefox; typecheck clean.

This iteration completes the matched-pair with iter 52: the backend
now returns meaningful errors AND the frontend now displays them.

### 54. Successful cancel refetches the experiment immediately

**Frontend fix:** `frontend/src/hooks/useExperiment.ts`,
`frontend/src/pages/ExperimentDetail.tsx`
**Frontend test:** `frontend/e2e/experiment-detail-cancel-error.spec.ts` (+1)

> As a security researcher who just cancelled an experiment, I want
> the page to immediately reflect the new 'cancelled' status —
> currently the status badge stays 'running' for up to 10 seconds
> while the polling cycle catches up, leaving me unsure if the
> cancel succeeded.

**The bug**: `useExperiment` polls `getExperiment` every 10s
(`POLL_INTERVAL_MS`). After a successful cancel, the modal closed
but the page kept showing the old status until the next poll
fired. The Cancel button (gated on `!isTerminal`) also stayed
visible during the lag, inviting a confusing second cancel
attempt.

**The fix**: expose a `refetch` function from `useExperiment`
(wrapping the existing `fetchExperiment`) and call it immediately
after a successful `cancelExperiment` in `handleCancelConfirm`.
The status badge flips and the Cancel button disappears within
the round-trip of the GET, not the 10s poll.

**Test**: a per-test `page.route` that flips its GET-response
branch on `cancelPosted = true` (set inside the POST handler).
The cancel POST → refetch hits the second branch → assertion sees
'cancelled' visible within 3 s, well below the 10 s poll.

**Result**: 10/10 cancel-error tests pass on both browsers
(4 pre-existing + 1 new).

---

### 55. CVE Discovery rejects invalid patch-size ranges with a useful message

**Backend fix:** `src/sec_review_framework/coordinator.py`
(`Coordinator.discover_cves`)
**Frontend fix:** `frontend/src/pages/CVEDiscovery.tsx`
**Backend test:** `tests/integration/test_datasets_extra_api.py` (+6)
**Frontend test:** `frontend/e2e/cve-discovery-patch-size-validation.spec.ts` (NEW, 6 tests)

> As a security researcher using the CVE Discovery search tab, when
> I enter an invalid patch-size range (negative min, zero max, or
> min > max), I want a clear validation error rather than getting
> an empty results table that looks like "no CVEs match".

**The bug**: `DiscoverCVEsRequest.patch_size_min/max` and
`max_results` were `int` with no bounds. A negative min, a zero max,
or an inverted range (min=500, max=10) all passed schema validation,
were forwarded to `CVESelectionCriteria` whose filter is
`lines_changed > max OR lines_changed < min` — so an inverted range
silently rejected every candidate. The user saw "0 candidates"
empty-state copy that suggested CVE feeds had no matches, when in
fact their query was malformed. `max_results` was also unbounded.

**The fix**: at the top of `Coordinator.discover_cves`, raise
`HTTPException(400, detail=...)` for `patch_size_min<0`,
`patch_size_max<1`, `patch_size_min>patch_size_max`, `max_results<1`,
and `max_results>500`. Each detail string names the offending field
so the existing apiFetch ApiError chain surfaces it through to the
existing `searchError` UI (now with `role="alert"` for screen
readers). HTML5 `min={0}` / `min={1}` on the patch-size inputs is
defense-in-depth — UI users can't even type negatives — but the
backend validates regardless.

**Test gotcha**: the negative-min and zero-max e2e tests strip the
HTML5 `min` attribute via `el.removeAttribute('min')` before
filling, so the form actually POSTs and exercises the backend
guard. The inverted-range test doesn't need this trick because
HTML5 has no min<=max constraint. `getByRole('alert')` is scoped
with `.filter({ hasText: 'patch_size' })` because two unrelated
import-error alerts share the role on this page.

**Result**: 12/12 e2e tests pass on chromium + firefox; 6 new backend
tests + 7 pre-existing all pass. 110/110 in the broader CVE Discovery
e2e suite — no regression.

---

### 56. Feedback page clears stale data on reload failure + surfaces FP errors

**Frontend fix:** `frontend/src/pages/Feedback.tsx`
**Frontend test:** `frontend/e2e/feedback-error-clearing.spec.ts` (NEW, 3 tests)
**Frontend test (updated):** `frontend/e2e/feedback-extended.spec.ts`
(FP-error test now asserts the corrected behavior)

> As an analyst on the Feedback page, when a Load Trends, Compare,
> or Load Patterns call fails after a previous successful load,
> I want stale data cleared so I don't misread the old chart as
> fresh — and I want a visible error, not a silent swallow that
> reads as "no results".

**The bug**: three sibling handlers had the same UX defect:
- `handleLoadTrends` and `handleCompare` only set their `error`
  state on failure but left the previous successful payload in place.
  After a reload failure the user saw both the chart from a prior
  load AND the error message — easy to misread.
- `handleLoadFP` had a bare `catch {}` that silently swallowed the
  error and set `fpPatterns = []`. The empty array then triggered
  the "No FP patterns found." copy — the same string a successful
  zero-result query produces — leaving the user no way to tell the
  service was down.

**The fix**:
- `handleLoadTrends` catch: also `setTrendsData(null)`.
- `handleCompare` catch: also `setComparison(null)`.
- `handleLoadFP`: capture `err`, set new `fpError` state, render it
  in a `<p role="alert">`, gate "No FP patterns found." on `!fpError`.
- All three error `<p>`s now have `role="alert"` for screen-reader
  announce.
- Updated the pre-existing `feedback-extended.spec.ts:140` test that
  had asserted the buggy contract (expecting "No FP patterns found."
  on 500) to assert the alert-visible / no-misleading-line contract.

**Result**: 3 new e2e tests pass on chromium + firefox; 132/132
in the full feedback suite — including the corrected pre-existing
test.

---

## Benchmark expansion stories

A second batch of stories driven by the multi-source ground-truth corpus
expansion (Phases 1–7: BenchmarkPython/Java, CVEfixes, CrossVul, SARD,
Bandit functional, Big-Vul, CodeQL test suites, MITRE Demonstrative
Examples). These stories cover the new surfaces those importers added —
multi-language datasets, `kind='archive'` content-addressed datasets,
paired-polarity ground truth via `dataset_negative_labels`, the
`language_allowlist` and `allow_benchmark_iteration` matrix gates, and
the benchmark scorecard the worker computes against negative labels.
Listed in the order they will be implemented.

### 57. Datasets list surfaces benchmark cards with multi-language tags

**Spec:** `frontend/e2e/benchmark-datasets-listing.spec.ts` (NEW)

> As a security researcher landing on the Datasets page, I want benchmark
> corpora (BenchmarkPython, BenchmarkJava, SARD-by-language, MITRE
> Demonstrative Examples) to render alongside CVE/injected datasets with
> their language tags and label counts visible — so I can pick a dataset
> whose language matches the strategy I'm about to run without drilling
> into each row first.

Covers a fixture that adds five benchmark rows (one each: BenchmarkPython,
BenchmarkJava, SARD-c, MITRE-CWE archive, Big-Vul). Asserts the row
renders the language pills (one per element of `languages[]`), the
`label_count`, and that clicking a benchmark row navigates to its
detail page. Uses `mockApi(page)` plus a per-test override of
`/api/datasets` returning the expanded list.

### 58. Archive-kind dataset detail surfaces archive origin

**Spec:** `frontend/e2e/benchmark-archive-origin.spec.ts` (NEW)
**Frontend:** `frontend/src/pages/DatasetDetail.tsx` extension to handle
`kind === 'archive'` (currently a kind-mismatch falls into the
"Derived from" branch and shows nothing useful).

> As a security researcher viewing a benchmark dataset that ships as a
> downloadable archive (MITRE Demonstrative Examples, SARD), I want the
> origin card to show the archive URL, the sha256 digest, and the format
> (tar.gz / zip) — so I can independently verify the bytes I'm about to
> review and cite the exact source in a write-up.

Covers a dataset row with `kind: 'archive'` plus the archive URL, sha256,
and format from `metadata`. Asserts the origin card heading reads
"Archive origin" (not "Derived from"), and that all three fields appear
with the sha256 truncated + copy button identical to the git-commit
treatment. Verifies a missing sha256 still renders gracefully.

### 59. Language allowlist server-side gates mismatched-language datasets

**Spec:** `tests/integration/test_language_allowlist_endpoint.py` (NEW)

> As a researcher running an experiment with `language_allowlist=['python']`,
> I want the coordinator to reject the submission when the chosen dataset's
> declared language is `java` — so I never burn LLM budget on a
> language-mismatched dispatch that the worker would just refuse anyway.

Covers three cases via the FastAPI test client: (a) allowlist is empty →
submission accepted regardless of dataset language; (b) allowlist mismatches
dataset's `metadata_json.language` → 400 with a message naming both the
dataset language and the allowlist; (c) dataset has no `metadata_json.language`
→ submission accepted with a logged warning (backward-compat path).

### 60. Per-test-file iteration mode requires explicit cost-gate flag

**Spec:** `tests/integration/test_per_test_file_iteration_endpoint.py` (NEW)

> As a researcher about to run BenchmarkPython (which contains thousands
> of test files), I want the coordinator to refuse a fan-out submission
> unless I explicitly opt in via `allow_benchmark_iteration=true` — so I
> can't accidentally trigger an N-times cost amplifier just by selecting
> a per-file benchmark.

Covers three cases: (a) dataset declares `iteration: per-test-file` and the
matrix omits the flag → 400 mentioning `allow_benchmark_iteration`;
(b) flag set + dataset materialized → submission expands runs to N
(one per matched file); (c) flag set + dataset NOT materialized → 400
mentioning materialization. Uses a tiny synthetic dataset (3 test files)
to keep the fan-out cheap.

### 61. Benchmark scorecard surfaces TN/FP from negative labels

**Spec:** `tests/integration/test_paired_polarity_scorecard.py` (NEW)

> As a researcher running a paired-polarity benchmark (BenchmarkPython,
> BenchmarkJava, Big-Vul), I want the worker's scorecard to include
> true-negatives and false-positives drawn from `dataset_negative_labels`
> alongside the TPs/FNs from `dataset_labels` — so I get an honest
> precision/recall/FP-rate readout instead of a positives-only
> approximation.

Covers a synthetic dataset seeded with 30 positive labels (CWE-89 SQLi)
and 30 negative labels (same CWE), runs the scoring via
`compute_benchmark_scorecard`, and asserts: TP+FN = 30, TN+FP = 30,
the per-CWE row appears, and the aggregate `owasp_score = tpr - fpr`
matches the expected formula. Also asserts the n<25 warning fires for
a CWE with only 10 positives + 10 negatives.

### 62. Live worker run produces benchmark scorecard against local provider

**Spec:** `tests/e2e/test_live_benchmark_run.py` (NEW)

> As a researcher pointing the framework at my local OpenAI-compatible
> server (e.g. llama.cpp at `http://192.168.7.100:8080`), I want a small
> end-to-end run on a benchmark-shaped dataset (positive + negative
> labels, single CWE) to actually produce a benchmark scorecard with
> populated TP/FP/TN/FN counts — so I can validate the full pipeline
> before committing to a multi-thousand-file corpus.

Skipped unless `LIVE_TEST_API_BASE` and `LIVE_TEST_MODEL_ID` are set
(default config in CI: `LIVE_TEST_API_BASE=http://192.168.7.100:8080/v1`).
Builds a 4-file synthetic dataset (2 positive, 2 negative) with a single
CWE, dispatches a single-strategy run via `ExperimentWorker`, and asserts
the produced `RunResult` carries a non-empty `benchmark_scorecards` list
with at least one CWE row. Tolerant of TP=0 (model may miss) but strict
on the schema and that TN+FP+FN+TP equals the file count.

### 63. Live worker run with `with_tools` variant exercises read_file in tool_calls.jsonl

**Spec:** `tests/e2e/test_live_with_tools_run.py` (NEW)

> As a security researcher pointing the framework at my local OpenAI-compatible
> server (e.g. llama.cpp at `http://192.168.7.100:8080`), I want a live
> end-to-end run with `tool_variant=WITH_TOOLS` to actually offer the
> read-file/grep tools to the model AND record any invocations to
> `tool_calls.jsonl` — so I can validate the tool-calling round-trip works
> against my local provider before committing to a multi-strategy
> experiment that depends on tool access.

Skipped unless `LIVE_TEST_API_BASE` and `LIVE_TEST_MODEL_ID` are set. Builds a
2-file synthetic dataset with a CWE-89 vulnerability in `helpers.py` and a thin
entry-point in `app.py` that hints the model to inspect helpers; dispatches a
single-strategy run via `ExperimentWorker` with `WITH_TOOLS`. Asserts the
WITH_TOOLS plumbing — tools are registered, the model invokes them
(`tool_call_count ≥ 1` required), and the round-trip is captured in both
`tool_calls.jsonl` (each entry schema-valid with a registered `tool_name`) and
`conversation.jsonl` (must contain ≥1 assistant message with a `tool_calls`
block AND ≥1 `role==tool` result entry). The test is independent of whether
pydantic-ai's structured-output parse succeeds: small models often fail that
final step against the framework's typed-Findings schema, so a `FAILED` status
is acceptable provided the error contains `"output validation"` — any other
failure mode is still caught and fails the test.

---

### 64. RunDetail surfaces the worker's error message when status is failed

**Spec:** `frontend/e2e/run-detail-error-banner.spec.ts` (NEW)
**Frontend fix:** `frontend/src/api/client.ts` (`Run` type + new `error` field),
`frontend/src/pages/RunDetail.tsx` (error banner)

> As a security researcher whose run failed (e.g. because the local model
> couldn't produce parseable structured output, the LLM provider returned
> a 4xx, or the strategy crashed), I want the run-detail page to show the
> exact error message — currently I see a red 'failed' badge with no
> explanation, and have to dig into the API response or run_result.json
> to find out what went wrong.

Covers five Playwright assertions: (1) the `data-testid="run-error-banner"` element is visible for a failed run and contains both the "Run error" heading text and the full error string; (2) the banner carries `role="alert"` for screen-reader accessibility; (3) the banner is absent when the run status is `completed` and `error` is null; (4) the existing "failed" status badge still renders alongside the banner — confirming the banner is additive, not a replacement; and (5) an error string containing a literal newline character is rendered with `whitespace-pre-wrap` so the line break is preserved in the browser's inner text.

---

### 65. MatrixTable surfaces failed-run status pill and error in the expand row

**Spec:** `frontend/e2e/matrix-table-failed-runs.spec.ts` (NEW)
**Frontend fix:** `frontend/src/components/MatrixTable.tsx` (status pill on
non-completed rows + status + error rows in expanded detail)

> As a security researcher scanning an experiment matrix that contains a
> mix of completed and failed runs, I want failed/cancelled runs to be
> visually distinct from completed-with-low-metrics runs — currently every
> failed run renders identically to a row whose metrics happened to be 0
> (all metric cells show '—'), so I can't tell whether the strategy
> performed badly or never ran. I also want the run.error to appear in
> the expand row so I don't have to drill into the run detail page just
> to triage which failures are framework bugs vs. genuine model failures.

Covers five Playwright assertions: (1) the failed run's table row contains a `data-testid="matrix-row-status-pill"` element with the text "failed" visible in the expand-toggle cell; (2) the cancelled run's row contains the same pill testid with text "cancelled"; (3) completed-run rows contain no status pill at all (`toHaveCount(0)`); (4) clicking the expand toggle on the failed run reveals a `data-testid="matrix-row-error"` block containing the full verbatim error string from the fixture; and (5) clicking the expand toggle on a completed run leaves the error testid absent (`toHaveCount(0)`).

---

### 66. MatrixFilterBar adds a Status dimension so users can isolate failed runs

**Spec:** `frontend/e2e/matrix-filter-status.spec.ts` (NEW)
**Frontend fix:** `frontend/src/lib/matrixFilter.ts`,
`frontend/src/components/MatrixFilterBar.tsx`,
`frontend/src/__tests__/lib/matrixFilter.test.ts`

> As a security researcher with a 40-run matrix that contains a mix of
> completed and failed runs, I want to filter the matrix down to only
> the failed runs (or only the cancelled runs) — currently the matrix
> filter offers Model, Strategy, Tools, Extensions, and Profile but
> not Status, so I can't isolate the failures Story 65's pill made
> visible.

Covers four Playwright assertions: (1) the Status filter button appears in the `MatrixFilterBar` when the runs fixture contains a mix of completed, failed, and cancelled runs; (2) the Status filter button is absent when all runs share the same status (single-option dimensions are hidden by the existing `allDims.filter` guard); (3) selecting "failed" from the Status popover filters the table so only the `few_shot` (failed) row remains and the `zero_shot` (completed) and `agent` (cancelled) rows are gone; and (4) after applying the status=failed filter the page URL contains `status=failed`, confirming the filter is serialized to the address bar.

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
