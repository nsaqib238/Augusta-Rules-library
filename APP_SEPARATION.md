# Augusta-Rules-library vs Augusta-Australia — keep them isolated

These are **two products**. Mixing remotes, domains, ports, or env files caused live incidents (wrong git remote on VPS, Q&A blocked as “shared compliance library”, 502s).

| | **Augusta-Australia** (uploads) | **Augusta-Rules-library** (NCC/SIR) |
|--|--------------------------------|-------------------------------------|
| GitHub | `nsaqib238/Augusta-Australia` | `nsaqib238/Augusta-Rules-library` |
| VPS path | `.../projects/Augusta-Australia` | `.../projects/Augusta-Rules-library` |
| Site | `https://ausstd.augustasearch.com` | `https://library.augustasearch.com` |
| API port | `8082` | `8083` |
| systemd | `aus-augusta-backend` | `aus-rules-backend` |
| Frontend www | `/var/www/aus-augusta-frontend/` | `/var/www/aus-rules-frontend/` |
| Supabase | upload app project | **different** library project |
| Redis DB | `/0` | `/1` (if same Redis host) |
| Stripe | **own** account/webhook/prices (do not share) |

## Do not

- Point the Australia folder’s `git remote` at `Augusta-Rules-library` (or the reverse).
- Deploy this frontend build into `/var/www/aus-augusta-frontend/`.
- Copy `backend/.env` between apps without changing `SUPABASE_*`, `FRONTEND_URL`, `ALLOWED_ORIGINS`, `UVICORN_PORT`, `REDIS_URL`, Stripe secrets.
- Nest this repo inside the Australia working tree on the VPS (move to a sibling folder).

## Check before pull

```bash
pwd
git remote -v
grep -E '^(SUPABASE_URL|FRONTEND_URL|UVICORN_PORT|REDIS_URL)=' backend/.env
```
