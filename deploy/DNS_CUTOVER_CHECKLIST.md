# DNS cutover checklist — ausstd.augustasearch.com → 37.60.238.78

## Before cutover

- [ ] Steps 1–4 complete on new VPS (`deploy/NEW_VPS_MIGRATION.md`)
- [ ] `curl http://127.0.0.1:8082/health` returns healthy on new server
- [ ] Preflight script passes
- [ ] Optional: hosts-file test from PC works

## Cutover

- [ ] Lower DNS TTL to 300s (if your provider allows) — wait old TTL to expire
- [ ] Change **A record** `ausstd.augustasearch.com` → `37.60.238.78`
- [ ] Wait 5–30 minutes; verify: `nslookup ausstd.augustasearch.com`

## After DNS propagates

- [ ] Run on new VPS: `sudo bash deploy/scripts/05-certbot-ssl.sh`
- [ ] `https://ausstd.augustasearch.com` loads
- [ ] Login works (Supabase)
- [ ] API works (dashboard loads data)
- [ ] PDF upload test (Modal)
- [ ] Stripe checkout opens (live/test as configured)

## Rollback (if needed)

- [ ] Restore A record to old IP `213.199.49.234`
- [ ] On old VPS: `sudo systemctl start aus-augusta-backend`

## Retire old (24–48h later)

- [ ] On old VPS: `bash deploy/scripts/06-retire-old-augusta.sh`
