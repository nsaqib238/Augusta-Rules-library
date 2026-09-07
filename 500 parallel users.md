# Augusta Search — Scaling Plan (2 VPS Now → Grow With Demand)

> **Last major update:** Status review — each checklist item marked **DONE** / **CODE READY** / **NOT DONE** / **DEFERRED**. Code-ready items still need commit, VPS pull, and apply.

### Status legend

| Mark | Meaning |
|------|---------|
| **DONE** | Live in production (or already owned: Supabase Pro, VPS1, etc.) |
| **CODE READY** | In repo (or local uncommitted) — not fully applied on VPS yet |
| **NOT DONE** | Still to do (ops, billing, or build) |
| **DEFERRED** | Intentionally later (e.g. claim “500 users”, pen test) — not blocked by code |
| **READY TO ADD** | No code blocker — create Contabo VPS + deploy same app + point LB; do when you want |

---

## Executive summary

**Budget note:** Soft launch works on **1–2 VPS**. You can **add more API VPS anytime** (create Contabo instance → deploy Augusta → shared Redis → load balancer). That was marked deferred only because it was not required for soft launch and was not provisioned yet — **not** because the app cannot scale.

### What two VPS can support (realistic soft launch)

| Scenario | Realistic capacity |
|---|---|
| Users logged in / browsing | Hundreds |
| Simultaneous Q&A in flight | ~80 (app cap), ~15 synthesizing at once |
| Practical concurrent askers | **~20–40** (e.g. ~30 people asking in a ~30s window) |
| Sustained Q&A completions | ~100–140/min **if** paid LLM limits allow |
| PDF jobs concurrent | 2 on API box; higher if VPS 2 is a dedicated PDF worker |

### Horizontal scale (extra API nodes) — **READY TO ADD** when you create VPS

App already supports multiple API nodes (shared Redis + Supabase). To add capacity:

1. **Provision** another Contabo VPS (create the machine — this is the main step)  
2. Deploy same Git commit  
3. **Copy the existing `backend/.env`** from VPS1 — **do not invent new app env vars** for “multi-API”  
4. Only adjust what must differ by host:  
   - `REDIS_URL` → VPS1’s Redis LAN/private IP (not `127.0.0.1` on the new box)  
   - Optional: `UVICORN_PORT` if you change ports  
5. Put Cloudflare / nginx load balancer in front of node1 + node2 (+ node3…)  
6. Health-check `/health`  

**Env note:** Free-ask, answer cache, Gemini, Supabase keys stay the same on every API node. Scaling = **more VPS + same secrets**, not new feature flags in `.env`.

Then raise ask/LLM caps carefully and confirm **paid LLM** limits — extra VPS alone does not remove Gemini/OpenAI rate limits.

| Piece | Status |
|-------|--------|
| 2nd / 3rd / 4th **API** VPS | **READY TO ADD** (you create + deploy) |
| Load balancer (Cloudflare or nginx) | **NOT DONE** — needed once you have 2+ API nodes |
| Shared Redis (all API nodes same `REDIS_URL`) | **PARTIAL** — on VPS1 today; point new nodes at it (or move Redis to shared host) |
| Dedicated PDF worker VPS | **DEPLOY TODAY** — see README § D |
| Paid LLM tier | **NOT DONE** — still required or new nodes just hit 429 faster |
| Claim “500 parallel askers” | **DEFERRED** until load test passes |

**Supabase Pro** — **DONE**.

---

## Capacity targets by stage

### Stage A — Soft launch (1–2 VPS) — **current target** — **IN PROGRESS**

- Dozens of simultaneous Q&A users — **DONE** (capacity exists; not load-tested)  
- Hundreds browsing — **DONE** (capacity exists)  
- Free NCC + daily free-ask caps — **CODE READY** (needs VPS env if not already)  
- Paid Gemini (and/or OpenAI) — **NOT DONE** / **PARTIAL** (keys wired; paid tier / stable quota still required)  

### Stage B — Growth (add API VPS one at a time) — **READY TO ADD**

- Move PDF and/or Redis onto VPS 2 — **READY TO ADD** when you create/use 2nd VPS  
- Add 3rd VPS as second API node + LB — **READY TO ADD** (create Contabo + deploy)  

### Stage C — 500 parallel (future) — **DEFERRED** (claim only after test)

- 500 users online with sustained ~250 questions/min — **DEFERRED** until load test  

---

## Current production configuration (typical) — **DONE** on VPS1

