# Augusta Search — Prelaunch sanity check

Work top to bottom. Mark each item `[ ]` → `[x]` when done. Tell Cursor when you finish a step (or hit a problem).

**Suggested order this week:** 1 → 3 → 4 → deploy partner → 5 → 6 → 11 → then 7–10. DeepSeek (2) after smoke. Pty Ltd track is separate.

---

## A. LLM / cost

### 1. Replace gpt-4o with gpt-4.1-mini (OpenAI fallback)
- [x] On VPS1 `backend/.env` set (or confirm):
  - `LLM_PRIMARY_PROVIDER=gemini` (if Gemini is primary)
  - `RAG_SYNTHESIS_MODEL=gpt-4.1-mini`
  - `RAG_ROUTER_MODEL=gpt-4.1-mini` (optional, recommended)
- [x] `sudo systemctl restart aus-augusta-backend`
- [x] Ask 2–3 test questions; confirm answers still OK

**Notes:** Done 2026-08-09 — VPS1 env updated + backend restarted.


### 2. Third option: DeepSeek V4 Pro
- [ ] **Defer** until after product smoke (needs new provider wiring)
- [ ] Later: API key, code support, JSON/compliance A/B test

**Notes:**
---
## B. Product / billing smoke

### 3. Temp $1 Company Small for testing
- [x] Stripe: create **$1 AUD / month** test price (or use test mode)
- [x] VPS: point `STRIPE_PRICE_COMPANY_SMALL` at that `price_...`
- [x] Restart backend
- [ ] **After testing:** restore real `$599` price ID + restart

**Notes:** Done 2026-08-09 — $1 Company Small live for smoke. Restore $599 after §4 (and partner smoke if needed).

### 4. Company owner + 2 employees (full loop)
Create owner email + two employee emails.

#### 4a. Subscribe and join
- [x] Owner buys Company (referral optional for this step)
- [x] Owner sees join code on **Company** panel
- [x] Both employees join with join code (own logins)
- [x] Owner sees members in Company panel
- [x] You see company + owner in **Admin → Companies**

#### 4b. Immediate cancel
- [x] Stripe portal / Dashboard: cancel **immediately**
- [x] Owner + employees → Sole / inactive
- [x] Company status → **inactive** in admin

#### 4c. Re-subscribe and docs
- [x] Owner subscribes again
- [x] Add employees again if needed
- [x] Upload PDF → all members see it
- [x] Ask questions (owner + employee)
- [x] Remove PDF
- [x] Owner removes employees
- [x] Cancel subscription again → Sole / inactive

**Notes:** Done 2026-08-09 — Tradecyrus owner + team1/team2. Cancel/Sole fix + company RAG search for members deployed during smoke.


### 5. Marketing partner + referral
Requires partner dashboard **deployed** + SQL `login_email` (in `combined_setup.sql`).

- [x] Supabase: ensure `referral_partners.login_email` exists (run combined_setup / partner section)
- [x] Deploy backend + frontend with Partner tab
- [x] Marketing person signs up (normal account)
- [x] Admin → Companies: create partner (name + **code** + **his login email**)
- [x] He opens Dashboard → **Partner** tab; sees code; list empty
- [x] Another company checks out with **his referral code**
- [x] Employees join; upload PDF; ask; remove PDF; remove employees
- [x] Cancel company subscription immediately
- [x] Partner page still shows company with status **inactive**

**Notes:** Done 2026-08-09 — Partner login_email + referral checkout; company library stamp/backfill in combined_setup (§6d).


### 6. Reset password
Uses Supabase Auth (`/forgot-password` → email → `/reset-password`).

- [x] Supabase Auth → URL config: add redirect `https://ausstd.augustasearch.com/reset-password` (and local if needed)
- [x] Deploy frontend with Forgot / Reset password pages
- [x] Login → **Forgot password?** → submit email
- [x] Email arrives; link opens reset page; set new password
- [x] Sign in with new password works

**Notes:** Done 2026-08-09 — redirect URL + live smoke OK.


---

## C. Professional operation (before real sales)

### 7. Customer welcome email (automated)
Backend sends once per user via SMTP (`POST /api/v1/account/welcome-email` + Stripe checkout webhook).

