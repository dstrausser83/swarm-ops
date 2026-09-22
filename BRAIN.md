# 🧠 Swarm Brain

This repo is the private knowledge and coordination base for the agent team:
**`<operator> > Rock > Scout / Hammer / Scribe / Analyst / Critic`**.

It exists so the team never forgets what it learned the hard way, and so any
agent can pick up the full picture from a cold start.

## Layout

| Path | What lives here |
|---|---|
| `scripts/` | Runnable tools the team uses (drafters, publishers, media builders, PC maintenance) |
| `prompts/` | Role definitions, mission cards, voice templates |
| `post-packages/` | Publisher queue format + example template |
| `docs/swarm-ops.md` | Chain of command, credit doctrine, mission mechanics, opsec |
| `knowledge/learnings.md` | **Trial-and-error log** — every hard-won lesson, newest at the bottom with dates |
| `tools/CATALOG.md` | Free tools the team evaluated / deployed, with ROI notes |
| `logs/` | Activity summaries — what was done, when, and what came of it |

## Rules for the brain

1. **Log the lesson, not just the fix.** When something breaks and you fix it,
   add a dated entry to `knowledge/learnings.md` explaining *why* it broke.
2. **No secrets, ever.** No tokens, keys, passwords, cookies, emails, IPs,
   usernames, or personal data. Placeholders and env-var references only.
3. **Summaries, not raw dumps.** `logs/` holds concise daily/weekly summaries.
   Raw transcripts and state files stay local (see `.gitignore`).
4. **Tools earn their place.** A tool goes in `tools/CATALOG.md` only after
   someone evaluated it against a real job. Note the verdict honestly —
   rejected tools stay listed with *why*, so nobody re-evaluates them.
5. **The brain is append-only by default.** Correct mistakes, but don't rewrite
   history. The trial-and-error trail is the point.
