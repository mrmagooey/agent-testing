# Agents

## Delegation policy

Any significant work should be completed by a team of subagents managed by
subagents — not done single-threaded in the primary agent. Decompose the task,
spawn specialized subagents for each sub-task, and let a coordinating subagent
fan out to leaf workers. Parallelize independent branches.

Match model complexity to scope. As the scope of a sub-task decreases, the
model running it should get cheaper:

- **Opus** — whole-project refactors, architectural decisions, multi-file
  investigations where judgment about unknowns matters.
- **Sonnet** — scoped feature work, single-file implementations, code review,
  non-trivial bug fixes.
- **Haiku** — mechanical edits, lookups, single-function rewrites, formatting
  passes, log grepping, anything where the correct answer is obvious once the
  inputs are in hand.

A leaf subagent should almost never run the same model as the coordinator that
spawned it. If it does, that is a signal the decomposition is too shallow —
the coordinator is just forwarding, not dividing the work.

## Concurrent agents

Multiple agents may be working in this repository at the same time. To avoid
stepping on each other's changes, prefer running work in a git worktree
rather than mutating the shared checkout directly. Spawn subagents with
`isolation: "worktree"` when their work touches the filesystem, and treat
the primary working directory as shared state that must not be left in a
half-modified condition. When a worktree's work is done, merge or discard
it deliberately — do not leave stale branches lying around.

## Test coverage

Any change must be covered by tests. New behavior needs new tests; modified
behavior needs updated tests; bug fixes need a regression test that fails
without the fix. Do not report work as complete until the relevant tests
exist and pass. If a change is genuinely untestable (e.g. pure formatting),
say so explicitly rather than skipping silently.

When writing tests, check for excessive memory usage. Any test whose peak
resident memory exceeds 2 GB should be treated as a defect: either shrink
the fixture, stream the data, or split the test. Measure before landing —
do not assume a test is cheap because it looks small.

## Code review

Any significant work must be reviewed by a separate subagent specialized in
code review before it is reported as complete. The review agent must not be
the same agent that wrote the code — independence is the point. The reviewer
reads the diff, checks for correctness, security issues, unintended scope,
and adherence to surrounding conventions, and returns a pass/fail verdict
with specific line-level findings. Address findings before shipping.
