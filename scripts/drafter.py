#!/usr/bin/env python3
"""
drafter.py — draft posts in the operator's voice for instagram / threads / x.

Pipeline position: trend-hits.jsonl -> drafter.py -> queue/post-<ts>.json -> publisher.py

Steps per draft:
  1. Read fresh trend hits from state/trend-hits.jsonl (evergreen voice when thin).
  2. Draft text via LLM: free PC Ollama first (SSH tunnel to localhost:11434,
     self-healing: drafter restarts a dead tunnel itself; model warmup ping
     before drafting; retries with backoff on transient failures), paid PC
     OpenClaw xAI route ONLY after Ollama genuinely fails. Every paid
     fallback is logged with its reason to state/llm_fallbacks.log.
     If NO LLM is reachable: FAIL CLOSED — clear log line, exit 1, queue nothing.
  3. Instagram: render a meme background via the media-generation CLI, overlay
     classic top/bottom meme text with PIL (crisp, correctly spelled), save to
     media/. Photoreal bar: reject anything cheap-looking. Never strip
     provenance metadata (EXIF is preserved when present).
  4. Enforcement BEFORE queueing:
       - blocklist regex scan (same patterns as publisher.py) -> REJECT
       - newsy-assertion heuristic (same as publisher.py) -> park queue/review/
       - not-creepy rules -> park queue/review/
       - no-AI-label rule (never caption/hashtag/tag as AI-made) -> REJECT
       - platform length caps (threads<=500, x<=280, ig<=2200) -> REJECT
       - quality gate on images -> REJECT the draft if the image fails
  5. Write queue/post-<ts>.json in publisher.py's exact format
     {"platform": ..., "text": ..., "media": ...} (+ drafter metadata fields,
     which publisher.py ignores).

Platform scope: instagram, threads, x ONLY. LinkedIn is NEVER drafted here
(drafts-first rule). X is text-first; images optional.

Usage: drafter.py [--max-per-platform N]   (default 2)
Exit codes: 0 = drafts queued (or nothing new to draft), 1 = LLM unavailable
            or fatal error (fail closed — nothing queued).
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

# qwen3 reasons at length (<think> blocks, brainstorming) before converging on
# the JSON payload. pc_parse.strip_reasoning() removes that so the draft
# parser sees the payload, not the ramble.
try:
    from pc_parse import strip_reasoning
except Exception:
    strip_reasoning = None

ENGINE = os.path.dirname(os.path.abspath(__file__))
HITS = os.path.join(ENGINE, "state", "trend-hits.jsonl")
QUEUE = os.path.join(ENGINE, "queue")
REVIEW = os.path.join(ENGINE, "queue", "review")
MEDIA_DIR = os.path.join(ENGINE, "media")
PUBLISHED_LOG = os.path.join(ENGINE, "published", "log.jsonl")
DRAFTER_LOG = os.path.join(ENGINE, "state", "drafter.log")
RESEARCH_DIR = os.path.join(ENGINE, "hidden_files", "research")
WORKSPACE = os.path.expanduser("~/workspace")
PC_SSH = os.path.expanduser("~/workspace/bin/pc-ssh")
PC_SSH_HOST = os.environ.get("PC_SSH_HOST", PC_SSH_HOST)  # local SSH alias for the PC
MEDIA_CLI = "/opt/hatch/bin/media-generation"
# 3-steps-ahead image bank: pre-generated on the operator PC via free
# browser Gemini. Drafter pulls from the bank first so drafting never waits on
# image generation; bank_builder.py keeps it stocked.
BANK_DIR = os.path.join(ENGINE, "media", "bank")
BANK_MANIFEST = os.path.join(BANK_DIR, "bank.json")
PC_IMAGE_JS = "C:/RockVideos/work/gemini_image.js"
PC_IMAGE_OUT = "C:/RockVideos/outbox/images"

# --- LLM routing: free PC Ollama first, paid xAI only as a genuine last resort.
# Hatch reaches the PC's Ollama through an SSH tunnel bound to localhost:11434.
# If the tunnel dies, drafter restarts it itself instead of burning paid xAI
# tokens on a transient hiccup. Every paid fallback is logged with its reason
# to state/llm_fallbacks.log so the fallback rate is measurable.
OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")  # "" = first model Ollama lists
OLLAMA_GEN_TIMEOUT = int(os.environ.get("OLLAMA_GEN_TIMEOUT", "150"))
OLLAMA_ATTEMPTS = int(os.environ.get("OLLAMA_ATTEMPTS", "3"))
FALLBACK_LOG = os.path.join(ENGINE, "state", "llm_fallbacks.log")
_TUNNEL_SSH = ["ssh", "-F", SSH_CONFIG, "-N",
               "-L", "11434:127.0.0.1:11434",
               "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
               "-o", "ServerAliveInterval=25", "-o", "ServerAliveCountMax=3",
               "-o", "ExitOnForwardFailure=yes", PC_SSH_HOST]

MAX_PER_PLATFORM = 2
# Scale X to max. X drafts are text-only via free Ollama,
# so X gets a higher per-run cap; image platforms stay capped until the
# PC-side Gemini image pipeline replaces the token-burning media CLI.
MAX_PER_X = 5

PLATFORMS = {
    # platform: (max_chars, needs_image)
    "instagram": {"max_chars": 2200, "needs_image": True},
    "threads":   {"max_chars": 500,  "needs_image": False},
    "x":         {"max_chars": 280,  "needs_image": False},
}

# --- Blocklist: same patterns as publisher.py (BLOCKLIST.md) ---
BLOCKLIST_PATTERNS = [
    # Customize: names, vendors, addresses, and terms that must never appear
    # in published content (former vendors, personal emails, internal codenames).
    r"<former-vendor-a>", r"<operator-email@example\.com>", r"<blocked-term>",
    # Structural patterns (keep as-is): private IPs, local paths, phone-like.
    r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", r"\b192\.168\.\d{1,3}\.\d{1,3}\b",
    r"[A-Za-z]:\\", r"/home/[a-z]+/", r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b",  # phone-like
]

# --- No-AI-label rule (operator standing rule): never caption, hashtag, or tag a
# post as AI-generated / AI-made. Plain topic mentions ("regulate AI", "#AI")
# are fine; labels claiming the post's media is AI-made are not.
NO_AI_LABEL_PATTERNS = [
    r"ai[\s\-_]?generated", r"generated[\s\-_]?by[\s\-_]?ai",
    r"made[\s\-_]?with[\s\-_]?ai", r"created[\s\-_]?with[\s\-_]?ai",
    r"ai[\s\-_]?made\b", r"#aigenerated", r"#aimade", r"#aiart\b",
    r"\bai[\s\-_]?art\b", r"\bai[\s\-_]?image", r"\bai[\s\-_]?meme",
    r"\bai[\s\-_]?photo", r"\bai[\s\-_]?picture",
]

# --- Not-creepy rules (BLOCKLIST.md): thirsty/flirty toward real people, no
# parasocial overfamiliarity with strangers, no punching down at private
# individuals, no trauma/grief-bait. Hits park in queue/review/, never publish.
NOT_CREEPY_PATTERNS = [
    (r"\b(babe|baby girl|cutie|hottie|shawty|hot mama)\b", "thirsty/pet-name language"),
    (r"\b(you'?re so hot|you'?re very hot|nice (body|legs|rack|ass))\b", "appearance comment at a real person"),
    (r"\bwe should (hang out|grab a drink|get together sometime)\b", "parasocial overfamiliarity"),
    (r"\b(slide into (your|her|his|their) dms|dm me (cutie|babe|sexy))\b", "parasocial overfamiliarity"),
    (r"\bkill yourself\b|\bkys\b", "harassment/self-harm bait"),
    (r"\brip\b.{0,20}\b(lol|lmao|😂|🤣)", "grief-bait"),
    (r"\bdeserve[sd]? to die\b", "punching down / violent"),
]

VIDEO_EXTS = (".mp4", ".mov", ".avi", ".webm")


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} drafter: {msg}"
    print(line, file=sys.stderr)
    try:
        with open(DRAFTER_LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def newsy_assertion(text):
    """Same heuristic as publisher.py: large dollar figures, percentages,
    years, trigger words -> human review, never auto-publish."""
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


def enforce_post(platform, text, media=None):
    """Returns (verdict, reason): 'ok' | 'review' | 'reject'."""
    if platform not in PLATFORMS:
        return "reject", f"platform {platform} out of drafter scope (instagram/threads/x only)"
    if not text or not text.strip():
        return "reject", "empty text"
    if len(text) > PLATFORMS[platform]["max_chars"]:
        return "reject", f"{len(text)} chars > {PLATFORMS[platform]['max_chars']} cap for {platform}"
    for pat in BLOCKLIST_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return "reject", f"blocklist hit: /{pat}/"
    for pat in NO_AI_LABEL_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return "reject", f"AI-label violation: /{pat}/ (never tag content as AI-made)"
    # Operator standing rule: EVERY post must include relevant hashtags,
    # no exceptions. Backstop: prompts already mandate them; reject the rare miss.
    if not re.search(r"#[A-Za-z0-9_]+", text):
        return "reject", "missing hashtags (operator standing rule: every post must include them)"
    flag = newsy_assertion(text)
    if flag:
        return "review", f"newsy/unverified assertion: {flag}"
    for pat, label in NOT_CREEPY_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return "review", f"not-creepy rule: {label}"
    if media:
        ext = os.path.splitext(media)[1].lower()
        if ext in VIDEO_EXTS:
            return "reject", "video requires human approval"
        if not os.path.isfile(media):
            return "reject", f"media file missing: {media}"
    elif PLATFORMS[platform]["needs_image"]:
        return "reject", f"{platform} requires an image — imageless draft dropped, never queued"
    return "ok", ""


def park_for_review(platform, text, media, reason):
    os.makedirs(REVIEW, exist_ok=True)
    path = os.path.join(REVIEW, f"review-{int(time.time())}-{platform}.json")
    with open(path, "w") as f:
        json.dump({"platform": platform, "text": text, "media": media,
                   "reason": reason,
                   "queued": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    log(f"parked in review: {path} ({reason})")


# ---------------------------------------------------------------- LLM wiring
# --- Voice template: customize for your operator. The mechanics below
# (joke structure, hashtag discipline, platform separation) are generic;
# the persona block is yours to define. Never commit a real persona here
# that you would not want public — this repo is shared with your swarm.
VOICE_BRIEF = (
    "You write social posts as <OPERATOR_NAME>: <one-line persona, e.g. "
    "small-business owner, plain-spoken, allergic to jargon>. "
    "<tone guidance: punchy one-liners, contractions, fragments, no jargon. "
    "Funny beats angry. Mock bad policy, never rage. Short. Plain words.> "
    "Joke mechanics: put the punch word LAST (cut everything after the twist), "
    "one clause of setup max, specific numbers/details beat generalities, "
    "skip the obvious first angle, end on an ironic reversal or tag. "
    "<PLATFORM VOICES: define one voice per platform here, e.g. Instagram = "
    "warm and wholesome, X = sharper zingers. State which topics live where; "
    "a topic earns its way onto a non-native platform only if it clears a high bar.> "
    "Hashtags (MANDATORY, operator standing rule): EVERY post MUST end with "
    "relevant hashtags, never zero — 2-4 max, only ones the post genuinely belongs to. "
    "Emojis: rare, max one, only when it sells the joke. "
    "NEVER: corporate speak, preachy lectures, conspiracy claims, "
    "personal attacks on private individuals, thirsty/flirty content, punching down, "
    "trauma-bait. NEVER label anything as AI-generated/AI-made. Opinion and jokes only — "
    "no unverified factual claims, no statistics, no dollar figures at policy scale, no dates."
)


def read_latest_briefing(max_chars=2500):
    """Read the newest research briefing (hidden_files/research/<date>-<slot>.md).
    Fails silent -> empty string; never breaks drafting."""
    try:
        if not os.path.isdir(RESEARCH_DIR):
            return ""
        files = sorted(f for f in os.listdir(RESEARCH_DIR)
                       if f.endswith(".md"))
        if not files:
            return ""
        with open(os.path.join(RESEARCH_DIR, files[-1])) as f:
            text = f.read().strip()
        return text[:max_chars] if len(text) > max_chars else text
    except Exception:
        return ""


def llm_prompt(platform, trend_context):
    base = VOICE_BRIEF + "\n\n"
    if platform == "instagram":
        base += (
            "Write ONE Instagram meme post (<operator IG voice>). "
            "Do not show your thinking or reasoning — output ONLY the JSON object. Reply with strict JSON only: {\"top\": \"MEME TOP TEXT (short, punchy, ALL CAPS)\", "
            "\"bottom\": \"MEME BOTTOM TEXT (the punchline, ALL CAPS)\", "
            "\"scene\": \"photorealistic scene description for the meme background, no people, no text, no watermark\", "
            "\"caption\": \"short caption in the operator's voice, one emoji max, then 2-4 hashtags\"}. "
            "Themes: <operator IG themes> — inviting to everyone. "
            "NO political memes unless the joke is undeniably hilarious; politics is X's job."
        )
    elif platform == "threads":
        base += (
            "Write ONE Threads post (<operator Threads voice>, 1-3 sentences). "
            "Do not show your thinking or reasoning — output ONLY the JSON object. Reply with strict JSON only: {\"text\": \"the post, ending with 1-3 relevant hashtags (REQUIRED, never zero)\"}. "
            "Themes: <operator IG themes> — inviting to everyone. "
            "NO politics unless the joke is undeniably hilarious; politics is X's job."
        )
    else:  # x
        base += (
            "Write ONE X post (<operator X voice>, funny not angry, max 280 chars, text only). "
            "Do not show your thinking or reasoning — output ONLY the JSON object. Reply with strict JSON only: {\"text\": \"the post, ending with 1-2 relevant hashtags (REQUIRED, never zero)\"}. "
            "Evergreen <operator> themes: <list 3-5 safe evergreen topics>."
        )
    if trend_context:
        base += f"\n\nFresh trends you may riff on (optional): {trend_context}"
    briefing = read_latest_briefing()
    if briefing:
        base += ("\n\nResearch briefing (fresh angles — use what's funny/relevant, "
                 "ignore the rest):\n" + briefing)
    return base


def _ollama_tags(timeout=8):
    """Probe Ollama's model list. Returns (models, None) or (None, error_str)."""
    try:
        with urllib.request.urlopen(OLLAMA_BASE + "/api/tags", timeout=timeout) as r:
            tags = json.load(r)
        models = [m.get("name") for m in tags.get("models", []) if m.get("name")]
        return (models, None) if models else (None, "tags returned no models")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _pc_ollama_check():
    """One cheap probe of Ollama directly on the PC, to name the real culprit
    when the tunnel is up but Ollama stays silent. Returns 'ok' or error str."""
    if not (os.path.isfile(PC_SSH) and os.access(PC_SSH, os.X_OK)):
        return "pc-ssh missing"
    try:
        proc = subprocess.run(
            [PC_SSH, 'powershell -NoProfile -Command "try { (Invoke-WebRequest '
             'http://127.0.0.1:11434/api/tags -TimeoutSec 8 -UseBasicParsing | '
             'Select-Object -ExpandProperty StatusCode) } catch { Write-Output '
             '(\'ERR:\' + $_.Exception.Message) }"'],
            capture_output=True, text=True, timeout=30)
        out = (proc.stdout or "").strip().split("\n")[-1].strip() if proc.stdout else ""
        return "ok" if out == "200" else (out[:120] or "no output")
    except Exception as e:
        return f"ssh probe failed: {e}"


