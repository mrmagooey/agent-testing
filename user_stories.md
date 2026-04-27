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

> As a security researcher, I want to pick two runs from different
> experiments via the global `/compare` picker and see the side-by-side
> finding diff — so I can compare strategies across experiments without
> manually copying URLs.

`global-compare.spec.ts` has 3 tests for picker mechanics; the actual
side-by-side comparison rendering and the cross-experiment POST body
shape (`compareRunsCross`) are not exercised.

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

> As a security researcher, I want to drop a `.secrev.zip` bundle onto the
> dropzone and have it select for upload — so I don't have to click
> through a file picker.

Iteration 3 chose `setInputFiles` for reliability. The actual drag-drop
DataTransfer path is untested. Lower priority because drag-drop is
inherently flaky in headless browsers and the UX path through the
file-picker is already covered.

### J. ExperimentNew "estimate" preview

> As a security researcher building a new experiment, I want to see the
> projected total runs and cost-USD update live as I tune the model /
> strategy / repetitions controls — so I don't accidentally configure a
> wildly expensive matrix.

`POST /experiments/estimate` is mocked in `mockApi.ts` and called by
`ExperimentNew`. Some piece of this is exercised by `experiment-new-extended.spec.ts`,
but those tests have shown flake (30s timeouts in iteration 3's
unrelated full-suite run). A focused pass on the estimate display
contract — not the form-submission path — would be useful.

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
