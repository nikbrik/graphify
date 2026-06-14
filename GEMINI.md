# Gemini Adapter

Canonical project spec: `.agents/project-spec.md`

Integrations:
- `.agents/integrations/ast-index.md`
- `.agents/integrations/caveman.md`

Keep this file as a thin adapter. Use `.agents/skills/` as the repo-local skill
source. Do not run broad upstream installers or write global Claude config unless
the user explicitly asks.
