"""Optional tool-extension plug-ins for the security review framework.

Each sub-module in this package:
  - Implements a builder function matching ``Callable[[ToolRegistry, Target], None]``.
  - Calls ``register_extension_builder(ToolExtension.<NAME>, builder)`` at import time.

Supported extensions (Chunk 3+):
  - tree_sitter_ext  — ``ToolExtension.TREE_SITTER``  (AST analysis via tree-sitter MCP)
"""