- [x] VPS `backend/.env`: set `SMTP_*`, `SUPPORT_EMAIL`, `LEGAL_TERMS_URL`, `LEGAL_PRIVACY_URL` (see `.env.example`)
- [x] Run latest `combined_setup.sql` (adds `profiles.welcome_email_sent_at`)
- [x] Deploy/restart backend (+ frontend for signup/dashboard trigger)
- [x] New signup (or first dashboard load) → welcome email arrives
- [x] Email includes: terms link, privacy link, support contact
- [x] Email includes disclaimer: AI assistance — **not** a substitute for professional compliance review

**Notes:** Done 2026-08-15 — Mailgun SMTP (`mg.augustasearch.com`); Gmail SMTP from VPS failed.


### 8. Legal pages current
Landing site lives in `Augusta-Site/` → `www.augustasearch.com`.

- [x] Privacy policy — `privacy.html` updated (Aug 2026)
- [x] Terms of use — `terms.html` updated (Aug 2026)
- [x] Refund / cancel language in Terms
- [x] Contact — `index.html#contact` + footer email
- [ ] Company info accurate (Pty Ltd / ABN when ready — scale track)
- [x] **No refund on cancel** — in Terms + Product Overview
- [x] Same **no-refund / cancel** wording in **Product Overview** (in-app)
- [x] Footer on landing pages: **Terms**, **Privacy**, **Disclaimer**

**Notes:** Done 2026-08-09 — deploy `Augusta-Site` to www + rebuild app frontend for AboutApp. Pty Ltd/ABN still later.


### 8b. Product Overview — cancel & refund (do not forget)
- [x] Product Overview mentions: cancel anytime via billing portal
- [x] Product Overview states: **no refund** if subscription is cancelled (paid period is not pro-rated / refunded unless required by law)
- [x] Align wording with Terms + Stripe Customer Portal settings (immediate cancel vs end of period)

**Notes:** Done 2026-08-09 — `/about-app` Cancel & refunds section + legal links.


### 9. Partner onboarding pack (ops)
- [ ] Company email for partner
- [ ] Email signature
- [ ] Digital business card
- [ ] One-pager
- [ ] Product brochure
- [ ] Demo account
- [ ] Short sales guide (targets, benefits, objections)
- [ ] Commission + responsibility note (written)

**Notes:**


### 10. Final polish
- [ ] Run benchmark Q&A (demo questions)
- [ ] Site pages consistent (prices, naming)
- [ ] Ship welcome email (item 7)
- [ ] Ready to present as professional operation

**Notes:**


### 11. Landing page prices
- [ ] Landing shows Company Small **$599**, Large **$1,099**, Professional **$49** (or current)
- [ ] Matches in-app `/pricing`

**Notes:**


---

## D. Legal and business migration (scale track — not blocking smoke)

### Company setup
- [ ] Register **Augusta Search Pty Ltd**
- [ ] Obtain **ABN** and **ACN**
- [ ] Open company bank account
- [ ] Company debit/credit card
- [ ] Update: website footer, terms, privacy, invoices, Stripe payouts

### Centralise under company
Move off personal accounts where possible (company email + 2FA):

- [ ] Stripe
- [ ] OpenAI
- [ ] Gemini
- [ ] Modal
- [ ] Supabase
- [ ] GitHub
- [ ] VPS hosting
- [ ] Domain / DNS
- [ ] Analytics
- [ ] Social accounts

### Business Assets Register
Simple spreadsheet columns: **service name | purpose | login email | owner | payment method | renewal date | 2FA | recovery details**

- [ ] Spreadsheet created and filled for all services above

**Notes:**


---

## Progress log

| Date | Step | Result |
|------|------|--------|
| 2026-08-09 | 1 | Done — gpt-4.1-mini on VPS1, backend restarted |
| 2026-08-09 | 5 | Done — marketing partner + referral smoke |
| 2026-08-09 | 6 | Done — forgot/reset password on live |
| 2026-08-09 | 8/8b | Landing legal pages + Product Overview cancel/no-refund |
| 2026-08-15 | 7 | Done — welcome email via Mailgun |

Tell Cursor: e.g. “Sanity 1 done” or “Stuck on 4b — company still active after cancel”.
