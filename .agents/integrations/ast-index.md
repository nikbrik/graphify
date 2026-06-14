# ast-index Integration

Source: `defendend/Claude-ast-index-search` at `97437e0cc79d4c430177f140daebb608df382030`.

## Purpose

Use ast-index for structural code navigation before broad text search. It is a
fast symbol/file/usage/outline/project-map index that complements graphify:

- graphify: durable knowledge graph and cross-file semantic relationships
- ast-index: fast local structural lookup while editing code
- `rg`: raw text, regex, comments, literals, and fallback when ast-index has no useful hit

## Required Agent Behavior

For codebase navigation tasks, prefer this order:

1. If `graphify-out/graph.json` exists and the question is architectural or relationship-heavy, use `graphify query/path/explain`.
2. For source-level lookup, use ast-index before broad `rg`: `search`, `symbol`, `class`, `usages`, `refs`, `outline`, `map`.
3. Use `rg` for regex, string literals, comments, non-code files, or empty ast-index results.

Before first ast-index query in a checkout:

```bash
ast-index stats
```

If no usable index exists and source navigation needs it:

```bash
ast-index rebuild
```

After pull/rebase or meaningful source edits:

```bash
ast-index update
```

Do not install the upstream Claude plugin in this repo. The repo-local canonical
skill lives at `.agents/skills/ast-index/SKILL.md`.

## Agent Adapters

- Codex: `.codex/skills/ast-index/SKILL.md` routes to the canonical skill.
- Cursor: `.cursor/rules/ast-index.mdc` gives always-on structural-search guidance.
- Cline/OpenCode/Windsurf/Gemini/Copilot: their adapter files route to this spec.

## Local Tooling Note

If `ast-index-mcp` is configured by a user's agent, ensure it points at this
repository root before trusting results. Do not commit user-specific absolute
MCP paths into the repo.
