# Repo-Local Skills

Canonical skill payloads for this repository.

- `ast-index/`: structural code search from `defendend/Claude-ast-index-search`
- `caveman/`: terse communication mode
- `caveman-commit/`: terse Conventional Commit messages
- `caveman-review/`: terse review comments
- `caveman-compress/`: memory-file compression, adapted for multi-agent use
- `caveman-help/`: quick reference
- `caveman-stats/`: Claude-hook stats compatibility only
- `cavecrew/`: terse subagent delegation guide

Platform-specific adapters should point here and to `.agents/integrations/`.
Do not run broad upstream installers from these skills unless the user
explicitly asks for global host integration.
