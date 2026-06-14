---
name: caveman-compress
description: >
  Compress natural language memory files (AGENTS.md, .agents/*.md, CLAUDE.md, todos, preferences) into caveman format
  to save input tokens. Preserves all technical substance, code, URLs, and structure.
  Compressed version overwrites the original file. No backup files are created; use git for rollback.
  Trigger: /caveman-compress FILEPATH or "compress memory file"
---

# Caveman Compress

## Purpose

Compress natural language memory files (`AGENTS.md`, `.agents/*.md`,
`CLAUDE.md`, todos, preferences) into caveman-speak to reduce input tokens.
Compressed version overwrites original. No backup files are created; use git for
rollback.

## Repo-Local Multi-Agent Adaptation

Portable default: use the current agent to compress the file. Do not call
Anthropic, Claude CLI, or upstream scripts unless the user explicitly asks for
that backend and accepts the data boundary.

The bundled `scripts/` directory is kept for upstream compatibility. In this
repo, treat it as optional Claude/Anthropic tooling, not the default path.

## Trigger

`/caveman-compress <filepath>` or when user asks to compress a memory file.

## Process

1. Read the target file.
2. Rewrite only natural-language prose. Preserve code blocks, inline code,
   paths, commands, URLs, frontmatter, headings, tables, and structure exactly
   where required by the rules below.
3. Validate the result manually against the preservation rules before writing.
4. Return a short summary with changed file path. Do not create backup files.

## Compression Rules

### Remove
- Articles: a, an, the
- Filler: just, really, basically, actually, simply, essentially, generally
- Pleasantries: "sure", "certainly", "of course", "happy to", "I'd recommend"
- Hedging: "it might be worth", "you could consider", "it would be good to"
- Redundant phrasing: "in order to" → "to", "make sure to" → "ensure", "the reason is because" → "because"
- Connective fluff: "however", "furthermore", "additionally", "in addition"

### Preserve EXACTLY (never modify)
- Code blocks (fenced ``` and indented)
- Inline code (`backtick content`)
- URLs and links (full URLs, markdown links)
- File paths (`/src/components/...`, `./config.yaml`)
- Commands (`npm install`, `git commit`, `docker build`)
- Technical terms (library names, API names, protocols, algorithms)
- Proper nouns (project names, people, companies)
- Dates, version numbers, numeric values
- Environment variables (`$HOME`, `NODE_ENV`)

### Preserve Structure
- All markdown headings (keep exact heading text, compress body below)
- Bullet point hierarchy (keep nesting level)
- Numbered lists (keep numbering)
- Tables (compress cell text, keep structure)
- Frontmatter/YAML headers in markdown files

### Compress
- Use short synonyms: "big" not "extensive", "fix" not "implement a solution for", "use" not "utilize"
- Fragments OK: "Run tests before commit" not "You should always run tests before committing"
- Drop "you should", "make sure to", "remember to" — just state the action
- Merge redundant bullets that say the same thing differently
- Keep one example where multiple examples show the same pattern

CRITICAL RULE:
Anything inside ``` ... ``` must be copied EXACTLY.
Do not:
- remove comments
- remove spacing
- reorder lines
- shorten commands
- simplify anything

Inline code (`...`) must be preserved EXACTLY.
Do not modify anything inside backticks.

If file contains code blocks:
- Treat code blocks as read-only regions
- Only compress text outside them
- Do not merge sections around code

## Pattern

Original:
> You should always make sure to run the test suite before pushing any changes to the main branch. This is important because it helps catch bugs early and prevents broken builds from being deployed to production.

Compressed:
> Run tests before push to main. Catch bugs early, prevent broken prod deploys.

Original:
> The application uses a microservices architecture with the following components. The API gateway handles all incoming requests and routes them to the appropriate service. The authentication service is responsible for managing user sessions and JWT tokens.

Compressed:
> Microservices architecture. API gateway route all requests to services. Auth service manage user sessions + JWT tokens.

## Boundaries

- ONLY compress natural language files (.md, .txt, .typ, .typst, .tex, extensionless)
- NEVER modify: .py, .js, .ts, .json, .yaml, .yml, .toml, .env, .lock, .css, .html, .xml, .sql, .sh
- If file has mixed content (prose + code), compress ONLY the prose sections
- If unsure whether something is code or prose, leave it unchanged
- No backup files are created. Use git to inspect or restore previous content.
- Never compress existing FILE.original.md files (skip old backup artifacts)
