# Activity Logs

Concise summaries of what the team did — not raw transcripts.

## Convention

- One file per day: `YYYY-MM-DD.md` (UTC date).
- Each entry: what ran, what it produced, what broke, what was learned
  (with a pointer to `knowledge/learnings.md` when a new lesson landed).
- Keep entries short. If it took more than 5 lines to describe, link the
  relevant doc instead.
- Raw outputs, queue contents, media, and state files never commit
  (see `.gitignore`). Summarize them here.

## Example

```markdown
# 2026-09-22

- Bank builder: 12 meme-base prompts queued; run 1 produced 1/12 before a
  stale browser.lock killed the rest. Root cause → learnings.md (lock bug).
  Fixed gemini_image.js/gemini_video.js, redeployed, re-ran.
- Moltbook heartbeat: 3 routine engagements, nothing worth surfacing.
- Ollama tunnel dropped 10:56, re-established 11:00 (transient proxy issue).
```
