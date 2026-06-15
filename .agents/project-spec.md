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
- LLM HTTP rate-limit retry policy: `graphify/rate_limit.py` (429/503; used by `llm.py`)
- Graph construction, clustering, analysis, and reporting: `graphify/build.py`, `graphify/cluster.py`, `graphify/analyze.py`, `graphify/report.py`
- Querying and serving graph data: `graphify/serve.py`, `graphify/global_graph.py`, `graphify/querylog.py`
- Exports and generated views: `graphify/export.py`, `graphify/wiki.py`, `graphify/callflow_html.py`, `graphify/tree_html.py`
- Host install and CLI orchestration: `graphify/__main__.py`, `graphify/hooks.py`
- Skill generation source: `tools/skillgen/fragments/`, `tools/skillgen/platforms.toml`, `tools/skillgen/gen.py`

## Agent Integrations

Repo-local agent integrations live under `.agents/`:

- ast-index structural search: `.agents/integrations/ast-index.md`
- active code search trigger: `.agents/skills/codebase-search/SKILL.md`
- caveman terse communication and cavecrew presets: `.agents/integrations/caveman.md`
- canonical skills: `.agents/skills/`
- subagent presets: `.agents/agents/`

Platform-specific files must remain thin adapters back to `.agents`. Do not run
upstream installers that write global agent config unless the user explicitly
asks for a global install.

For source-level search, symbol lookup, usage tracing, refactor impact checks,
debugging investigations, and code review context lookup, use the
`codebase-search` skill. It requires ast-index before broad `rg`, grep, or host
Search/Grep when an index is available or can be built.

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

## LLM Backends

Semantic extraction go through `graphify/llm.py`. AST/code path no LLM. Headless
`graphify extract` pick backend via `--backend` or `detect_backend()` API-key
priority.

### HTTP rate-limit retry (429)

RPM/TPM → 429/503. Extract wait+retry, no drop chunk. Policy: **`graphify/rate_limit.py`**. Wire: **`graphify/llm.py`**. Docs: **`README.md`** env + OpenRouter. Tests: **`tests/test_rate_limit_retry.py`**, **`tests/test_llm_backends.py`**.

#### Two retry layers — never merge

| Layer | Module / fn | Trigger | Action |
|-------|-------------|---------|--------|
| **Adaptive bisection** | `_extract_with_adaptive_retry()` | `finish_reason=length`, context overflow, hollow 200 | Split chunk, recurse (`max_retry_depth`) |
| **HTTP rate-limit** | `call_with_rate_limit_retry()` | 429/503/502/504, `RateLimitError`, throttle, timeout | Sleep Retry-After/backoff, retry same HTTP req |

**Must:** 429 retry below adaptive, inside `_call_*` / SDK wrapper.
**Must not:** 429 in `_extract_with_adaptive_retry` — `test_adaptive_retry_re_raises_unrelated_errors` guard.
**Must not:** Double-wrap — `_create_chat_completion_with_format_fallback` already wraps; `_repair_openai_compat_json` call direct, no outer wrapper.

Call stack (OpenRouter extract):

```text
extract_corpus_parallel
  → _run_one [thread-local RateLimitContext chunk idx/total]
  → _extract_with_adaptive_retry          # truncation only
      → extract_files_direct
          → _call_openai_compat
              → _create_chat_completion_with_format_fallback
                  → call_with_rate_limit_retry → client.chat.completions.create
              → _repair_openai_compat_json (optional, same inner path)
```

Also: `_call_claude`, `_call_azure`, `_call_bedrock` wrap SDK; `claude-cli`/`codex-cli` subprocess — 429 in stderr **or** stdout → `SubprocessRateLimitError` → retry. `label`/`dedup`/`prs` via `_call_llm()` same retry; OpenAI-compat `_call_llm` need `timeout=_resolve_api_timeout()`.

#### Touch map

