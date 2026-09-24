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
