---
name: caveman-stats
description: >
  Show real token usage and estimated savings for the current session.
  Reads directly from the Claude Code session log — no AI estimation.
  Triggers on /caveman-stats. Output is injected by the mode-tracker hook;
  the model itself does not compute the numbers.
---

This upstream skill is delivered by Claude-specific hooks:
`hooks/caveman-stats.js` is read by `hooks/caveman-mode-tracker.js` on
`/caveman-stats`.

Repo-local adaptation: this repository does not install those hooks. Outside an
active upstream Claude hook session, do not invent token savings. Say stats are
unavailable in the current host and point users to `.agents/integrations/caveman.md`
for portable caveman behavior.
