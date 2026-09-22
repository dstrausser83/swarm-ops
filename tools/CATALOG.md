# Free Tool Catalog

Free / open-source tools evaluated for the swarm. A tool earns an entry by
being tested against a real job — not by looking good on GitHub.

## How to read this

- **Status:** `deployed` (in production use) · `trial` (being evaluated) ·
  `rejected` (tried, didn't fit — reason noted so nobody retries blindly)
- **Runs on:** `vm` (the Linux agent host) · `pc` (the operator's Windows machine)
- **Offloads:** what paid-agent time or attention this removes

## Deployed

| Tool | License | Runs on | Offloads | Notes |
|---|---|---|---|---|
| Ollama + qwen3 | Apache-2.0 | pc | Drafting, synthesis, recon, reviews — all bulk text work | Local LLM; paid API is last-resort fallback only |
| OpenClaw gateway | Apache-2.0 | pc | Model-provider routing for agents | Hidden scheduled task; verify after every boot |

## Trial (researched 2026-09-22 — full survey in `survey-2026-09-22.md`)

| Tool | License | Runs on | Offloads | Notes |
|---|---|---|---|---|
| Postiz | AGPL-3.0 | vm | Entire queue/publisher/cross-post system — scheduling, per-platform formatting, retries, analytics | Self-hosted Buffer; agentic API for n8n/AI pipelines. Migrate one platform first. |
| n8n | Fair-code (Sustainable Use) | vm | Cron + bespoke Python glue: draft → QC → queue → publish → engagement as visual workflows with retries + run history | Migrate pipeline by pipeline, no big-bang. |
| LiteLLM | MIT (verify) | vm | Credit doctrine as config: Ollama-first → paid-fallback routing, per-agent budgets, fallback logging | Point every agent's LLM calls at one endpoint. |
| Mem0 + Qdrant | Apache-2.0 | vm | Agent long-term memory: what worked, voice corrections, mission outcomes. RAG over this repo. | Needs pruning discipline; local embeddings via Ollama to stay free. |
| browser-use | MIT | pc | Hardens flaky browser scripts (Gemini gen, social posting) — AI-driven recovery from UI drift | Keep Playwright for hot paths; use for the flaky ones. Local Ollama as brain to stay free. |
| Uptime Kuma | MIT | vm | Outage detection: watches Ollama tunnel, gateways, proxies — alerts instead of next-cron discovery | Afternoon to deploy; immediate payoff. Do this first. |

## Suggested adoption order

1. Uptime Kuma → 2. LiteLLM → 3. Postiz → 4. Mem0+Qdrant → 5. n8n →
   6. ComfyUI/InvokeAI (gated on PC GPU survey) → 7. browser-use → 8. Wiki.js/RAGFlow

## Rejected

| Tool | License | Reason |
|---|---|---|
| _(none yet)_ | | |

---
*Catalog discipline: every entry needs a date and a verdict. A tool without a
verdict is a rumor.*
