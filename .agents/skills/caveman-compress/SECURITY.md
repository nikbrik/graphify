# Security

## graphify repo-local note

This vendored copy is adapted for multi-agent use. Default operation uses the
current agent to compress prose and should not send file contents to Anthropic
or invoke the Claude CLI. Use the upstream scripts only as an explicit opt-in
backend after the user accepts that data boundary.

## Snyk High Risk Rating

`caveman-compress` receives a Snyk High Risk rating due to static analysis heuristics. This document explains what the skill does and does not do.

### What triggers the rating

1. **subprocess usage**: The skill calls the `claude` CLI via `subprocess.run()` as a fallback when `ANTHROPIC_API_KEY` is not set. The subprocess call uses a fixed argument list — no shell interpolation occurs. User file content is passed via stdin, not as a shell argument.

2. **File read/write**: The skill reads the file the user explicitly points it at, compresses it, validates the result against the in-memory original, and writes the result back to the same path. No backup files are created. No unrelated files are read.

### What the skill does NOT do

- Does not execute user file content as code
- Default repo-local path does not make network requests. Optional upstream script backend may call Anthropic's API via SDK or Claude CLI.
- Does not read files outside the path the user provides
- Does not use shell=True or string interpolation in subprocess calls
- Does not collect or transmit any data beyond the file being compressed

### Optional script auth behavior

When the upstream script backend is explicitly used, `ANTHROPIC_API_KEY` routes
through the Anthropic Python SDK directly. If it is not set, the script falls
back to the `claude` CLI, which uses the user's existing Claude desktop
authentication.

### File size limit

Files larger than 500KB are rejected before any API call is made.

### Reporting a vulnerability

If you believe you've found a genuine security issue, please open a GitHub issue with the label `security`.