| What | Where |
|------|-------|
| Policy, gate, backoff, Retry-After | `graphify/rate_limit.py` |
| OpenAI-compat, format fallback, repair | `llm.py` `_create_chat_completion_with_format_fallback`, `_repair_openai_compat_json` |
| API backends | `llm.py` `_call_claude`, `_call_azure`, `_call_bedrock` |
| Subscription CLI | `llm.py` `_call_claude_cli`, `_call_codex_cli`, `_call_codex_cli_text`, `_call_llm` claude branch |
| Parallel extract + metrics | `llm.py` `extract_corpus_parallel` — stats reset, `set_rate_limit_context`, keys `rate_limit_*` |
| CLI → env | `graphify/__main__.py` extract ~4040 — `--rate-limit-*`, `--no-rate-limit-retry` |
| Unit / integration / regression | `tests/test_rate_limit_retry.py`, `tests/test_llm_backends.py`, `tests/test_chunking.py`, `tests/test_extract_cli.py`, `tests/test_charmap_encoding.py` |

OpenRouter/DeepSeek/Kimi/custom `providers.json` → `_call_openai_compat` — one path. No OpenRouter-only fork unless generic Retry-After + body `retry_after` fail.

#### Parallel (`--max-concurrency > 1`)

- **`_RateLimitGate`**: worker 429 → `signal_pause(delay)`; all `wait_if_paused()` before retry.
- **`_sleep_with_heartbeat`**: wait `max(local_deadline, gate_remaining)` — no early exit if peer extended gate.
- **`total_waited` / stats**: actual elapsed sleep, not planned `delay` — else `MAX_TOTAL_WAIT` wrong under gate extend.
- **`rate_limit_recovered_chunks`**: main thread after `future.result()` — not `nonlocal` in worker (race).
- Ollama/claude-cli/codex-cli serial default — gate still OK.

#### Retry policy

| Signal | Retry? |
|--------|--------|
| 429, 503, 502, 504, `RateLimitError`, Bedrock throttle | yes |
| `APITimeoutError`, timeout name/msg | yes |
| 400 incl context overflow | **no** → adaptive |
| 401, 403, 404 | no |
| `"502" in msg` | **no** — `\b502\b` only (not `5020`) |
| `x-ratelimit-reset-requests` header | **not** Retry-After seconds |

Backoff: header/body `retry_after`/`retryAfter` (cap per attempt); else exp+jitter. `_sdk_retryable()` isinstance before substring.

#### Config

| Var / flag | Default | Notes |
|------------|---------|-------|
| `GRAPHIFY_RATE_LIMIT_RETRY` | on | `0` / `--no-rate-limit-retry` off |
| `GRAPHIFY_RATE_LIMIT_MAX_WAIT` | 600 | one pause max; `--rate-limit-max-wait` |
| `GRAPHIFY_RATE_LIMIT_MAX_TOTAL_WAIT` | 3600 | per `call_with_rate_limit_retry` chain; `--rate-limit-max-total-wait` |
| `GRAPHIFY_RATE_LIMIT_MAX_RETRIES` | 25 | `--rate-limit-retries` |
| `GRAPHIFY_RATE_LIMIT_BACKOFF_BASE` | 2 | sec |
| `GRAPHIFY_RATE_LIMIT_BACKOFF_MULTIPLIER` | 2 | |
| `GRAPHIFY_API_TIMEOUT` | 600 | single HTTP attempt only — not inter-retry sleep |

OpenRouter doc-heavy: `--max-concurrency 2 --rate-limit-max-wait 600 --rate-limit-max-total-wait 3600`

#### UX / exit

- Stderr: `[graphify] {backend} rate limited (chunk i/n, attempt a/m); waiting Xs (Retry-After=…)`
- Heartbeat 60s if wait >120s
- Summary: `rate limit: N recovered, M retries, Ts waited; K dropped`
- `on_chunk_done`: success only
- Exit 1: all uncached chunks fail (`_chunk_stats["succeeded"]==0`)
- Partial drop: exit 0 + `failed_chunks` WARNING

#### Tests (rate-limit PR)

- [ ] `test_rate_limit_retry.py` — Retry-After, 400 no-retry, gate extend, actual wait budget, stdout 429
- [ ] `test_llm_backends.py` — 429→200, `extract_corpus_parallel` metrics
- [ ] Adaptive: `test_adaptive_retry_*`, `test_looks_like_context_exceeded_ignores_unrelated_errors`
- [ ] Mock `time.sleep` — no real long waits in CI

