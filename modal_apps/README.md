# Modal apps

## PDF table extractor (`standards-pdf-extractor.py`)

GPU worker for table detection + Gemini vision extraction (Stages A–D). The backend calls this via `MODAL_ENDPOINT` and authenticates with `X-Modal-Secret`.

### One-time setup

```bash
pip install modal
modal token new
modal secret create gemini-api-key GEMINI_API_KEY="your-key"
# Optional if still using Groq fallback:
# modal secret create groq-api-key GROQ_API_KEY="your-key"
modal secret create modal-api-secret MODAL_API_SECRET="your-long-random-shared-secret"
```

Use a long random value for `MODAL_API_SECRET` (e.g. `openssl rand -hex 32`). The same value must be in `backend/.env`.

### Deploy

```bash
cd modal_apps
modal deploy standards-pdf-extractor.py
```

Copy the deployed web URL into `backend/.env`:

```env
MODAL_ENDPOINT=https://<your-modal-endpoint>
MODAL_API_SECRET=your-long-random-shared-secret
```

**Deploy order:** create/update the Modal secret → `modal deploy` → set backend env and restart API. Until the backend has `MODAL_API_SECRET`, extract calls return 401.

App name: `pdf-table-extractor-v3`

### Auth

| Endpoint | Auth |
|----------|------|
| `POST /extract`, `/extract_tables`, `/extract_clauses` | Required: header `X-Modal-Secret` |
| `GET /health` | Public (uptime only) |

`debug_secrets` has been removed.

### Recent fixes (in-repo)

- Multi-segment AS/NZS table numbers (`5.1.5`, `L.8.2.2`)
- Skip glossary/figure/bibliography regions before Gemini
- Skip Stage C/D when Stage B returns 0 data rows
- Defer `{page}.{index}` fallback until after Stage A metadata
- Require shared secret on extraction endpoints; remove secret-debug endpoint