```env
UVICORN_WORKERS=3
UVICORN_LIMIT_CONCURRENCY=96

ASK_MAX_CONCURRENT_PER_USER=2
ASK_GLOBAL_MAX_INFLIGHT=80

RAG_MAX_PARALLEL_PARCELS=4
RAG_GLOBAL_LLM_INFLIGHT=60

PDF_JOB_MAX_CONCURRENT=2
PDF_JOB_MAX_PER_USER=2

REDIS_URL=redis://127.0.0.1:6379/0
PDF_QUEUE_BACKEND=postgres

FREE_ASK_QUOTA_ENABLED=true
FREE_ASK_DAILY_GLOBAL_LIMIT=80
FREE_ASK_DAILY_PER_USER_LIMIT=5
FREE_ASK_TIMEZONE=Australia/Sydney

ANSWER_CACHE_ENABLED=true
ANSWER_CACHE_TTL_SECONDS=86400
```

| Component | Limit | Status |
|---|---:|---|
| Multi-worker API on Contabo | 3 workers | **DONE** |
| HTTPS / nginx / Certbot | live | **DONE** |
| Redis on VPS1 | local | **DONE** |
| Ask inflight caps | 80 / 2 per user | **DONE** |
| LLM slot cap | 60 | **DONE** |
| PDF concurrent | 2 | **DONE** |
| Free-ask daily budget | 80 / 5 | **CODE READY** |
| Answer cache | Redis 24h | **CODE READY** |
| Nginx LLM rate limits | ask / passcode / API | **CODE READY** (not installed on VPS) |
| 2nd VPS as PDF worker | — | **DEPLOY TODAY** (README § D) |

---

## Recommended 2-VPS layout

### Option 1 — Soft launch — **VPS 1 live; VPS 2 available**

```text
VPS 1 — API + nginx + Redis (stop local PDF worker after split) → DONE / UPDATE TODAY
VPS 2 — dedicated PDF worker                                    → DEPLOY TODAY
Supabase Pro                                                    → DONE
Gemini / OpenAI — paid LLM                                      → NOT DONE (paid tier)
Cloudflare (free)                                               → NOT DONE
```

**Deploy VPS 2 today:** follow **README → D. Deploy VPS2 as dedicated PDF worker**. Copy `.env` from VPS1, set `WORKER_MODE=pdf` + `EXTERNAL_PDF_WORKER=true`, enable `aus-augusta-pdf-worker` only, then disable the PDF worker on VPS1. Alternative later: second API node (needs LB + shared Redis). Do **one** role first.

### Option 2 — When uploads grow — **DEFERRED**

Split PDF to VPS 2 when queue/RAM hurts the API.

**Add VPS later (order)** — all **DEFERRED**:

1. Keep 2-VPS split healthy  
2. 3rd VPS — second API + shared Redis  
3. 4th VPS — more API or PDF  
4. Claim high parallel capacity only after load test  

---

## Phase 0 — Soft launch checklist

### 1. Paid LLM + free-ask budget

| Sub-item | Status |
|----------|--------|
| Gemini client + failover in code | **DONE** (in app) |
| `FREE_ASK_*` service + `/ask` enforcement | **CODE READY** (in repo; confirm VPS `.env` + restart) |
| Local `.env` has free-ask + Gemini models | **DONE** (dev machine) |
| VPS `.env` free-ask vars + restart | **NOT DONE** (confirm on server) |
| Google AI **paid** billing (not free-tier only) | **NOT DONE** |
| OpenAI topped up / usable backup | **NOT DONE** (was out of quota) |

### 2. Fix Supabase `auth_users_exposed`

| Sub-item | Status |
|----------|--------|
| `subscription_details` no `auth.users` join (in `combined_setup.sql`) | **DONE** in repo |
| `combined_setup.sql` updated (no `auth.users` join) | **CODE READY** |
| Run SQL in Supabase SQL Editor | **NOT DONE** |
| Security Advisor clear | **NOT DONE** |

### 3. Nginx rate limiting

| Sub-item | Status |
|----------|--------|
| `deploy/nginx/conf.d/aus-augusta-rate-limits.conf` | **CODE READY** |
| Site conf locations (ask / access-codes / API; webhooks exempt) | **CODE READY** |
| Copied to `/etc/nginx/` + `nginx -t` + reload on VPS | **NOT DONE** |
| Merged into Certbot HTTPS server block (if SSL separate) | **NOT DONE** |

### 4. Async Q&A

| Sub-item | Status |
|----------|--------|
| Async ask code exists | **DONE** |
| Polling verified end-to-end in production | **NOT DONE** |
| Recommendation if unverified | leave `ASK_ASYNC_*=false` — **OPS** |

### 5. PDF concurrency

| Sub-item | Status |
|----------|--------|
| Stay at `PDF_JOB_MAX_CONCURRENT=2` on single box | **DONE** |
| Raise on dedicated PDF VPS | **DEFERRED** |

### 6. Small load baseline (25 → 50 VU)

