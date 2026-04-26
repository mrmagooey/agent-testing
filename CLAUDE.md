@AGENTS.md

## Tool Extensions (Chunks 1-7)

The framework has a pluggable tool extensions system: optional tools (DEVDOCS, LSP, SEMGREP, TREE_SITTER) that are activated per-run via `tool_extensions`. Extensions are:
- Controlled via `helm/sec-review/values.yaml` (`workerTools.{devdocs,lsp,semgrep,treeSitter}.enabled`).
- Tracked in `ExperimentRun.tool_extensions: frozenset[ToolExtension]` and the `runs.tool_extensions` DB column.
- Registered via `register_extension_builder()` in `tools/extensions/{ext_name}_ext.py`.
- Exposed to the frontend via `GET /api/tool-extensions` and shown in the matrix UI.
- Run IDs gain `_ext-<sorted>` suffix for non-empty extension sets; legacy empty-extension runs stay byte-identical.

The SEMGREP extension differs from MCP-backed extensions: it runs the `semgrep` binary in-process (no subprocess MCP server). The binary is baked into the worker image via pipx (see `Dockerfile.worker` `ARG SEMGREP_VERSION`); the Python package is excluded from the worker venv due to a dependency conflict with pydantic-ai.

See ARCHITECTURE.md § 18 for design details, README.md "Tool Extensions Configuration" for Helm setup.
