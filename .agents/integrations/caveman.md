# caveman Integration

Source: `juliusbrussee/caveman` at `25d22f864ad68cc447a4cb93aefde918aa4aec9f`.

## Purpose

Use caveman skills to reduce agent output verbosity while keeping technical
precision. This repo uses a Claude-free, repo-local integration:

- Canonical skills live under `.agents/skills/`.
- Cavecrew subagent presets live under `.agents/agents/`.
- Platform adapters are thin rule files that point back to `.agents`.
- No upstream installer, Claude hook, statusline, or global config is run from this repo.

## Default Style

For Russian-language prompts, answer in Russian with terse technical prose:

- drop filler, pleasantries, and hedging
- use short labels like `Причина:`, `Фикс:`, `Риск:`, `Проверка:`
- use arrows for causality when clear
- keep code, identifiers, paths, commands, URLs, and exact error strings unchanged

Auto-clarity wins over compression. Use normal prose for security warnings,
irreversible operations, confused user state, or anything where terse fragments
could change meaning.

## Skills

- `.agents/skills/caveman/SKILL.md`: response compression mode
- `.agents/skills/caveman-commit/SKILL.md`: terse Conventional Commit messages
- `.agents/skills/caveman-review/SKILL.md`: terse code review findings
- `.agents/skills/caveman-compress/SKILL.md`: compress natural-language memory files
- `.agents/skills/caveman-help/SKILL.md`: quick reference
- `.agents/skills/cavecrew/SKILL.md`: delegation guide for terse subagents
- `.agents/skills/caveman-stats/SKILL.md`: Claude-hook stats only; not portable

## Caveats

Do not run broad caveman installers from this repo:

```bash
curl -fsSL https://raw.githubusercontent.com/JuliusBrussee/caveman/main/install.sh | bash
npx -y github:JuliusBrussee/caveman -- --all
node bin/install.js --all
```

Those paths may detect Claude Code, edit `~/.claude`, install Claude hooks, or
fail with Claude-specific errors. That is installer-scope trouble, not a skill
failure.

If a user explicitly wants a global per-agent install, use scoped commands from
the upstream docs, not `--all`. For repo-local work, use the checked-in files.

## Agent Adapters

- Codex: `.codex/skills/caveman*/SKILL.md` routes to canonical `.agents` skills.
- Cursor: `.cursor/rules/caveman.mdc`
- Cline: `.clinerules/caveman.md`
- OpenCode: `.opencode/AGENTS.md`
- Windsurf: `.windsurf/rules/caveman.md`
- Gemini: `GEMINI.md`
- Copilot: `.github/copilot-instructions.md`

Kilo Code has no committed hidden config path here. Use the canonical `.agents`
skills if the host supports them, or invoke `/caveman ultra` per session.
