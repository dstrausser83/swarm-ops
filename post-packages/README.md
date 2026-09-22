# post-packages/

A post package is the unit the publisher consumes. Drafters write them,
`scripts/publisher.py` enforces the gates and publishes them.

## Format (`post-<unixts>-<platform>.json`)
```json
{
  "platform": "instagram | threads | x",
  "text": "<post text, hashtags REQUIRED at the end>",
  "media": "<absolute path to image, or list of paths for a carousel>",
  "drafted_at": "<iso timestamp>",
  "drafted_by": "<agent>",
  "voice": "<voice profile used>",
  "critic_verdict": "APPROVE | REVISE | REJECT",
  "notes": "<anything the reviewer should know>"
}
```
`publisher.py` only reads `platform`, `text`, `media` — the rest is provenance
for the reviewer. See `example-post-package.json` for a filled template.

## Lifecycle
1. `drafter.py` (or a PC agent) writes the package into the local `queue/`
   (queue/ is git-ignored — packages are runtime data, not repo content).
2. `publisher.py post-....json` runs the gates: blocklist → newsy heuristic →
   media rules → length caps → daily caps → burst pacing → publish.
3. Exit 0 = published (receipt stamped into the package); 1 = rejected/review;
   3 = rate-limited hard stop.
