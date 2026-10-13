# Swarm coordination hub

Private coordination repo for a lead agent's PC bot swarm: shared scripts,
mission prompts, role definitions, and post-package formats. The swarm does the
labor on free local compute; the lead agent's judgment stays on the expensive
tokens.

## How the swarm is organized

```
<operator> > Rock > Scout / Hammer / Scribe / Analyst / Critic
```

| Agent | Role |
|---|---|
| **Scout** | Read-only recon and investigation. Looks, never touches. |
| **Hammer** | Drafting and building worker. Drafts, never ships. Runs the creative factory (image/video prompt generation). |
| **Scribe** | Long-form writing in the operator's voice (posts, articles, newsletters). |
| **Analyst** | Research synthesis: trend briefs, engagement analysis, what-worked reviews. |
| **Critic** | Quality gate: APPROVE / REVISE / REJECT verdicts on drafts. |

The operator sets direction. Rock plans, reviews, and keeps final say on
anything public. The five agents execute mission cards and return results.

## Layout

- `scripts/` — the pipeline: `drafter.py` (draft posts via free local LLM),
  `publisher.py` (gated auto-publisher), `bank_builder.py` (3-steps-ahead image
  bank), `pc_parse.py` (deterministic payload extraction from rambling local
  models), `pc-mission` (queue a mission card for a PC agent), `gemini_image.js`
  / `gemini_video.js` (browser-based free media generation), `RockCleanup.ps1`
  + `register_cleanup.ps1` (bots clean up generated files weekly).
- `prompts/` — role definitions (`roles/`) and recurring mission briefs
  (bank keeper, video bank keeper) plus a `voice-template.md` to customize.
- `post-packages/` — the post package format the publisher consumes, with an
  example template. Real packages are runtime data (git-ignored).
- `docs/` — `swarm-ops.md`: chain of command, credit doctrine, mission cards,
  local-model parsing notes, opsec rules.

## Setup

1. Copy `.env.example` values into your environment (never commit real ones):
   `THREADS_ACCOUNT_ID`, `IG_ACCOUNT_ID`, `OPERATOR_HANDLE`,
   `PC_SSH_HOST`, `PC_SSH_USER`, `PC_SSH_KEY`, `OLLAMA_BASE`, `OLLAMA_MODEL`.
2. Install the platform CLIs your publisher needs (`threads-cli`,
   `instagram-cli`, …) and authenticate them OUTSIDE this repo.
3. Configure SSH to the PC (`~/.ssh/config` entry, key auth, no passwords).
4. Fill in `prompts/voice-template.md` with the operator's persona.
5. Customize `BLOCKLIST_PATTERNS` in `scripts/drafter.py` / `publisher.py`.

## Conventions

- Drafts never publish without passing the publisher gates (blocklist, newsy
  heuristic, media rules, length caps, daily caps, burst pacing).
- `Critic` verdicts are machine-extracted (`pc_parse.parse_verdict`); a
  low-confidence parse escalates to the human reviewer — never auto-publish
  on a guess.
- Local models ramble: strip `<think>` blocks and extract the payload, never
  publish raw output.
- One browser media job at a time (the scripts serialize via a lock file) —
  keep the operator's PC responsive.
- Bots clean up after themselves: generated media/text older than a week is
  deleted; credentials, profiles, scripts, and state are never touched.

## Security

This repo contains NO credentials, tokens, cookies, API keys, private IPs,
emails, or login material — verified by grep before every commit (see
`.gitignore`, which aggressively excludes all of it). If you find a secret
here, treat it as compromised and rotate it.

---

# swarm-ops

Free-tier ops bots for the Rock swarm. Everything here runs at $0.

## Workflows

- **swarm-watchdog** (`.github/workflows/watchdog.yml`) — every 5 min, probes
  `https://deadbrands.co/`, `/blog/`, `/feed.xml`. On failure it opens (or
  comments on) one standing `[watchdog]` issue. Public repo = unmetered
  Actions minutes on the Free plan.

## Known gotchas

- **60-day schedule auto-disable:** GitHub disables `schedule:` triggers after
  60 days without repo activity. Backstop: an external scheduler
  (cron-job.org, free, no card) POSTing a `repository_dispatch` event, or
  just make sure something commits here regularly. The watchdog issue
  comments themselves count as activity only if a *commit* lands — issue
  activity does NOT reset the 60-day clock. A monthly heartbeat commit
  (or the cron-job.org dispatch) keeps the cron alive.
- Cron is best-effort, UTC, min 5-min interval. Not for time-critical work.
