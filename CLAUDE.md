@AGENTS.md

## Tool Extensions (Chunks 1-7)

The framework has a pluggable tool extensions system: optional MCP-backed tools (TREE_SITTER, LSP, DEVDOCS) that run as subprocesses within each worker pod. Extensions are:
- Controlled via `helm/sec-review/values.yaml` (`workerTools.{treeSitter,lsp,devdocs}.enabled`).
- Tracked in `ExperimentRun.tool_extensions: frozenset[ToolExtension]` and the `runs.tool_extensions` DB column.
- Registered via `register_extension_builder()` in `tools/extensions/{ext_name}_ext.py`.
- Exposed to the frontend via `GET /api/tool-extensions` and shown in the matrix UI.
- Run IDs gain `_ext-<sorted>` suffix for non-empty extension sets; legacy empty-extension runs stay byte-identical.

See ARCHITECTURE.md § 18 for design details, README.md "Tool Extensions Configuration" for Helm setup.
