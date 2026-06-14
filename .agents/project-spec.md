# graphify Project Spec

This file is the source of truth for agent work on the graphify repository.
Platform-specific agent files should link here instead of duplicating these rules.

## Project Purpose

graphify turns a code, document, media, or repository corpus into a persistent
knowledge graph that agents can query instead of repeatedly scanning raw files.
It ships as the `graphifyy` Python package and exposes the `graphify` CLI.

The project supports multiple agent hosts, including Claude Code, Codex,
OpenCode, Kilo Code, Cursor, Gemini CLI, Copilot, Aider, OpenClaw, Factory Droid,
Trae, Hermes, Kiro, Pi, Devin CLI, and Google Antigravity.

## Architecture

Use `ARCHITECTURE.md` for the high-level module map. The core pipeline is:

```text
detect() -> extract() -> build_graph() -> cluster() -> analyze() -> report() -> export()
```

Important subsystems:
- Corpus discovery and ignore handling: `graphify/detect.py`, `graphify/manifest.py`
- AST and semantic extraction: `graphify/extract.py`, `graphify/llm.py`
- Graph construction, clustering, analysis, and reporting: `graphify/build.py`, `graphify/cluster.py`, `graphify/analyze.py`, `graphify/report.py`
- Querying and serving graph data: `graphify/serve.py`, `graphify/global_graph.py`, `graphify/querylog.py`
- Exports and generated views: `graphify/export.py`, `graphify/wiki.py`, `graphify/callflow_html.py`, `graphify/tree_html.py`
- Host install and CLI orchestration: `graphify/__main__.py`, `graphify/hooks.py`
- Skill generation source: `tools/skillgen/fragments/`, `tools/skillgen/platforms.toml`, `tools/skillgen/gen.py`

## Agent Integrations

Repo-local agent integrations live under `.agents/`:

- ast-index structural search: `.agents/integrations/ast-index.md`
- caveman terse communication and cavecrew presets: `.agents/integrations/caveman.md`
- canonical skills: `.agents/skills/`
- subagent presets: `.agents/agents/`

Platform-specific files must remain thin adapters back to `.agents`. Do not run
upstream installers that write global agent config unless the user explicitly
asks for a global install.

## Development Workflow

Use `uv` for local development:

```bash
uv sync --all-extras
uv run graphify --version
uv run python -c "import graphify; print(graphify.__file__)"
```

Run tests with:

```bash
uv run pytest tests/ -q
uv run pytest tests/<file>.py -q
uv run pytest tests/ -q -k "<filter>"
```

Before opening a PR or after broad changes, match CI where relevant:

```bash
uv run --frozen pytest tests/ -q --tb=short
uv run --frozen python -m tools.skillgen --check
uv run --frozen python -m tools.skillgen --audit-coverage
uv run --frozen python -m tools.skillgen --schema-singleton
uv run --frozen python -m tools.skillgen --monolith-roundtrip
uv run --frozen python -m tools.skillgen --always-on-roundtrip
```

Security scans in CI are currently non-blocking but useful for security-sensitive
changes:

```bash
uv run --frozen bandit -r graphify -ll
uv run --frozen pip-audit --strict
```

## Generated Artifacts

Do not hand-edit generated skill artifacts:
- `graphify/skill*.md`
- `graphify/skills/**/references/**`
- `graphify/always_on/**`
- `tools/skillgen/expected/**`

For skill behavior changes, edit the sources under `tools/skillgen/fragments/**`
or `tools/skillgen/platforms.toml`, then regenerate and validate with:

```bash
uv run python -m tools.skillgen
uv run python -m tools.skillgen --check
```

Use `uv run python -m tools.skillgen --bless` only when expected artifacts must
be intentionally updated.

## Graph Policy

This checkout does not assume a local `graphify-out/` exists.

If `graphify-out/graph.json` exists:
- Use `graphify query "<question>"` for codebase questions before broad raw-file searches.
- Use `graphify path "<A>" "<B>"` for relationship traces.
- Use `graphify explain "<concept>"` for focused concept lookups.
- If `graphify-out/wiki/index.md` exists, use it for broad navigation.

If `graphify-out/graph.json` does not exist:
- Do not read `graphify-out/GRAPH_REPORT.md`; it may not exist.
- Do not run `graphify update .` just because files changed.
- Use `README.md`, `ARCHITECTURE.md`, `pyproject.toml`, `.github/workflows/ci.yml`, tests, and `rg` for orientation.

Create or update `graphify-out/` only when the user explicitly asks, or when an
existing graph is being maintained after code changes.

## Code Change Policy

Keep edits scoped to the requested behavior and existing module boundaries.

After changing Python code, packaging metadata, CLI behavior, generated-source
inputs, or tests, run the narrowest relevant test first. Broaden to the CI-level
commands above when the change affects shared behavior, packaging, install flows,
skill generation, security boundaries, or graph output contracts.

When changing language extraction:
- Add or update fixtures under `tests/fixtures/`.
- Add focused tests near the relevant extractor coverage, commonly
  `tests/test_languages.py`, `tests/test_multilang.py`, or a language-specific
  test file.
- Keep node IDs deterministic and aligned with existing schema tests.

When changing install or host integration behavior:
- Cover both user-scope and project-scope behavior when applicable.
- Check install round-trip and generated string tests when skill or always-on
  text changes.
