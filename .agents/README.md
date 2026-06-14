# graphify Agent Instructions

Start here for any agent working on this repository.

- Canonical project spec: `project-spec.md`
- ast-index integration: `integrations/ast-index.md`
- caveman integration: `integrations/caveman.md`
- repo-local skills: `skills/`
- repo-local subagent presets: `agents/`
- generic `.agents` rules: `rules/`
- upstream source attribution: `vendor/SOURCES.md`
- Root bootstrap for Codex and other agents: `../AGENTS.md`
- Claude Code adapter: `../CLAUDE.md`
- CodeBuddy adapter: `../CODEBUDDY.md`
- Codex adapter: `../.codex/AGENTS.md`
- Cursor adapters: `../.cursor/rules/`
- Cline adapters: `../.clinerules/`
- OpenCode adapter: `../.opencode/AGENTS.md`
- Windsurf adapters: `../.windsurf/rules/`
- Gemini adapter: `../GEMINI.md`
- Copilot adapter: `../.github/copilot-instructions.md`

Rules:
- Keep substantive project guidance in `project-spec.md`.
- Keep integration-specific policy in `integrations/`.
- For source-level code search, use `skills/codebase-search/SKILL.md`; it
  actively routes agents to ast-index before broad text search.
- Keep platform-specific files thin; they should route agents back here instead of duplicating policy.
- Do not create or update `graphify-out/` unless the user explicitly asks, or unless an existing graph is being maintained after code changes.
- Do not run broad upstream installers that write global Claude, Codex, or IDE config unless the user explicitly asks for a global install.
