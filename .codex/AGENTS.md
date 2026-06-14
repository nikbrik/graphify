# Codex Adapter

Codex loads the repository root `AGENTS.md` for this project. The canonical
agent development spec is `../.agents/project-spec.md`.

Additional repo-local integrations:
- `../.agents/integrations/ast-index.md`
- `../.agents/skills/codebase-search/SKILL.md`
- `../.agents/integrations/caveman.md`
- `../.agents/skills/`

Do not duplicate project policy here. Keep this file as a thin Codex-specific
pointer so `.agents/` remains reusable by other agents.

Do not mirror repo-local skills into `.codex/skills/`; Codex should read the
canonical `.agents/skills/` payloads.

No `.codex/hooks.json` is required for this repository integration.
