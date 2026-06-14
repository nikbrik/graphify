# Vendored Agent Sources

This repository vendors agent instruction payloads, not active installers.

## ast-index

- Upstream: https://github.com/defendend/Claude-ast-index-search
- Commit: `97437e0cc79d4c430177f140daebb608df382030`
- License: MIT, copied at `licenses/Claude-ast-index-search-LICENSE`
- Vendored payload: `.agents/skills/ast-index/`

## caveman

- Upstream: https://github.com/juliusbrussee/caveman
- Commit: `25d22f864ad68cc447a4cb93aefde918aa4aec9f`
- License: MIT, copied at `licenses/caveman-LICENSE`
- Vendored payload: `.agents/skills/caveman*`, `.agents/skills/cavecrew`, `.agents/agents/cavecrew-*`

## Local Adaptation Policy

Keep upstream substance, but adapt files for repo-local, multi-agent use:

- no broad installers
- no Claude-only hook activation
- no global config writes
- no user-specific absolute paths
- adapters point back to `.agents` as source of truth
