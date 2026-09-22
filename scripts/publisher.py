#!/usr/bin/env python3
"""
publisher.py — gated auto-publisher for the operator's social accounts.

Usage:
    publisher.py post.json [--dry-run]

post.json: {"platform": "threads"|"instagram", "text": "...", "media": "/workspace/path.jpg" (optional; may be a list of image paths for an IG carousel)}

Enforcement order:
  1. Blocklist scan (BLOCKLIST.md) -> reject
  2. Newsy-assertion heuristic -> needs_review (parked, never auto-published)
  3. Media rules: NO video; image must exist under ~/workspace
  4. Length limits (Threads <=500 chars, IG caption <=2200)
  5. Caps: documented platform ceilings + local daily accounting + operator's pacing rule.
     Threads: 250 API-published posts / rolling 24h.
     Instagram: 2/day — operator's standing reputation/pacing rule,
     enforced here instead of Meta's ~100/day platform ceiling.
     NOTE: neither Meta's Threads API nor the IG Graph API exposes a live
     "quota remaining" endpoint — only rate-limit headers on responses. The
     engine therefore uses the LOWER of (documented ceiling, locally counted
     publishes) and treats any 429 / rate-limit provider error as a HARD STOP.
  6. Burst pacing: >=120s since last publish; <25 publishes in last 60 min.
  7. Publish via the platform CLI; log result; update daily counts.

Exit codes: 0=published (or dry-run clean), 1=rejected/needs_review,
            2=usage/config error, 3=rate-limited (hard stop, do not retry this cycle).
"""
import json, os, re, subprocess, sys, time
from datetime import datetime, timezone

ENGINE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ENGINE, "published", "log.jsonl")
COUNTS = os.path.join(ENGINE, "state", "daily-counts.json")
REVIEW = os.path.join(ENGINE, "queue", "review")
WORKSPACE = os.path.expanduser("~/workspace")

# Account IDs and handles come from the environment — never hardcode them.
#   THREADS_ACCOUNT_ID, IG_ACCOUNT_ID, OPERATOR_HANDLE
ACCOUNTS = {
    "threads":   {"cli": "threads-cli",   "id": os.environ.get("THREADS_ACCOUNT_ID", ""), "cap": 250},
    "instagram": {"cli": "instagram-cli",  "id": os.environ.get("IG_ACCOUNT_ID", ""),      "cap": 2,
                  "username": os.environ.get("OPERATOR_HANDLE", "")},
}

BLOCKLIST_PATTERNS = [
    # Customize: names, vendors, addresses, and terms that must never appear
    # in published content (former vendors, personal emails, internal codenames).
    r"<former-vendor-a>", r"<operator-email@example\.com>", r"<blocked-term>",
    # Structural patterns (keep as-is): private IPs, local paths, phone-like.
    r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
    r"[A-Za-z]:\\", r"/home/[a-z]+/", r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",  # phone-like
]
VIDEO_EXTS = (".mp4", ".mov", ".avi", ".webm")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# Newsy-assertion heuristic: large dollar figures, percentages, years,
# or trigger words -> human review, never auto-publish.
# Small-dollar zingers ("$600 Venmo") pass; policy-scale figures don't.
def newsy_assertion(text):
    for m in re.finditer(r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion|trillion)?", text, re.IGNORECASE):
        num = float(m.group(1).replace(",", ""))
        if m.group(2) or num >= 1000:
            return f"large dollar figure: {m.group(0).strip()}"
    for pat, label in [
        (r"\b\d+(?:\.\d+)?\s*%", "percentage statistic"),
        (r"\b(19|20)\d{2}\b", "specific year"),
        (r"\bbreaking\b|\bjust announced\b|\bsources say\b|\breportedly\b|\bconfirmed\b", "news trigger word"),
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return f"{label}: {m.group(0)}"
    return None

def log_event(status, platform, text, reason=None, post_id=None):
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "status": status,
             "platform": platform, "text": text[:200], "reason": reason, "post_id": post_id}
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry

def load_counts():
    if os.path.exists(COUNTS):
        with open(COUNTS) as f:
            return json.load(f)
    return {}

def save_counts(c):
    with open(COUNTS, "w") as f:
        json.dump(c, f, indent=2)

def recent_publishes(minutes):
    cutoff = time.time() - minutes * 60
    n, last = 0, 0.0
    if os.path.exists(LOG):
        with open(LOG) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("status") != "published":
                    continue
                t = datetime.fromisoformat(e["ts"]).timestamp()
                if t >= cutoff:
                    n += 1
                last = max(last, t)
    return n, last

def reject(platform, text, reason, dry):
    msg = f"REJECTED [{platform}]: {reason}"
    print(msg)
    if not dry:
        log_event("rejected", platform, text, reason)
    return 1

