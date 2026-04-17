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

## Code review

Any significant work must be reviewed by a separate subagent specialized in
code review before it is reported as complete. The review agent must not be
the same agent that wrote the code — independence is the point. The reviewer
reads the diff, checks for correctness, security issues, unintended scope,
and adherence to surrounding conventions, and returns a pass/fail verdict
with specific line-level findings. Address findings before shipping.
