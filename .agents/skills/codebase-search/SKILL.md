---
name: codebase-search
description: Use this skill for any source-level codebase navigation, code search, refactor impact check, debugging investigation, code review context lookup, "where is X", "what calls Y", "find usages", "find file", "project structure", or agent/subagent code investigation. Prefer ast-index before rg, grep, or host Search/Grep whenever an ast-index index is available or can be built.
---

# Codebase Search

This is the repo-local active-search policy. It exists so agents do not wait for
an explicit "use ast-index" request before using the structural index.

Read `.agents/integrations/ast-index.md` for integration policy and
`.agents/skills/ast-index/SKILL.md` for the full command reference when needed.

## Required Workflow

1. Run `ast-index stats` before the first structural lookup in a checkout.
2. If the index is missing and the task needs source navigation, run
   `ast-index rebuild` from the repository root.
3. After pull/rebase or meaningful source edits, run `ast-index update` before
   trusting previous search results.
4. Use ast-index before broad raw text tools for source-level lookup:
   - files: `ast-index file <name>` or `ast-index search <term>`
   - symbols: `ast-index symbol <name>`, `ast-index class <name>`
   - references: `ast-index refs <name>`, `ast-index usages <name>`
   - callers and inheritance: `ast-index callers <name>`, `ast-index implementations <name>`
   - file shape: `ast-index outline <path>`, `ast-index imports <path>`
   - project shape: `ast-index map`, `ast-index conventions`
5. Use `rg`, grep, or host Search/Grep only for regex, string literals,
   comments, non-code files, or empty ast-index results.
6. Do not run `rg` "for completeness" after ast-index returns useful
   structural results.

## Graphify-Specific Routing

If `graphify-out/graph.json` exists and the question is architectural or
relationship-heavy, use `graphify query`, `graphify path`, or `graphify explain`
before raw-file browsing.

If no graph exists, use ast-index for source-level navigation and `rg` only for
the fallback cases above.

The ast-index database is a local cache outside the repo in normal operation.
Do not commit accidental repo-local index databases.
