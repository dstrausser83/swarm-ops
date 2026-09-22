#!/usr/bin/env python3
"""pc_parse.py — deterministic payload extraction for PC agent outputs.

The PC agents run qwen3:4b-8k via Ollama (free). That model reliably *reasons
at length* even when asked for one clean word. These parsers extract the
intended final payload so Rock never has to publish — or decide off — raw
rambling output.

Usage:
    from pc_parse import parse_verdict, parse_caption, parse_bullets, parse_json_block

Every parser returns a dict: {"ok": bool, "value": ..., "confidence": "high|medium|low", "note": str}
Never trust a low-confidence parse for a publishing decision — escalate to Rock.
"""

import json
import re

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Phrases qwen uses to open its reasoning; everything before the real payload
# often starts with one of these.
_PREAMBLE_RES = [
    re.compile(r"^(okay|ok|alright|let me|i need to|i should|first,?|here'?s my).{0,200}?\n", re.IGNORECASE | re.DOTALL),
]

_VERDICT_WORDS = {
    "approve": "APPROVE",
    "approved": "APPROVE",
    "pass": "APPROVE",
    "reject": "REJECT",
    "rejected": "REJECT",
    "fail": "REJECT",
    "revise": "REVISE",
    "revision": "REVISE",
    "needs work": "REVISE",
}


def strip_reasoning(text: str) -> str:
    """Remove <think> blocks and common reasoning preambles."""
    t = _THINK_RE.sub("", text or "").strip()
    # Drop a leading "reasoning paragraph" if the payload follows a blank line
    # and the first paragraph looks like meta-talk about the task.
    parts = re.split(r"\n\s*\n", t, maxsplit=1)
    if len(parts) == 2 and re.search(
        r"(i will|i need to|let me|my task|the user wants|analyzing|thinking about)",
        parts[0], re.IGNORECASE,
    ) and len(parts[0]) < 400:
        t = parts[1].strip()
    return t.strip()


def parse_verdict(text: str) -> dict:
    """Extract a Critic verdict: APPROVE | REVISE | REJECT."""
    t = strip_reasoning(text)
    # 1) explicit labeled verdict wins
    m = re.search(r"verdict\s*[:\-]\s*(approve|reject|revise[d]?)", t, re.IGNORECASE)
    if m:
        return {"ok": True, "value": _VERDICT_WORDS[m.group(1).lower()],
                "confidence": "high", "note": "labeled verdict"}
    # 2) a lone verdict word on its own line
    for line in t.splitlines():
        w = line.strip().lower().strip(".,!*\"'")
        if w in _VERDICT_WORDS:
            return {"ok": True, "value": _VERDICT_WORDS[w],
                    "confidence": "high", "note": "lone verdict word"}
    # 3) last occurrence heuristic — the conclusion usually comes last
    hits = [(m.start(), _VERDICT_WORDS[m.group(0).lower()])
            for m in re.finditer(r"\b(approve[d]?|reject[ed]?|revise[d]?|pass|fail)\b", t, re.IGNORECASE)]
    if hits:
        hits.sort()
        return {"ok": True, "value": hits[-1][1],
                "confidence": "medium", "note": "last verdict-like word; conclusion assumed final"}
    return {"ok": False, "value": None, "confidence": "low",
            "note": "no verdict found — escalate to Rock"}


def parse_caption(text: str, max_len: int = 300) -> dict:
    """Extract a Scribe caption: the payload, not the character-count discussion."""
    t = strip_reasoning(text)
    # Prefer a quoted block (models often wrap the deliverable in quotes)
    quoted = re.findall(r'"([^"]{10,400})"', t)
    if quoted:
        best = max(quoted, key=len)
        return {"ok": True, "value": best.strip(), "confidence": "high",
                "note": "quoted deliverable"}
    # Else: drop lines that are meta-talk about length/counts
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    content = [ln for ln in lines if not re.search(
        r"(character|characters|word count|\b\d+\s*(chars?|words?)\b|here is|below is)",
        ln, re.IGNORECASE)]
    # The caption is usually the longest remaining line/block
    if content:
        best = max(content, key=len)
        conf = "high" if len(best) <= max_len else "medium"
        return {"ok": True, "value": best, "confidence": conf,
                "note": "longest non-meta line"}
    return {"ok": False, "value": None, "confidence": "low",
            "note": "no caption found — escalate to Rock"}


def parse_bullets(text: str, min_bullets: int = 1) -> dict:
    """Extract an Analyst bullet list."""
    t = strip_reasoning(text)
    bullets = []
    for ln in t.splitlines():
        m = re.match(r"\s*(?:[-*•]|\d+[.)])\s+(.+)", ln.strip())
        if m and len(m.group(1).strip()) > 3:
            bullets.append(m.group(1).strip())
    if len(bullets) >= min_bullets:
        return {"ok": True, "value": bullets, "confidence": "high",
                "note": f"{len(bullets)} bullets"}
    return {"ok": False, "value": bullets, "confidence": "low",
            "note": "too few bullets — escalate to Rock"}


def parse_json_block(text: str) -> dict:
    """Pull the first JSON object/array out of rambling output."""
    t = strip_reasoning(text)
    for pat in (re.compile(r"\{.*\}", re.DOTALL), re.compile(r"\[.*\]", re.DOTALL)):
        m = pat.search(t)
        if m:
            try:
                return {"ok": True, "value": json.loads(m.group(0)),
                        "confidence": "high", "note": "json block parsed"}
            except json.JSONDecodeError:
                continue
    return {"ok": False, "value": None, "confidence": "low",
            "note": "no valid json — escalate to Rock"}


if __name__ == "__main__":
    # smoke tests
    v = parse_verdict("<think>hmm</think>\nLet me think about this post...\n\nVerdict: REVISE — the hook is weak.")
    assert v["value"] == "REVISE", v
    c = parse_caption('The caption is 180 characters.\n\n"Grok 4.7 drops and Congress wants to regulate AI. These people still fax."')
    assert "fax" in c["value"], c
    b = parse_bullets("Some intro\n- point one\n- point two\n")
    assert b["value"] == ["point one", "point two"], b
    j = parse_json_block('here you go\n{"a": 1}\nbye')
    assert j["value"] == {"a": 1}, j
    print("pc_parse smoke tests passed")