#### Common mistakes (429 post-mortem)

1. 429 in `_extract_with_adaptive_retry` — wrong layer
2. Double wrap repair around `_create_chat_completion_with_format_fallback` — 2× budget/stats
3. `recovered_chunks += 1` in pool worker — race
4. `x-ratelimit-reset-requests` as seconds — wrong (e.g. `6m0s`)
5. `"502" in str(exc)` — hits `5020`; word boundary
6. HTTP 400 + `"capacity"` msg → HTTP retry — 400 in `_NON_RETRYABLE_STATUS`
7. `_sleep_with_heartbeat` exit before gate clear — herd
8. `total_waited += delay` not actual — busts `MAX_TOTAL_WAIT`
9. Subprocess 429 stderr-only — check stdout too
10. `_call_llm` OpenAI no `timeout=` — hang label/dedup

#### Navigate fast (429)

1. `rg 'call_with_rate_limit_retry|rate_limit' graphify/ tests/`
2. `graphify/rate_limit.py`
3. `llm.py`: `_create_chat_completion_with_format_fallback`, `extract_corpus_parallel`, `_extract_with_adaptive_retry` (read-only), `extract_files_direct`, `_call_llm`
4. `__main__.py` extract env ~4180
5. `pytest tests/test_rate_limit_retry.py tests/test_llm_backends.py -q`

Not 429: format fallback one-shot drop `response_format`; repair one-shot malformed JSON.

### Backend kinds

| Kind | Examples | Auth | Auto-detect |
|------|----------|------|-------------|
| API key | `gemini`, `kimi`, `claude`, `openai`, `deepseek`, `azure`, `ollama`, `bedrock` | env API key / AWS / Ollama URL | yes (key order in `detect_backend()`) |
| Subscription CLI | `claude-cli`, `codex-cli` | host CLI + user subscription; no graphify API key | **no** — user must pass `--backend` explicitly |
| Custom OpenAI-compat | entries in `providers.json` | user API key | if key set |

**Do not confuse:**
- `codex-cli` backend = `codex exec` subprocess in `llm.py` (headless extract).
- `.codex/skills/graphify/` = Codex **platform skill** (host hooks/install); different subsystem.
- `--backend openai` = pay-as-you-go API key; not ChatGPT subscription.

### Canonical pattern: add subscription CLI backend

Copy `claude-cli` first. Reference files:

| What | Where |
|------|-------|
| Template impl | `graphify/llm.py` — `_resolve_claude_cli`, `_call_claude_cli`, `_call_claude_cli_text` (if any), `_resolve_claude_cli_model` |
| Codex impl | same file — `_resolve_codex_cli`, `_call_codex_cli`, `_call_codex_cli_text`, `_resolve_codex_cli_model` |
| Extraction dispatch | `extract_files_direct()` — branch before `_call_openai_compat` |
| Label/dedup/triage text | `_call_llm()` — branch before bedrock |
| Serial chunks | `extract_corpus_parallel()` — force `max_concurrency=1` unless env `=1` |
| Extract auth (no API key) | `graphify/__main__.py` extract path ~4300 — `try: _resolve_*_cli()` not bare `shutil.which()` |
| Triage fallback (optional) | `graphify/prs.py` `_resolve_triage_backend()` after API keys, after claude-cli |
| Tests | `tests/test_claude_cli_backend.py` → mirror as `tests/test_<name>_cli_backend.py` |
| UTF-8 subprocess | `tests/test_charmap_encoding.py` — extract + `_call_llm` |
| Vision argv | `tests/test_image_vision.py` — `-i` or Read-tool path |
| Docs | `README.md` env table + extract example + Privacy bullet |
| Changelog | `CHANGELOG.md` Unreleased feat line |
| Skillgen | `tools/skillgen/fragments/references/shared/github-and-merge.md` then `uv run python -m tools.skillgen --bless` |

**Every new subscription CLI — touch all whitelist sites in `llm.py`:**
1. `BACKENDS["<name>"]` — `default_model: "<name>-plan"` placeholder, `pricing: 0`, `vision: True/False`, optional `model_env_key`
2. `if not key and backend not in (...)` — **two** places: `extract_files_direct`, `_call_llm`
3. `detect_backend()` tail exclusion tuple — never auto-detect subscription CLI
4. `extract_corpus_parallel` serial guard + env `GRAPHIFY_<NAME>_CLI_PARALLEL=1`