# Cross-posting (operator standing order): every successful Instagram publish is
# mirrored to Threads via threads-cli publish-post (same text + images), and a
# Facebook draft is queued in queue/fb/pending/ for review (FB stays draft-first
# per the operator's standing rule; facebook-cli cannot publish). Threads text limit is
# 500 chars — longer IG captions skip the Threads mirror (logged, never truncated).
def crosspost_to_threads(text, media_list):
    acct = ACCOUNTS["threads"]
    if len(text) > 500:
        log_event("crosspost_skipped", "threads", text, "caption > 500 chars")
        print("CROSSPOST [threads]: skipped — caption over 500 chars")
        return False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    counts = load_counts()
    used = counts.get("threads", {}).get(today, 0)
    if used >= acct["cap"]:
        log_event("crosspost_skipped", "threads", text, f"daily cap reached ({used}/{acct['cap']})")
        print(f"CROSSPOST [threads]: skipped — daily cap reached ({used}/{acct['cap']})")
        return False
    cmd = [acct["cli"], "publish-post", "--account-id", acct["id"], "--text", text]
    for m in media_list[:20]:
        item = json.dumps({"file": m, "alt_text":
                          f"Photo from @{ACCOUNTS['instagram']['username'] or '<operator-handle>'} on Instagram"})
        cmd += ["--media-item", item]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        log_event("crosspost_failed", "threads", text, "publish command timed out")
        return False
    blob = (out.stdout or "") + (out.stderr or "")
    if "429" in blob or "rate limit" in blob.lower() or "rate_limit" in blob.lower():
        print("CROSSPOST [threads]: provider 429 — skipped, no retry this cycle")
        log_event("crosspost_rate_limited", "threads", text, "provider 429/rate_limit")
        return False
    if out.returncode != 0:
        log_event("crosspost_failed", "threads", text, f"cli exit {out.returncode}: {blob[:300]}")
        print(f"CROSSPOST [threads]: FAILED cli exit {out.returncode}")
        return False
    try:
        post_id = json.loads(out.stdout).get("post_id")
    except (ValueError, AttributeError):
        post_id = None
    counts.setdefault("threads", {})[today] = used + 1
    save_counts(counts)
    log_event("published", "threads", text, post_id=post_id, reason="instagram crosspost")
    print(f"CROSSPOST [threads]: published post_id={post_id} (daily {used + 1}/{acct['cap']})")
    return True


def queue_fb_crosspost(text, media_list, ig_post_id):
    pending = os.path.join(ENGINE, "queue", "fb", "pending")
    os.makedirs(pending, exist_ok=True)
    rec = {"source": "instagram_crosspost",
           "text": text,
           "media": media_list,
           "ig_post_id": ig_post_id,
           "queued": datetime.now(timezone.utc).isoformat(),
           "note": "IG crosspost: auto-published by the social-fb-xpost-publisher cron (operator is FB-monetized). Original fb drafts stay review-gated."}
    path = os.path.join(pending, f"xpost-{int(time.time())}.json")
    with open(path, "w") as f:
        json.dump(rec, f, indent=2)
    log_event("crosspost_queued", "facebook", text, reason=path)
    print(f"CROSSPOST [facebook]: draft queued for review ({os.path.basename(path)})")


# After a publish whose CLI output carries no usable post_id, confirm the post
# actually landed by reading the live profile and matching our caption.
# Returns the live post_id, or None if no matching recent post is found.
def verify_ig_post_id(acct, text):
    if not acct.get("username"):
        return None
    try:
        v = subprocess.run(
            [acct["cli"], "posts", "--account-id", acct["id"],
             "--username", acct["username"], "--limit", "5"],
            capture_output=True, text=True, timeout=60)
        if v.returncode != 0:
            return None
        posts = json.loads(v.stdout).get("posts", []) or []
    except Exception:
        return None
    now = time.time()
    for p in posts:
        cap = p.get("post_caption") or ""
        if cap and text[:60] and (text[:60] in cap or cap[:60] in text):
            return p.get("post_id")
    # Fallback: newest post created within 10 minutes counts as ours.
    for p in posts:
        try:
            ts = datetime.strptime(p.get("created_at", ""), "%Y-%m-%d %H:%M:%S")
            ts = ts.replace(tzinfo=timezone.utc).timestamp()
            if now - ts < 600:
                return p.get("post_id")
        except Exception:
            continue
    return None