def ensure_ollama_tunnel():
    """Make sure localhost:11434 answers (SSH tunnel -> PC's Ollama).
    A dead/wedged tunnel is the #1 false trigger for the paid xAI fallback,
    so drafter kills the stale tunnel and respawns it itself.
    Returns (True, '') or (False, reason)."""
    models, err = _ollama_tags()
    if models:
        return True, ""
    log(f"ollama tunnel probe failed ({err}) — restarting tunnel")
    subprocess.run(["pkill", "-f", "11434:127.0.0.1:11434"],
                   capture_output=True, timeout=10)  # kill stale tunnel only
    time.sleep(2)
    try:
        subprocess.Popen(_TUNNEL_SSH, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception as e:
        return False, f"tunnel spawn failed: {e}"
    for _ in range(6):  # ~30s for ssh + forward to come up
        time.sleep(5)
        models, err = _ollama_tags()
        if models:
            log("ollama tunnel restarted OK")
            return True, ""
    pc = _pc_ollama_check()
    return False, f"tunnel restart did not restore Ollama ({err}); pc_ollama={pc}"


def _ollama_generate(model, prompt, timeout=120, attempts=3):
    """POST /api/generate with retries + exponential backoff.
    Returns (text, None) or (None, error_str)."""
    last_err = "unknown"
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(
                OLLAMA_BASE + "/api/generate",
                data=json.dumps({"model": model, "prompt": prompt, "stream": False,
                                 # qwen3 is a thinking model; ask Ollama to skip the
                                 # <think> chain (ignored by servers that don't support it)
                                 "think": False,
                                 "options": {"temperature": 0.9}}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r).get("response")
            if resp:
                if attempt > 1:
                    log(f"ollama generate succeeded on attempt {attempt}/{attempts}")
                return resp, None
            last_err = "empty response"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            log(f"ollama generate attempt {attempt}/{attempts} failed: {last_err}")
        if attempt < attempts:
            time.sleep(2 ** attempt)  # 2s, 4s
    return None, last_err


def ollama_warmup(timeout=180):
    """Fire one tiny generate to load the model into PC memory BEFORE the real
    draft pass. Cold model loads are the #2 false trigger for paid fallbacks.
    Returns (True, '') or (False, reason). Never raises."""
    try:
        ok, reason = ensure_ollama_tunnel()
        if not ok:
            return False, reason
        models, err = _ollama_tags()
        if not models:
            return False, f"no models: {err}"
        model = OLLAMA_MODEL or models[0]
        log(f"ollama warmup: loading {model}")
        text, err = _ollama_generate(model, "Reply with exactly: WARMUP_OK",
                                     timeout=timeout, attempts=1)
        if text and "WARMUP_OK" in text:
            log("ollama warmup ok")
            return True, ""
        return False, f"warmup generate failed: {err}"
    except Exception as e:
        return False, f"warmup exception: {e}"


def _log_fallback(reason):
    """Every paid fallback lands here with its reason — this file IS the
    fallback-rate metric: wc -l state/llm_fallbacks.log."""
    msg = f"FALLBACK_TO_XAI reason={reason}"
    log(msg)
    try:
        with open(FALLBACK_LOG, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")
    except OSError:
        pass


def try_ollama(prompt, timeout=None):
    """Free PC Ollama via the localhost tunnel: self-healing tunnel, model
    warmup done separately, retries with backoff on transient failures.
    Returns (text, None) or (None, error_str)."""
    timeout = timeout if timeout is not None else OLLAMA_GEN_TIMEOUT
    ok, reason = ensure_ollama_tunnel()
    if not ok:
        return None, f"tunnel: {reason}"
    models, err = _ollama_tags()
    if not models:
        return None, f"no models: {err}"
    model = OLLAMA_MODEL or models[0]
    return _ollama_generate(model, prompt, timeout=timeout,
                            attempts=OLLAMA_ATTEMPTS)


def try_pc_openclaw(prompt, timeout=150):
    """PC's OpenClaw xAI route (paid, last resort). No keys leave the PC:
    the CLI uses the PC's own stored credential. Returns text or None."""
    if not (os.path.isfile(PC_SSH) and os.access(PC_SSH, os.X_OK)):
        return None
    fname = f"drafter-prompt-{int(time.time())}.txt"
    local = os.path.join(tempfile.gettempdir(), fname)
    try:
        with open(local, "w") as f:
            f.write(prompt)
        # Paid-fallback route: copy the prompt to the PC byte-perfect (never pass
        # long strings via ssh args — they get mangled). Configure via env:
        #   PC_SSH_USER, PC_SSH_HOST, PC_SSH_KEY, PC_SSH_PROXYCOMMAND (optional)
        proxy = os.environ.get("PC_SSH_PROXYCOMMAND", "")
        scp_cmd = ["scp", "-i", os.path.expanduser("~/.ssh/<pc-key>"),
                   "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                   "-o", "ConnectTimeout=20"]
        if proxy:
            scp_cmd += ["-o", f"ProxyCommand={proxy}"]
        scp_user = os.environ.get("PC_SSH_USER", "<ssh-user>")
        scp = subprocess.run(
            scp_cmd + [local,
                       f"{scp_user}@{PC_SSH_HOST}:C:/Users/<windows-user>/AppData/Local/Temp/{fname}"],
            capture_output=True, timeout=60)
        if scp.returncode != 0:
            log(f"pc scp failed: {scp.stderr.decode()[:200]}")
            return None
        remote = ('powershell -NoProfile -Command "openclaw agent --model xai/grok-4-1-fast '
                  f'--message-file $env:TEMP\\\\\\\\{fname} --json --timeout 120" 2>&1 | tail -c 4000')
        proc = subprocess.run([PC_SSH, remote], capture_output=True, text=True, timeout=timeout)
        out = proc.stdout or ""
        # clean up the prompt file on the PC (best effort)
        subprocess.run([PC_SSH, 'powershell -NoProfile -Command "Remove-Item $env:TEMP\\\\\\\\'
                        f'{fname} -ErrorAction SilentlyContinue"'],
                       capture_output=True, timeout=30)
        m = re.search(r"\{.*\}", out, re.DOTALL)
        if not m:
            log("pc openclaw: no JSON in output")
            return None
        try:
            data = json.loads(m.group(0))
        except ValueError:
            log("pc openclaw: unparseable JSON")
            return None
        if not data.get("ok"):
            log(f"pc openclaw error: {str(data.get('error'))[:200]}")
            return None
        for key in ("reply", "message", "text", "result", "output", "response"):
            if isinstance(data.get(key), str) and data[key].strip():
                return data[key]
        log("pc openclaw: ok but no text field found")
        return None
    except Exception as e:
        log(f"pc openclaw route failed: {e}")
        return None
    finally:
        try:
            os.remove(local)
        except OSError:
            pass


def llm_generate(prompt):
    """Cheapest working LLM wins. Paid xAI fires ONLY after Ollama genuinely
    fails (self-healed tunnel + warmup + retries exhausted). Every fallback is
    logged with its reason so the fallback rate is measurable.
    None reachable -> fail closed (None)."""
    text, err = try_ollama(prompt)
    if text:
        log("llm: ollama (free, PC via tunnel)")
        return text
    _log_fallback(f"ollama_failed({err})")
    text = try_pc_openclaw(prompt)
    if text:
        log("llm: pc openclaw xai route (paid)")
        return text
    log("FALLBACK_FAILED: paid xAI route also unreachable — fail closed")
    return None


def parse_draft_json(raw):
    if not raw:
        return None
    text = raw.strip()
    # strip markdown code fences the model sometimes adds
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    # qwen3 reasons at length (<think> blocks, brainstorming) before converging
    # on the payload at the end. Strip the ramble; keep the conclusion.
    if strip_reasoning:
        try:
            text = strip_reasoning(text)
        except Exception:
            pass
    else:
        text = re.sub(r"<think>.*?</think>", "", text,
                      flags=re.DOTALL | re.IGNORECASE).strip()
    # try candidate JSON objects, LAST first (the converged payload comes last)
    for cand in reversed(re.findall(r"\{[^{}]*\}", text, re.DOTALL)):
        try:
            d = json.loads(cand)
            if isinstance(d, dict) and (d.get("text") or d.get("caption") or d.get("top")):
                return d
        except ValueError:
            continue
    # fallback: greedy span for payloads with nested braces
    for m in reversed(list(re.finditer(r"\{.*\}", text, re.DOTALL))):
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return d
        except ValueError:
            continue
    # small models often ignore the JSON wrapper and return a bare quoted string
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            return {"text": json.loads(text)}
        except ValueError:
            return {"text": text[1:-1]}
    return None

# ------------------------------------------------------- image rendering
def overlay_meme_text(img, top_text, bottom_text):
    """Classic meme top/bottom text: bold uppercase white with black stroke.
    Mutates and returns the PIL image."""
    from PIL import ImageDraw, ImageFont
    w, h = img.size
    draw = ImageDraw.Draw(img)

    def overlay(text, y_center):
        text = (text or "").upper()
        if not text:
            return
        size = int(w * 0.11)
        while size > 12:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", size)
            bbox = draw.multiline_textbbox((0, 0), text, font=font,
                                           stroke_width=max(2, size // 25),
                                           align="center")
            tw = bbox[2] - bbox[0]
            if tw <= w * 0.92:
                break
            size -= 4
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", size)
        sw = max(2, size // 25)
        bbox = draw.multiline_textbbox((0, 0), text, font=font, stroke_width=sw, align="center")
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.multiline_text(((w - tw) / 2 - bbox[0], y_center - th / 2 - bbox[1]),
                            text, font=font, fill="white", stroke_width=sw,
                            stroke_fill="black", align="center")

    overlay(top_text, h * 0.10)
    overlay(bottom_text, h * 0.90)
    return img


def _bank_pick(pillar):
    """Pick the least-used bank image for a pillar. Returns (path, manifest_entry) or (None, None)."""
    try:
        m = json.load(open(BANK_MANIFEST))
    except Exception:
        return None, None
    cands = [im for im in m.get("images", []) if im.get("pillar") == pillar]
    if not cands:
        # fall back to any non-x pillar for IG/FB/Threads, x pillar for X
        pool = "x" if pillar == "x" else None
        cands = [im for im in m.get("images", [])
                 if (im.get("pillar") == "x") == (pool == "x")]
    if not cands:
        return None, None
    cands.sort(key=lambda im: im.get("uses", 0))
    pick = cands[0]
    p = os.path.join(BANK_DIR, pick["file"])
    if not os.path.isfile(p):
        return None, None
    return p, pick

def _bank_mark_used(pick):
    try:
        m = json.load(open(BANK_MANIFEST))
        for im in m.get("images", []):
            if im.get("file") == pick.get("file"):
                im["uses"] = im.get("uses", 0) + 1
        json.dump(m, open(BANK_MANIFEST, "w"), indent=2)
    except Exception as e:
        log(f"bank manifest update failed: {e}")

def _pc_generate_image(scene_prompt):
    """Generate one image on the operator PC via free browser Gemini. Returns local path or None."""
    tmpdir = tempfile.mkdtemp(prefix="drafter-pcimg-")
    ps = ('powershell -NoProfile -Command "Set-Location C:/RockVideos/work; '
          f'node gemini_image.js \\"{scene_prompt}, no text, no watermark\\" {PC_IMAGE_OUT}"')
    try:
        proc = subprocess.run([PC_SSH, ps], capture_output=True, text=True, timeout=300)
    except Exception as e:
        log(f"pc image gen: ssh failed: {e}")
        return None
    tail = (proc.stdout.strip().split("\n")[-1] if proc.stdout.strip() else "")
    try:
        res = json.loads(tail[tail.index("{"):])
    except Exception:
        log("pc image gen: unparseable result")
        return None
    if not res.get("ok"):
        log(f"pc image gen failed: {res.get('error')}")
        return None
    win_path = res["file"].replace("\\", "/")
    local = os.path.join(tmpdir, "pcimg.jpg")
    rc = subprocess.run(["scp", "-F", SSH_CONFIG, "-q",
                         f"{PC_SSH_HOST}:{win_path}", local],
                        capture_output=True, timeout=120).returncode
    if rc != 0 or not os.path.isfile(local) or os.path.getsize(local) < 20000:
        log("pc image gen: retrieve failed")
        return None
    return local

def render_meme_image(scene_prompt, top_text, bottom_text, slug, pillar="funny"):
    """Render a meme: bank image first (3-steps-ahead, free), then PC browser
    Gemini (free), then the token-burning media CLI as a last resort.
    Overlays classic top/bottom meme text with PIL. Returns path or None.

    Quality gate: valid image, >=600px on the short side, >=50KB. Photoreal
    bar enforced by prompt wording; anything failing the gate rejects the draft.
    Provenance: EXIF is preserved when present — never stripped to fool detectors.
    """
    from PIL import Image, ImageDraw, ImageFont
    src = None
    bank_pick = None
    # 1) Bank — pre-generated, zero wait, zero tokens.
    bank_src, bank_pick = _bank_pick(pillar)
    if bank_src:
        src = bank_src
        log(f"meme: bank hit ({bank_pick['file']}, uses={bank_pick.get('uses',0)})")
    else:
        # 2) PC browser Gemini — free, ~30-60s.
        log("meme: bank empty, generating on PC")
        src = _pc_generate_image(scene_prompt)
        # 3) Last resort: paid media CLI.
        if not src:
            log("meme: PC gen failed, falling back to paid media CLI (last resort)")
            tmpdir = tempfile.mkdtemp(prefix="drafter-img-")
            conv = json.dumps([{"text": scene_prompt + ", no people, no text, no watermark, photorealistic"}])
            proc = subprocess.run(
                [MEDIA_CLI, "--media-subagent-output-type", "image",
                 "--conversation-json", conv, "--orientation", "square",
                 "--output-dir", tmpdir, "--image-output-format", "jpg",
                 "--timeout-secs", "95"],
                capture_output=True, text=True, timeout=120)
            m = re.search(r"\{.*\}", proc.stdout or "", re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                    files = data.get("downloaded_files") or []
                    if files:
                        src = files[0]
                except ValueError:
                    pass
            if not src or not os.path.isfile(src):
                cands = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)
                         if f.lower().endswith((".jpg", ".jpeg"))]
                src = cands[0] if cands else None
            if not src:
                log("image render failed: no file from any source")
                return None
    try:
        img = Image.open(src).convert("RGB")
        w, h = img.size
        if min(w, h) < 600:
            log(f"image quality gate: too small ({w}x{h})")
            return None

        overlay_meme_text(img, top_text, bottom_text)

        ts = int(time.time())
        out = os.path.join(MEDIA_DIR, f"meme-{slug}-{ts}.jpg")
        os.makedirs(MEDIA_DIR, exist_ok=True)
        save_kwargs = {"quality": 92}
        try:
            if img.info.get("exif"):
                save_kwargs["exif"] = img.info["exif"]  # preserve provenance, never strip
        except Exception:
            pass
        img.save(out, **save_kwargs)
        if os.path.getsize(out) < 50 * 1024:
            log("image quality gate: file suspiciously small")
            os.remove(out)
            return None
        log(f"meme rendered: {out} ({w}x{h})")
        if bank_pick:
            _bank_mark_used(bank_pick)
        return out
    except Exception as e:
        log(f"image render error: {e}")
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ------------------------------------------------------- trends + dedupe
def load_trend_hits(limit=3, max_age_hours=24):
    hits = []
    if not os.path.isfile(HITS):
        return hits
    cutoff = time.time() - max_age_hours * 3600
    try:
        with open(HITS) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    # multi-line entries from other writers land here as
                    # fragments / bare strings; only dict rows are trends
                    continue
                ts = e.get("ts", "")
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    # entries written without a ts field: fall back to the file's
                    # mtime so fresh trend files are never silently ignored
                    try:
                        t = os.path.getmtime(HITS)
                    except OSError:
                        continue
                if t >= cutoff:
                    hits.append(e.get("trend", ""))
    except OSError:
        pass
    # most recent first, deduped
    seen, fresh = set(), []
    for h in reversed(hits):
        if h and h not in seen:
            seen.add(h)
            fresh.append(h)
    return fresh[:limit]


def already_drafted(normalized_text):
    """Avoid queueing the same zinger twice."""
    needle = re.sub(r"\s+", " ", normalized_text.strip().lower())
    if not needle:
        return False
    # live queue
    try:
        for fn in os.listdir(QUEUE):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(QUEUE, fn)) as f:
                    t = json.load(f).get("text", "")
                if needle in re.sub(r"\s+", " ", t.lower()):
                    return True
            except (ValueError, OSError):
                continue
    except OSError:
        pass
    # published history
    try:
        with open(PUBLISHED_LOG) as f:
            for line in f:
                try:
                    t = json.loads(line).get("text", "")
                except ValueError:
                    continue
                if needle and needle in re.sub(r"\s+", " ", t.lower()):
                    return True
    except OSError:
        pass
    return False


def write_queue_item(platform, text, media=None, extra=None):
    target_dir = QUEUE
    if platform == "x":
        # X is published by the browser-driven X engine, not publisher.py —
        # keep X drafts out of the main sweep's top-level queue.
        target_dir = os.path.join(QUEUE, "x")
    os.makedirs(target_dir, exist_ok=True)
    ts = int(time.time())
    path = os.path.join(target_dir, f"post-{ts}-{platform}.json")
    i = 0
    while os.path.exists(path):
        i += 1
        path = os.path.join(QUEUE, f"post-{ts}-{platform}-{i}.json")
    item = {"platform": platform, "text": text}
    if media:
        item["media"] = media
    item["drafted_at"] = datetime.now(timezone.utc).isoformat()
    item["drafter"] = "drafter.py"
    if extra:
        item.update(extra)
    with open(path, "w") as f:
        json.dump(item, f, indent=2)
    log(f"queued: {path}")
    return path


# ------------------------------------------------------------------ main
def main():
    # Serialize drafter runs: the PC's Ollama serves ONE request at a time, so
    # overlapping runs just queue behind each other and time out. A second run
    # finding the lock skips its cycle instead of piling on.
    _lockf = open(os.path.join(ENGINE, "state", "drafter.lock"), "w")
    try:
        fcntl.flock(_lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another drafter run holds the lock — skipping this cycle (no pile-on)")
        return 0

    max_per = MAX_PER_PLATFORM
    dry_run = False
    args = sys.argv[1:]
    if "--dry-run" in args:
        dry_run = True
    if "--max-per-platform" in args:
        try:
            max_per = int(args[args.index("--max-per-platform") + 1])
        except (ValueError, IndexError):
            print("usage: drafter.py [--max-per-platform N] [--dry-run]", file=sys.stderr)
            return 2

    trends = load_trend_hits()
    trend_ctx = "; ".join(trends) if trends else ""
    log(f"trend hits (24h): {len(trends)}")

    # Warmup ONCE: load the model into PC memory so the real draft pass never
    # eats a cold-load timeout and triggers a paid fallback. Non-fatal — the
    # preflight below retries on its own.
    ok, reason = ollama_warmup()
    if not ok:
        log(f"ollama warmup failed ({reason}) — proceeding; preflight will retry")

    # Preflight the LLM ONCE before doing any work: fail closed if unreachable.
    probe = llm_generate("Reply with exactly: DRAFTER_OK")
    if not probe or "DRAFTER_OK" not in probe:
        log("LLM_UNAVAILABLE: no local Ollama and PC OpenClaw xAI route unreachable — "
            "fail closed, nothing queued. (Install Ollama locally or restore the PC gateway.)")
        return 1
    log("llm preflight ok")

    if dry_run:
        log("dry-run: one text draft per platform, queueing nothing, no images")
        for platform in ("instagram", "threads", "x"):
            raw = llm_generate(llm_prompt(platform, trend_ctx))
            draft = parse_draft_json(raw)
            text = (draft or {}).get("text") or (draft or {}).get("caption") or ""
            print(f"--- {platform} ({len(text)} chars) ---")
            print(text if text else "(unparseable draft)")
            print()
        return 0

    queued = 0
    for platform in ("instagram", "threads", "x"):
        cap = MAX_PER_X if platform == "x" else max_per
        made = 0
        attempts = 0
        while made < cap and attempts < cap + 2:
            attempts += 1
            raw = llm_generate(llm_prompt(platform, trend_ctx))
            draft = parse_draft_json(raw)
            if not draft or not draft.get("text") and not draft.get("caption"):
                log(f"{platform}: llm returned unparseable draft, skipping")
                continue
            text = draft.get("text") or draft.get("caption") or ""
            if already_drafted(text):
                log(f"{platform}: duplicate of existing draft, skipping")
                continue

            media = None
            if platform == "instagram":
                scene_txt = (draft.get("scene", "") + " " + text).lower()
                if any(k in scene_txt for k in ("eagle", "eagles", "football", "nfl", "philadelphia")):
                    pillar = "eagles"
                elif any(k in scene_txt for k in ("guitar", "rock", "band", "concert", "vinyl", "metal")):
                    pillar = "rock"
                elif any(k in scene_txt for k in ("dad", "kid", "kids", "father", "minivan", "garage")):
                    pillar = "dad"
                else:
                    pillar = "funny"
                media = render_meme_image(
                    draft.get("scene", "photorealistic meme background"),
                    draft.get("top", ""), draft.get("bottom", ""),
                    slug=re.sub(r"[^a-z0-9]+", "-", text.lower())[:24].strip("-") or "meme",
                    pillar=pillar)
                if not media:
                    log(f"{platform}: image render failed — draft dropped (never queued imageless)")
                    continue

            verdict, reason = enforce_post(platform, text, media)
            if verdict == "reject":
                log(f"{platform}: REJECTED ({reason})")
                if media and os.path.isfile(media):
                    os.remove(media)
                continue
            if verdict == "review":
                park_for_review(platform, text, media, reason)
                continue
            write_queue_item(platform, text, media,
                             extra={"meme_top": draft.get("top"), "meme_bottom": draft.get("bottom")})
            made += 1
            queued += 1
        log(f"{platform}: {made} draft(s) queued")

    log(f"done: {queued} queued")
    return 0


if __name__ == "__main__":
    sys.exit(main())