### Windows CLI binary (#1072)

npm shim ship `.cmd` + `.ps1`. `CreateProcess` fail on `.ps1`. Always:
- `_resolve_*_cli()` prefer `shutil.which("<name>.cmd")` on win32, else bare `<name>`
- subprocess use resolved path; pass `**_no_window_kwargs()`
- auth gate call `_resolve_*_cli()` — not `which("codex")` alone

### Model resolution (subscription CLI)

Priority: `graphify extract --model X` → `GRAPHIFY_*_CLI_MODEL` env → omit CLI `-m` (host default).

Implement `_resolve_*_cli_model(explicit)`:
- skip placeholder `*-plan`
- read env via `BACKENDS[name].model_env_key` when present
- `extract_files_direct` pass `model=mdl` where `mdl = model or _default_model_for_backend(backend)`

Pass `--model` from extract to `_call_*_cli` — claude-cli historically missed this; codex-cli must not repeat.

### Vision / images

| Backend | Mechanism | Set membership |
|---------|-----------|----------------|
| `claude-cli` | Read tool + path in prompt + `--add-dir` | `_PATH_IMAGE_BACKENDS` |
| `codex-cli` | native `codex exec -i <abs path>` | `_CLI_IMAGE_PATH_BACKENDS` |
| API vision | inline base64 in SDK payload | neither set |

In `extract_files_direct`: `read_bytes = vision and backend not in _CLI_IMAGE_PATH_BACKENDS`.
Do **not** put codex-cli in `_PATH_IMAGE_BACKENDS` — avoids 5MB inline cap wrongly stripping `-i` paths.

### Codex-cli subprocess contract (agent CLI — not chat completion)

One semantic chunk = one `codex exec`:

```text
codex exec \
  --sandbox read-only \
  --skip-git-repo-check \
  --ephemeral \
  --json \
  --output-schema <tmp schema.json> \
  -o <tmp output.json> \
  -C <corpus root> \
  [-m <model>] \
  [-i <abs image>]... \
  "<_EXTRACTION_SYSTEM + _CODEX_CLI_AGENT_SUFFIX>"
stdin = _read_files() + _with_image_notes(..., with_paths=False)
```

Schema tmp file: `json.dumps(_GRAPHIFY_EXTRACTION_JSON_SCHEMA)` — delete in `finally`.

**Agent risk:** Codex may tool/shell instead of JSON. Mitigate layers:
1. `_CODEX_CLI_AGENT_SUFFIX` — forbid tools; stdin has corpus
2. `--output-schema` validates final response
3. `--sandbox read-only` default; `GRAPHIFY_CODEX_CLI_SANDBOX=bypass` → `--dangerously-bypass-approvals-and-sandbox` only if spike stuck on approvals

**Response handling:**
- Read `-o` file primary; not stdout prose
- If output JSON string wrapped in quotes → `json.loads` unwrap once
- `_parse_llm_json_result` — if `not ok` **or** `_response_is_hollow` → set `finish_reason="length"` → existing `_extract_with_adaptive_retry` bisect
- No API repair path (`_repair_openai_compat_json`) — bisect only
- Token usage: best-effort parse `--json` JSONL stdout; else `0`

Plain text (label/triage): `_call_codex_cli_text()` — same sandbox flags + `-o`, no schema; reused by `_call_llm` and `prs --triage`.

Env vars:

| Var | Default | Purpose |
|-----|---------|---------|
| `GRAPHIFY_CODEX_CLI_MODEL` | unset | `-m` override |
| `GRAPHIFY_CODEX_CLI_PARALLEL` | serial | `=1` allow parallel chunks |
| `GRAPHIFY_CODEX_CLI_SANDBOX` | `read-only` | `bypass` for headless stuck |
| `GRAPHIFY_API_TIMEOUT` | 600 | subprocess timeout (also `--api-timeout`) |

Claude-cli parallel env: `GRAPHIFY_CLAUDE_CLI_PARALLEL`. Model env: `GRAPHIFY_CLAUDE_CLI_MODEL` (no `model_env_key` in BACKENDS yet — env read inside `_call_claude_cli`).

