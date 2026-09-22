# Trial-and-Error Log

Every lesson this team learned by breaking something first. Newest entries go
at the bottom. Each entry: date, what happened, why, and the rule going forward.

---

## 2026-09-21 — SSH mangles long secrets passed as command-line arguments

An 84-character alphanumeric token arrived as 74 characters after traveling
through `ssh <host> "<command>"`. Shell quoting/proxy layers ate characters.
**Rule:** never pass secrets, tokens, or exact long strings via SSH command-line
args. Move exact bytes with `scp` as a file, or have the operator paste into a
file the script reads. Verified: `scp` transfer is byte-perfect.

## 2026-09-21 — Don't retype credentials from chat

Manual copies of the same key varied (83/84/86 chars across attempts). Humans
and agents both mistype long random strings.
**Rule:** credentials enter through the secure vault flow or a file on the
target machine. Never retype a key.

## 2026-09-21 — SSH config ignored when running as root

`ssh rockpc` failed: the process ran as root, so `~/.ssh/config` resolved to
`/root/.ssh/config`, not the user's. The host block (user, key, proxy) never
applied and the egress proxy reset the connection.
**Rule:** always `ssh -F /home/hatch/.ssh/config rockpc`, or use the
`pc-ssh` wrapper that bakes the flag in.

## 2026-09-21 — Instagram rejects mixed-aspect-ratio carousels

A 5-image carousel mixing 1:2 and 2:3 aspect ratios failed 4x with HTTP 500.
**Rule:** normalize every carousel image to one aspect ratio before queueing.
Prefer JPEG over webp for IG uploads.

## 2026-09-22 — OpenClaw gateway silently down after PC reboot

ECONNREFUSED on the gateway port after a morning boot; the paid-LLM fallback
died because of it.
**Rule:** after any PC boot/reconnect, verify the gateway with an HTTP probe
(expect 200). Restart via the scheduled task if down. `scp` to the PC needs an
existing target dir (`C:/Users/<user>/`, never `/tmp` — Windows has none).

## 2026-09-22 — Gemini returns PNG bytes under a `.jpg` name

A banked "JPEG" was actually PNG/RGBA data. Extension lied.
**Rule:** never trust the extension — verify with `file` / PIL. The bank
builder now normalizes every retrieved image to genuine JPEG (RGB, q90) in
place before manifesting.

## 2026-09-22 — Browser lock never released on success path

`gemini_image.js` / `gemini_video.js` printed `ok:true` and exited without
calling `done()`, so `browser.lock` was never deleted. Every successful run
poisoned the lock; all later runs waited 5 min then failed `pc_busy`.
**Rule:** the success path must release shared locks explicitly. Defense in
depth added: lock-stealing when the recorded PID is dead, plus
exit/SIGINT/SIGTERM handlers as backstop. Keep deployed copies
(`C:/RockVideos/work/`) in sync with this repo's `scripts/`.

## 2026-09-22 — Builder crashed on a hung generation instead of skipping it

`bank_builder.py` let `subprocess.TimeoutExpired` propagate, killing a 40-minute
run over one stuck image.
**Rule:** catch timeouts per attempt, log, retry once, then move on. One bad
job must never sink the batch.
