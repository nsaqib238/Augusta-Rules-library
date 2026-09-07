# Augusta Search — NCC & SIR

Q&A over shared **National Construction Code (NCC)** volumes and **Service & Installation Rules (SIR)** editions. Users ask questions; admins ingest the libraries. There is **no user PDF upload**.

A **simpler Q&A pipeline** rephrases questions with OpenAI, retrieves clauses with multi-signal RRF search, and answers in parcels.

See `ARCHIVED_SEARCH_METHODS.md` for the old full RAG architecture.

## What it does

| Role | Flow |
|------|------|
| **User** | Sign up / log in → **Q&A** → country → document type → one document → ask. Sole (free) or Professional (higher limits). |
| **Admin** | **Library** tab: add countries, types, and documents; ingest editions as CSV |

## Stack

- **Frontend:** React (`frontend/`)
- **Backend:** FastAPI (`backend/`)
- **Database / auth / storage:** Postgres (hosted database)
- **Scaling (S1+):** Redis, multi-worker uvicorn — see [Scaling — production checklist](#scaling--production-checklist-s0s1)

## Quick start

### 1. Database

Create a database project, then in the **SQL Editor** run **`supabase/combined_setup.sql`** only.

| Scenario | Run |
|----------|-----|
| **New or existing project** | `supabase/combined_setup.sql` (idempotent — safe to re-run) |
| **Wipe & redeploy** | `supabase/full_schema_reset.sql` then `combined_setup.sql` |
| **Production security (Stage 1)** | Re-run `combined_setup.sql` — see [Security hardening — Stage 1](#stage-1--supabase-rls--rpc-lockdown) |

There are no separate patch files. All schema (profiles, documents, codebook fields, chunks, chunk_embeddings, PDF queue, storage, RAG vector search, NCC/SIR shared library, Stripe/billing, passcodes, plus backup-compat chat/subscription tables) is in that one script. The script also includes **RLS hardening** (profile trigger, secure passcode RPCs, subscription view grants).

**Existing project missing NCC/SIR?** Re-run **`supabase/combined_setup.sql`** only (section 9 adds `is_shared_library`, shared-library RLS, and refreshes `search_chunks_vector`).

**Existing project missing the country library tree?** Re-run **`supabase/combined_setup.sql`** only (section 10b adds `library_countries`, types, catalog documents). Do **not** run `full_schema_reset.sql` if this Supabase is also used by the code-search app.

### 2. Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create `backend/.env`:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
OPENAI_API_KEY=sk-...
# Optional: skip local embedding model (vector search falls back to fuzzy proxy)
# DISABLE_CHUNK_EMBEDDINGS=true

# CORS + Stripe redirect validation (comma-separated origins; never use *)
ALLOWED_ORIGINS=http://localhost:3000
# Production example:
# ALLOWED_ORIGINS=https://app.example.com,https://www.example.com

# Admin emails granted API/UI admin without profiles.role (comma-separated; optional)
# Prefer profiles.role=admin in production; leave empty if all admins use DB role.
ADMIN_EMAIL_ALLOWLIST=you@example.com

# Stripe (required for paid subscriptions)
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_PROFESSIONAL=price_...
# Optional legacy price IDs if your Stripe catalog still uses them:
# STRIPE_PRICE_INDIVIDUAL_MONTHLY=price_...
# STRIPE_PRICE_SOLE_TRADER=price_...
```

```bash
uvicorn main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
cp env.example .env
# Set REACT_APP_SUPABASE_URL, REACT_APP_SUPABASE_ANON_KEY, REACT_APP_API_URL=http://localhost:8000
# Optional: REACT_APP_ADMIN_EMAIL_ALLOWLIST=you@example.com (must match backend ADMIN_EMAIL_ALLOWLIST)
npm install
npm start
```

Open http://localhost:3000

## Security hardening — Stages 1–4 (required before production)

These steps apply database, backend API, and config hygiene fixes documented in [`Security review`](Security%20review). Run stages in order. Restart the backend after Stages 1–4.

| Stage | What it fixes | Where |
|-------|----------------|--------|
| **1** | Profile self-escalation, passcode enumeration, billing data leaks | Supabase SQL + backend passcode services |
| **2** | Skipped | Modal PDF extract is **not used** in this product |
| **3** | Plan limits server-side, route shadowing, open redirect | Backend API (`query`, `documents`, `subscriptions`, `admin`) |
| **4** | Hard-coded admin email, build artifacts in repo, debug prints | `ADMIN_EMAIL_ALLOWLIST` env, `.gitignore` |

---

### Stage 1 — Supabase RLS & RPC lockdown

**Goal:** Stop users from elevating their own role/plan, enumerating passcodes, or reading other users’ billing data.

#### 1. Apply SQL (dev/staging first)

1. Open your Supabase project → **SQL Editor**.
2. Paste and run the full script: **`supabase/combined_setup.sql`** (idempotent — safe to re-run on existing projects).
3. Confirm no errors in the output panel.

This script now includes:

- Profile **trigger** — blocks client updates to `role`, `account_type`, `subscription_status`, billing fields
- **No broad SELECT** on `promo_passcodes` / `access_codes` for normal users
- **`redeem_promo_passcode`** and **`redeem_access_code`** RPCs (row-locked redemption)
- Auth checks on **`get_user_active_subscription`** and **`get_all_subscriptions_admin`**
- **Revoked** direct `SELECT` on subscription views for `authenticated` users

#### 2. Deploy backend (uses new RPCs)

The repo already calls the new RPCs from:

- `backend/services/passcode_service.py`
- `backend/services/access_code_service.py`

Restart the API after SQL is applied:

```bash
cd backend
# Windows: .venv\Scripts\activate
uvicorn main:app --reload --port 8000
```

**Order:** SQL first → then restart backend.

#### 3. Verify (quick checks)

| Test | Expected |
|------|----------|
| Normal user tries `UPDATE profiles SET role='admin'` via client | Fails |
| Normal user `SELECT * FROM promo_passcodes` | Denied / empty |
| Redeem a valid passcode in the app | Profile shows `professional` + expiry |

---

### Stage 2 — skipped (Modal not used)

This product does **not** upload PDFs. Admins ingest NCC/SIR as **CSV**. Do not deploy Modal or set `MODAL_ENDPOINT`.

---

### Stage 3 — Backend API enforcement

**Goal:** Enforce plan limits server-side; fix broken routes and unsafe inputs. Backend only — no Supabase changes if Stage 1 is done.

#### 1. Deploy

The repo already includes:

- Query plan gating (`check_question_limit`) and sole-user codebook restrictions on `/prepare`, `/search`, `/ask`
- Documents route order fix (`GET /search` before `GET /{document_id}`)
- Stripe redirect URL validation (`success_url`, `cancel_url`, `return_url` vs `ALLOWED_ORIGINS`)
- Upload progress requires auth; progress JSON is bound to the uploading user
- Admin schema creation validates `user_id` as UUID
- CORS defaults to localhost when `ALLOWED_ORIGINS` is unset (production must set it explicitly)

Restart the API:

```bash
cd backend
# Windows: .venv\Scripts\activate
uvicorn main:app --reload --port 8000
```

#### 2. Production CORS

Set `ALLOWED_ORIGINS` in `backend/.env` to every frontend origin (comma-separated). Do **not** use `*`.

```env
ALLOWED_ORIGINS=https://app.example.com,https://www.example.com
```

#### 3. Verify (quick checks)

| Test | Expected |
|------|----------|
| `GET /api/v1/documents/search?q=…` (authenticated) | Search results (not 404 / invalid UUID) |
| Sole user `POST /api/v1/query/ask` with non-NCC/SIR codebook | 403 |
| Stripe checkout with `success_url=https://evil.com` | 400 |
| Unauthenticated `GET /api/v1/uploads/progress?upload_id=…` | 401 |

---

### Stage 4 — Config & repo hygiene

**Goal:** Remove hard-coded admin identities; keep build artifacts out of git.

#### 1. Set admin allowlist (backend + frontend)

In `backend/.env`:

```env
ADMIN_EMAIL_ALLOWLIST=you@example.com,ops@example.com
```

In `frontend/.env` (must match for admin UI tabs):

```env
REACT_APP_ADMIN_EMAIL_ALLOWLIST=you@example.com,ops@example.com
```

Production should prefer `profiles.role = admin` in Supabase; leave allowlist empty if all admins use DB roles.

Restart backend and rebuild/restart frontend after changing env.

#### 2. Verify

| Check | Expected |
|-------|----------|
| `grep control_engr@hotmail.com` in source (not `frontend/build/`) | No matches in `.py` / `.tsx` |
| `git status` | `frontend/build/` not listed (ignored) |
| Ask an NCC/SIR question | No `print()` noise in backend logs |

---

### Stage 5 — Verification sign-off

```powershell
cd backend
.venv\Scripts\python.exe scripts\stage5_verification.py
```

Before production: confirm Stripe webhook writes subscription rows to the database (see **Residual risk** in [`Security review`](Security%20review)).

---

## Security hardening complete

All five stages are done. Re-run verification with the command above anytime.

---

## VPS production deploy

- **Routine updates:** [update vps.md](update%20vps.md)
- **New VPS migration:** [deploy/NEW_VPS_MIGRATION.md](deploy/NEW_VPS_MIGRATION.md) (scripts in `deploy/scripts/`)
- **Soft-launch / scale plan:** [`500 parallel users.md`](500%20parallel%20users.md)

### Production topology (1 Contabo VPS)

This product is NCC/SIR Q&A only — **no user PDF upload**, so **VPS 2 is not needed**.

| Role | Host | Runs |
|------|------|------|
| **VPS1** | Current Contabo (`ausstd.augustasearch.com`) | nginx, API, Redis |

Admin library ingest is CSV on VPS1. Do not deploy a dedicated PDF worker box or Modal.

**Extra API capacity later:** copy the same app + `.env` as VPS1 (point `REDIS_URL` at a shared Redis). Provision only when load tests say you need it.

### Scaling — production checklist (S0–S1)

Full plan: [`500 parallel users.md`](500%20parallel%20users.md)

| Component | Windows dev | Linux dev (Ubuntu) | **Ubuntu VPS (production)** |
|-----------|-------------|--------------------|-----------------------------|
| **Redis** | Skip OK | Optional (`apt install`) | **Required** |
| **Multi-worker API** | No — `uvicorn --reload` | Optional | **Yes** — `start-api.sh` |
| **PDF worker / VPS 2** | Not needed | Not needed | **Not needed** |
| **Modal** | Not needed | Not needed | **Not needed** (no PDF upload) |

---

#### A. Local dev — Windows

You do **not** need Redis. **Do not run `sudo apt` in PowerShell** — that is for Linux (Ubuntu) only.

```powershell
cd backend
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

`backend/.env` for local dev:
- Leave `REDIS_URL` unset
- Do **not** set `HEALTH_REQUIRE_REDIS=true`

Optional Redis on Windows: [Memurai](https://www.memurai.com/) or `docker run -d -p 6379:6379 redis:7`

---

#### B. Local dev — Linux (Ubuntu / Debian)

Same as Windows — Redis is **optional** for day-to-day coding:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Optional — test S1 locally on Ubuntu (same packages as the VPS):

```bash
sudo apt update
sudo apt install -y redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
redis-cli ping   # → PONG
```

Then in `backend/.env`: `REDIS_URL=redis://127.0.0.1:6379/0`

---

#### C. Production — Ubuntu VPS (Contabo)

SSH into the VPS, then run **all** steps below (`apt`, `systemctl`, `journalctl`).

##### C1. Install Redis

```bash
sudo apt update
sudo apt install -y redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
redis-cli ping   # → PONG
```

##### C2. Backend env (`backend/.env`)

Copy from `backend/.env.example`. On the VPS, set **production** values:

```env
# S0 — runtime (already in code; set explicitly in production)
ASK_EXECUTOR_MAX_WORKERS=32
OPENAI_TIMEOUT_SECONDS=60
OPENAI_MAX_RETRIES=2
SESSION_CHECK_CACHE_SECONDS=30
HEALTH_REQUIRE_OPENAI=false

# S1 — infrastructure scale
WORKER_MODE=api
UVICORN_WORKERS=3
UVICORN_PORT=8082
UVICORN_LIMIT_CONCURRENCY=96
REDIS_URL=redis://127.0.0.1:6379/0
ASK_MAX_CONCURRENT_PER_USER=2
ASK_GLOBAL_MAX_INFLIGHT=80
HEALTH_REQUIRE_REDIS=true
```

##### C3. Install Python deps (includes `redis`)

```bash
cd backend
source venv/bin/activate
pip install -r requirements.txt
```

##### C4. Deploy systemd services

`deploy/scripts/03-deploy-services.sh` installs on Ubuntu:

- **API** — `aus-augusta-backend.service` → runs `deploy/scripts/start-api.sh` (multi-worker)

Do **not** enable the PDF worker service. This product has no user PDF upload.

```bash
sudo bash deploy/scripts/03-deploy-services.sh
sudo systemctl disable --now aus-augusta-pdf-worker
```

Manual start (debug):

```bash
bash deploy/scripts/start-api.sh
```

##### C5. Verify S1 (on VPS1)

| Check | Command | Expected |
|-------|---------|----------|
| Redis | `redis-cli ping` | `PONG` |
| API health | `curl http://127.0.0.1:8082/health` | `{"status":"healthy"}` |
| Ready probe | `curl http://127.0.0.1:8082/health/ready` | `"redis":"ok"`, `"database":"ok"` |
| API service | `sudo systemctl status aus-augusta-backend` | `active (running)` |
| Logs | `journalctl -u aus-augusta-backend -n 50 --no-pager` | no crash loop |
| Ask NCC/SIR | App UI | Answer with citations |

Useful Ubuntu commands if something fails:

```bash
sudo systemctl restart aus-augusta-backend
sudo journalctl -u aus-augusta-backend -f
```

---

#### Scaling progress

| Stage | Status | Notes |
|-------|--------|-------|
| **S0** Runtime correctness | Done in repo | Ask thread pool, `/health/ready`, OpenAI timeouts |
| **S1** Infrastructure scale | Done in repo | Redis, multi-worker, rate limits |
| **S2** — DB hybrid search | Done in repo | Included in `combined_setup.sql`; set `HYBRID_SEARCH_ENABLED=true` on VPS |
| **S3** — LLM throughput | Done in repo | Defaults OK on VPS; tune `RAG_GLOBAL_LLM_INFLIGHT` with OpenAI limits |
| **S3b** — Async ask API | Done in repo | Set `ASK_ASYNC_ENABLED=true` + `ASK_JOB_WORKER_ENABLED=true` on VPS (needs Redis) |
| **S4–S5** | Not started | See soft-launch plan |

**Next scaling step:** load-test ask path; then optional extra API Contabo nodes (READY TO ADD). Soft-launch plan: [`500 parallel users.md`](500%20parallel%20users.md).

**S2 enable (VPS):** Re-run `supabase/combined_setup.sql` in Supabase SQL Editor (idempotent), then add `HYBRID_SEARCH_ENABLED=true` to `backend/.env`.

**S3 tuning (VPS):** `RAG_MAX_PARALLEL_PARCELS=4`, `RAG_GLOBAL_LLM_INFLIGHT=60` (see scaling plan §8–§9).

**S3b enable (VPS):** `ASK_ASYNC_ENABLED=true`, `ASK_ASYNC_DEFAULT=true`, `ASK_JOB_WORKER_ENABLED=true`. Asks return 202 + `job_id`; the frontend polls `GET /api/v1/query/ask/jobs/{id}` automatically. Local dev: leave unset — asks stay synchronous.

---

Billing schema is included in **`supabase/combined_setup.sql`** (section 11 — Stripe, passcodes, plan columns, views). Re-run that file in the database SQL editor if you see missing-column errors:

```sql
-- Database SQL Editor — re-run the full script (idempotent)
-- supabase/combined_setup.sql
```

| Plan | Access |
|------|--------|
| **Sole** (free, default) | NCC + SIR Q&A |
| **Professional** (Stripe) | Same libraries, higher limits, billing portal |
| **Professional** (passcode) | Admin-generated code — same access for 3 or 6 months, then Sole |

**Admin:** Dashboard → **Passcodes** tab → create batch (e.g. `PILOT-XXXXXX`).

**User:** **Pricing** → “Have a passcode?” (must be signed in). Old `/redeem-code` redirects there.

**Frontend routes:** `/pricing`, `/pricing-stripe`, `/subscription-success`, `/about-app`

**Dashboard:** Upgrade / Manage Plan, Billing Status panel, admin **Passcodes** tab.

**Backend routes:**

| Route | Purpose |
|-------|---------|
| `POST /api/v1/subscriptions/create-checkout-session` | Start Stripe checkout |
| `GET /api/v1/subscriptions/usage-stats` | Plan + usage for UI gating |
| `POST /api/v1/subscriptions/create-billing-portal-session` | Stripe customer portal |
| `POST /api/v1/webhooks/stripe` | Stripe webhook (set in Stripe Dashboard) |
| `POST /api/v1/access-codes/redeem` | Redeem passcode for trial/pro access |

**Stripe webhook:** point to `https://your-api/api/v1/webhooks/stripe` and subscribe to `checkout.session.completed`, `customer.subscription.*`, `invoice.*`.

**Frontend `.env`** (see `frontend/env.example`):

```env
REACT_APP_STRIPE_PUBLISHABLE_KEY=pk_test_...
```

To re-copy all billing files from backup, run `python scripts/restore_stripe_from_backup.py`.

## Library ingest

Admins upload **clause CSV** and **tables CSV** in the NCC / SIR library tabs. No PDF upload, so **Modal is not needed**.

## API surface

| Prefix | Purpose |
|--------|---------|
| `/api/v1/uploads` | Disabled (no user PDF upload) |
| `/api/v1/documents` | Document list / status |
| `/api/v1/query` | Ask: rephrase, search, answer |
| `/api/v1/admin` | Admin queue, NCC/SIR CSV ingest |
| `/api/v1/admin/tables` | Codebooks list, tables CSV upload |
| `/api/v1/subscriptions` | Stripe checkout, usage stats, billing portal |
| `/api/v1/access-codes` | Passcode redeem / admin |
| `/api/v1/webhooks` | Stripe webhooks |

## RAG pipeline logging

Every `/api/v1/query/*` call returns a **`trace`** object (and writes the same steps to the backend console):

| Step | What you see |
|------|----------------|
| `prepare_input` / `prepare_output` | Original question → LLM rephrased query, FTS/vector/fuzzy/heading terms |
| `search_input` | Queries sent to each retriever, chunk pool size |
| `search_signals` | Top hits per signal (fts_bm25, vector, fuzzy, heading, supabase_fts) |
| `search_rrf` | RRF merged ranking with scores |
| `search_selected` | Final top-K chunks chosen for the answer |
| `answer_parcel_send` / `answer_parcel_result` | Clauses in each LLM parcel + excerpt returned |
| `answer_merge` | Final synthesized answer preview |

**UI:** Dashboard → **Ask a question** → chat-style Q&A panel.

**Server logs:** lines prefixed `[RAG:step_name]` on stdout. Optional file log:

```env
BACKEND_DEBUG_LOG=true
BACKEND_DEBUG_LOG_PATH=backend_debug.log
```

## Schema: this project vs backup (`AUS-Augusta/`)

The backup `AUS-Augusta/supabase/combined_setup.sql` is ~3,800 lines and bundles the **old full RAG stack**. This repo keeps a **slim master script** (~900 lines) for upload + chunk RAG only.

| Area | Backup schema | This project (`supabase/combined_setup.sql`) |
|------|---------------|-----------------------------------------------|
| Core upload | profiles, documents, chunks, admin_queue | Same |
| Vector search | `chunk_embeddings`, `search_chunks_vector` | Same |
| Tables ingest | `standard_tables` (+ optional rows/columns) | `standard_tables` only |
| Chat UI memory | `conversations`, `conversation_messages` | **Included** (section 10) |
| Admin user list | `user_subscriptions` | **Included** (empty OK) |
| Legacy helper | `search_chunks_enhanced` | **Included** |
| Old global RAG | `clause_units`, `dataset_registry`, NCC tables | **Not included** — see `ARCHIVED_SEARCH_METHODS.md` |
| Community Q&A | `electrician_qa`, votes, similarity groups | Not included |
| Billing | Stripe products/prices, passcodes, payment history | **Included** in `combined_setup.sql` section 11 |
| Entities glossary | `entities` table | Not included |

If you need a table from the backup, copy its `CREATE TABLE` block into section 10 of `combined_setup.sql` (idempotent) rather than maintaining separate patch files.

Users with `profiles.role` in `admin`, `engineer`, or `inspector` see the **Admin ingest** tab. The hard-coded admin email in `DocumentProcessing` / backend checks can also grant access.

## Project layout

```
backend/
  main.py
  api/v1/          documents, query, admin, subscriptions
  services/        database client, CSV ingest, storage, redis, ask rate limits
deploy/
  scripts/start-api.sh # Multi-worker uvicorn launcher (S1)
  systemd/           aus-augusta-backend.service
frontend/
  src/
    pages/         SimpleLogin, SignupPage, Dashboard
    components/    AskQuestion, admin/SharedLibraryPanel
500 parallel users.md   Soft-launch / scale plan
ARCHIVED_SEARCH_METHODS.md
```