### Claude-cli subprocess contract (reference)

```text
claude -p --output-format json --no-session-persistence \
  [--add-dir <img parent>]... \
  --system-prompt "<_extraction_system()>" \
  [--model <from _resolve_claude_cli_model>]
stdin = user corpus (+ Read-tool path notes if images)
```

Parse `_claude_cli_envelope(stdout)` → JSON `result` field → `_parse_llm_json`.

### Pre-merge spike (subscription CLI)

Before merge PR for new CLI backend:

```powershell
<cli> login status          # exit 0
graphify extract <2 docs> --backend <name> --no-cluster --force
graphify extract <8 docs> --backend <name> --no-cluster --token-budget 30000
```

Pass criteria: ≥90% chunks valid JSON without manual repair; no tool/shell loops in stderr/JSONL; `--sandbox read-only` enough (else document bypass env); record latency p50/p95 on ~10 chunks.

### Tests checklist (new subscription CLI)

- [ ] `tests/test_<name>_cli_backend.py` — happy path, tokens, hollow→length, invalid JSON→length, missing CLI, nonzero exit, missing `-o`, Windows `.cmd`, argv flags, serial guard, `extract_files_direct` dispatch, zero cost BACKENDS
- [ ] `tests/test_charmap_encoding.py` — `extract_files_direct` + `_call_llm` `encoding=utf-8`
- [ ] `tests/test_image_vision.py` if `vision: True`
- [ ] `tests/test_claude_cli_backend.py` regression unchanged
- [ ] `uv run python -m tools.skillgen --check` if backend mentioned in skillgen fragments

### MVP vs phase-2 (subscription CLI)

| MVP | Phase-2 optional |
|-----|------------------|
| `extract`, `_call_llm`, `__main__` auth, tests, README, CHANGELOG | `prs --triage` fallback (codex after claude) |
| vision via backend-native attach | skillgen all platforms if not shared fragment |
| serial default | `detect_backend()` auto-detect (**keep off** for subscription CLI) |

### Common mistakes (codex-cli post-mortem)

1. `read_bytes=True` for codex-cli → large images hit 5MB cap; use `_CLI_IMAGE_PATH_BACKENDS`
2. `_parse_llm_json` swallow parse fail → chunk silently empty; check `parse_result.ok` → `finish_reason=length`
3. `detect_backend()` auto-detect codex-cli → shadows `OPENAI_API_KEY` intent
4. bare `which("codex")` on Windows → WinError 2 or false positive `.ps1`
5. duplicate subprocess in `prs.py` → miss UTF-8 / `_no_window_kwargs`; route through `_call_llm`
6. edit skillgen fragment but forget `--bless` → `test_skillgen.py` fail
7. `_call_llm` claude-cli `if model is not None` only → ignore `GRAPHIFY_CLAUDE_CLI_MODEL`; use `_resolve_*_cli_model(mdl)`
8. confuse codex platform skill dir with `codex-cli` backend
9. parallel `codex exec` default → session/auth conflict; serial unless env opt-in
10. compact mode: `_is_compact_model(cli_model)` in `_extraction_system(compact=...)` for weak models

### Navigate fast (no graph.json)

1. `rg 'claude-cli|rate_limit|call_with_rate_limit' graphify/ tests/` — LLM + HTTP retry touch surface
2. Read `graphify/rate_limit.py` then `graphify/llm.py` `BACKENDS`, `_create_chat_completion_with_format_fallback`, `_call_claude_cli`, `_call_codex_cli`, `extract_files_direct`, `_call_llm`, `extract_corpus_parallel`, `_extract_with_adaptive_retry`, `detect_backend`
3. Read `graphify/__main__.py` extract backend auth ~4280; rate-limit env ~4180
4. `tests/test_claude_cli_backend.py` — CLI backend recipe; `tests/test_rate_limit_retry.py` — 429 policy
5. External: `codex exec --help`, `codex login status` — verify flags before impl

If `graphify-out/graph.json` exists: `graphify query` / wiki index for architecture questions; still use `rg`/ast-index for backend edit sites above.

