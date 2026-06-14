## graphify

Agent development instructions for this repository live in `.agents/project-spec.md`.
Read that file before making architecture, codebase, or workflow decisions.

Rules:
- Treat `.agents/project-spec.md` as the source of truth for project-specific agent guidance.
- If `graphify-out/graph.json` exists, use `graphify query`, `graphify path`, or `graphify explain` before broad raw-file searches for codebase questions.
- If `graphify-out/wiki/index.md` exists, use it for broad navigation instead of raw source browsing.
- If `graphify-out/graph.json` does not exist, do not try to read `graphify-out/GRAPH_REPORT.md`; use `README.md`, `ARCHITECTURE.md`, `pyproject.toml`, CI config, and `rg`.
- After modifying code files, run relevant tests. Run `graphify update .` only when `graphify-out/graph.json` already exists or the user explicitly asks to create/update the graph.