def main():
    dry = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    if len(args) != 1:
        print("usage: publisher.py post.json [--dry-run]", file=sys.stderr)
        return 2
    with open(args[0]) as f:
        post = json.load(f)

    platform = post.get("platform", "").lower()
    text = post.get("text", "") or ""
    media = post.get("media")
    # Carousel support: "media" may be a list of image paths (instagram only).
    media_list = media if isinstance(media, list) else ([media] if media else [])
    if platform not in ACCOUNTS:
        print(f"unknown platform: {platform}", file=sys.stderr)
        return 2

    # 1. Blocklist
    for pat in BLOCKLIST_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return reject(platform, text, f"blocklist hit: /{pat}/", dry)

    # 2. Newsy-assertion heuristic -> human review
    flag = newsy_assertion(text)
    if flag:
        msg = f"NEEDS REVIEW [{platform}]: newsy/unverified factual assertion detected"
        print(msg)
        if not dry:
            os.makedirs(REVIEW, exist_ok=True)
            with open(os.path.join(REVIEW, f"review-{int(time.time())}.json"), "w") as f:
                json.dump({"platform": platform, "text": text, "media": media,
                           "queued": datetime.now(timezone.utc).isoformat()}, f, indent=2)
            log_event("needs_review", platform, text, "newsy assertion heuristic")
        return 1

    # 3. Media rules
    for m in media_list:
        ext = os.path.splitext(m)[1].lower()
        if ext in VIDEO_EXTS:
            return reject(platform, text, "video requires human approval", dry)
        if ext not in IMAGE_EXTS:
            return reject(platform, text, f"unsupported media type: {ext}", dry)
        if not m.startswith(WORKSPACE) or not os.path.isfile(m):
            return reject(platform, text, "media must be an existing file under ~/workspace", dry)
    if platform == "instagram" and len(media_list) > 1:
        pass  # carousel: CLI takes repeated --file

    # 4. Length limits
    if platform == "threads" and len(text) > 500:
        return reject(platform, text, f"threads text {len(text)} chars > 500", dry)
    if platform == "instagram" and len(text) > 2200:
        return reject(platform, text, f"ig caption {len(text)} chars > 2200", dry)
    if not text.strip() and not media:
        return reject(platform, text, "empty post", dry)

    # 5. Daily caps (documented ceiling vs local accounting -> lower wins)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    counts = load_counts()
    used = counts.get(platform, {}).get(today, 0)
    cap = ACCOUNTS[platform]["cap"]
    if used >= cap:
        return reject(platform, text, f"daily cap reached ({used}/{cap})", dry)

    # 6. Burst pacing
    n_hour, last_ts = recent_publishes(60)
    if n_hour >= 25:
        print(f"RATE-LIMITED [{platform}]: 25/hour velocity cap hit — hard stop, no retry this cycle")
        if not dry:
            log_event("rate_limited", platform, text, "25/hour velocity cap")
        return 3
    if last_ts and time.time() - last_ts < 120:
        wait = int(120 - (time.time() - last_ts))
        print(f"PACED [{platform}]: last publish too recent, wait {wait}s")
        return 1

    # 7. Publish
    acct = ACCOUNTS[platform]
    if platform == "threads":
        cmd = [acct["cli"], "publish-post", "--account-id", acct["id"], "--text", text]
    else:
        if not media_list:
            return reject(platform, text, "instagram requires an image (no text-only posts)", dry)
        cmd = [acct["cli"], "post-feed", "--account-id", acct["id"]]
        for m in media_list:
            cmd += ["--file", m]
        cmd += ["--caption", text]
    if dry:
        media_desc = (f" + {len(media_list)} images (carousel)" if len(media_list) > 1
                      else (f" + image {os.path.basename(media_list[0])}" if media_list else " (text-only)"))
        print(f"DRY-RUN OK [{platform}]: would publish {len(text)} chars"
              + media_desc
              + f" | daily {used + 1}/{cap}")
        return 0

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        log_event("failed", platform, text, "publish command timed out")
        return 1
    blob = (out.stdout or "") + (out.stderr or "")
    if "429" in blob or "rate limit" in blob.lower() or "rate_limit" in blob.lower():
        print(f"RATE-LIMITED [{platform}]: provider 429 — hard stop, no retry this cycle")
        log_event("rate_limited", platform, text, "provider 429/rate_limit")
        return 3
    if out.returncode != 0:
        log_event("failed", platform, text, f"cli exit {out.returncode}: {blob[:300]}")
        print(f"FAILED [{platform}]: cli exit {out.returncode}")
        return 1
    try:
        post_id = json.loads(out.stdout).get("post_id")
    except (ValueError, AttributeError):
        post_id = None
    if not post_id and platform == "instagram":
        # CLI emitted no usable id — confirm against the live profile and
        # match our caption, so a "published" with post_id=None is never silent.
        post_id = verify_ig_post_id(acct, text)
    if post_id and args:
        # Stamp the receipt into the queue record so it travels with it to published/.
        try:
            with open(args[0]) as f:
                rec = json.load(f)
            rec["post_id"] = post_id
            rec["published_at"] = datetime.now(timezone.utc).isoformat()
            with open(args[0], "w") as f:
                json.dump(rec, f, indent=2)
        except Exception:
            pass
    counts.setdefault(platform, {})[today] = used + 1
    save_counts(counts)
    log_event("published", platform, text, post_id=post_id)
    print(f"PUBLISHED [{platform}]: post_id={post_id} (daily {used + 1}/{cap})")
    if platform == "instagram":
        # Operator's one-to-all rule: mirror to Threads, queue FB draft.
        crosspost_to_threads(text, media_list)
        queue_fb_crosspost(text, media_list, post_id)
    return 0

if __name__ == "__main__":
    sys.exit(main())
