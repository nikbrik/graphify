# Copilot Adapter

Canonical project spec: `.agents/project-spec.md`

Integrations:
- `.agents/integrations/ast-index.md`
- `.agents/integrations/caveman.md`

Use ast-index before broad raw text search for source-level navigation when an
index is available. For Russian-language prompts, keep answers Russian and terse
while preserving code, paths, commands, URLs, identifiers, and exact errors.

Do not run broad upstream installers or write global Claude config unless the
user explicitly asks.