| Sub-item | Status |
|----------|--------|
| k6 / manual baseline recorded | **NOT DONE** |

---

## Phase 1 — Maximise the two VPS

| # | Item | Status |
|---|------|--------|
| 1 | Raise Gemini/OpenAI paid limits before raising LLM inflight | **NOT DONE** |
| 2 | Fewer parcels + golden-set quality test | **NOT DONE** (optional) |
| 3 | Shared-library answer cache (`answer_cache.py`) | **CODE READY** — enable on VPS Redis |
| 4 | Basic monitoring / alerts (LLM 429, free quota, PDF queue) | **NOT DONE** |
| 5 | Cloudflare free on hostname | **NOT DONE** |

---

## Phase 2 — Add API VPS (whenever you create them) — **READY TO ADD**

Not blocked by application code. When you create Contabo VPS:

| Step | Add | Status |
|------|-----|--------|
| 2a | 2nd VPS as PDF worker **or** 2nd API | **READY TO ADD** |
| 2b | All API nodes use same `REDIS_URL` | **READY TO ADD** |
| 2c | Load balancer in front of API nodes | **NOT DONE** (set up with 2nd API) |
| 2d | 3rd / 4th API for burst | **READY TO ADD** |

Still required in parallel: **paid LLM limits**, or more nodes only amplify 429s.

---

## Phase 3 — 500 parallel users — **DEFERRED**

| Item | Status |
|------|--------|
| 3–4 API nodes | **DEFERRED** |
| Mixed 500-user load test | **DEFERRED** |
| Node failure test | **DEFERRED** |
| Advertise “500 parallel” | **DEFERRED** (do not claim yet) |

---

## Security

| Item | Status |
|------|--------|
| RLS, plan checks, Stripe webhooks, HTTPS, sessions, CORS | **DONE** |
| Fix `auth_users_exposed` | **CODE READY** → run SQL (**NOT DONE**) |
| Nginx rate limits | **CODE READY** → install (**NOT DONE**) |
| Free-ask caps | **CODE READY** → confirm VPS (**NOT DONE**) |
| Cloudflare | **NOT DONE** |
| Backup restore drill / pen test | **DEFERRED** |

---

## Cost / accounts

| Item | Status |
|------|--------|
| Contabo VPS 1 (API + nginx + Redis) | **DONE** — production |
| Contabo VPS 2 | **AVAILABLE** — assign as PDF worker (preferred) or 2nd API |
| Supabase Pro | **DONE** |
| Modal | **DONE** (in use for uploads) |
| Stripe | **DONE** (configured) |
| Gemini paid | **NOT DONE** |
| OpenAI healthy quota | **NOT DONE** |
| Cloudflare | **NOT DONE** |

---

## Implementation order — live tracker

1. Paid Gemini + free-ask on VPS — **CODE READY** / billing **NOT DONE**  
2. Re-run `supabase/combined_setup.sql` (includes `subscription_details` auth_users_exposed fix) — **NOT DONE** on VPS DB if Advisor still flags it  
3. Install nginx rate limits + reload — **NOT DONE**  
4. Enable `ANSWER_CACHE_*` on VPS — **CODE READY** / deploy **NOT DONE**  
5. Cloudflare free — **NOT DONE**  
6. Metrics / alerts — **NOT DONE**  
7. PDF or 2nd API on new VPS — **READY TO ADD** when you create it  
8. 3rd API VPS + load balancer — **READY TO ADD**  
9. Shared Redis URL on all API nodes — **READY TO ADD**  
10. Load-test to 500 — **DEFERRED** (after nodes exist)  
11. Advertise 500 capacity — **DEFERRED** until test passes  

**Also:** commit + push local CODE READY files, then `git pull` on VPS — **NOT DONE** (as of this review).

---

## Product expansion — offices & other countries

| Item | Status |
|------|--------|
| Strategy: multi-tenant on same VPS | **DONE** (documented) |
| RAG for private office docs (no fine-tune required) | **DONE** (architecture) |
| Strict tenant isolation productization for many offices | **PARTIAL** (user docs exist; full wrap.ai packaging **NOT DONE**) |
| Other-country libraries / subdomains | **NOT DONE** |
| Fine-tune Qwen as replace | **DEFERRED** / not recommended as blocker |

---

## Final recommendation

- **Now:** Soft-launch on **up to 2 VPS**. Expect **dozens of concurrent askers**, not 500.  
- **Next actions (this week):** commit/push → VPS pull → free-ask + answer-cache env → run auth SQL → install nginx limits → Cloudflare → paid Gemini.  
- **500 parallel askers:** **DEFERRED** until more VPS + load test.

The application does not need a rewrite. Grow servers with revenue; finish deploying CODE READY items before hard marketing.
