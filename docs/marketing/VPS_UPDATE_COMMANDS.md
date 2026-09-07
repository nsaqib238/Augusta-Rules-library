# VPS update commands (backend + NCC env)

Run on the VPS as `ragadmin` after SSH:

```bash
ssh ragadmin@vmi2728224
```

Project path: `~/ragadmin/projects/AUS-Augusta`  
Backend service: `aus-augusta-backend`

---

## 1. Pull latest code

```bash
cd ~/ragadmin/projects/AUS-Augusta
git pull --rebase
```

---

## 2. Add NCC env vars (only if missing)

```bash
cd ~/ragadmin/projects/AUS-Augusta/backend

grep -q '^BENCHMARK_NCC_MIN_TEXT_LEN=' .env || cat >> .env << 'EOF'

#=======================================================
# NCC
#=======================================================
BENCHMARK_NCC_MIN_TEXT_LEN=120
BENCHMARK_NCC_MAX_ITEMS_PER_SOURCE=4
EOF
```

Check they’re set:

```bash
grep -E '^BENCHMARK_NCC_|^#.*NCC' .env
```

To change values later, edit by hand:

```bash
nano .env
```

**Note:** `BENCHMARK_NCC_MIN_TEXT_LEN` is used by the VPS when parsing NCC CSVs. `BENCHMARK_NCC_MAX_ITEMS_PER_SOURCE` is read on the **Modal** GPU worker; set it in the Modal app env too if you want to tune Q&A count per clause.

---

## 3. Install backend deps (if `requirements.txt` changed on pull)

```bash
cd ~/ragadmin/projects/AUS-Augusta/backend
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

---

## 4. Restart backend

```bash
sudo systemctl restart aus-augusta-backend
sudo systemctl status aus-augusta-backend --no-pager
```

---

## 5. Quick sanity check

```bash
grep -E '^BENCHMARK_GENERATION_ENABLED=|^BENCHMARK_MODAL_ENDPOINT=' ~/ragadmin/projects/AUS-Augusta/backend/.env | sed 's/=.*$/=…/'
```

```bash
sudo journalctl -u aus-augusta-backend -n 30 --no-pager
```

---

## 6. Live logs (follow — Ctrl+C to stop)

```bash
sudo journalctl -u aus-augusta-backend -f --no-pager
```

Benchmark / NCC only:

```bash
sudo journalctl -u aus-augusta-backend -f --no-pager | grep -E 'benchmark_generation|benchmark_qna|ncc_benchmark|Modal'
```

Last 200 lines then filter (no follow):

```bash
sudo journalctl -u aus-augusta-backend -n 200 --no-pager | grep -E 'benchmark_generation|benchmark_qna|ncc_benchmark'
```

---

## Frontend + admin UI (required when Admin panel looks “old”)

Restarting the backend does **not** update React. After `git pull`:

```bash
cd ~/ragadmin/projects/AUS-Augusta/frontend
npm install
npm run build
sudo cp -r build/* /var/www/aus-augusta-frontend/
sudo chown -R www-data:www-data /var/www/aus-augusta-frontend
sudo systemctl reload nginx
```

Hard refresh the browser (`Ctrl+Shift+R`) or use incognito.

See also [update vps.md](../update%20vps.md) for the full checklist.
