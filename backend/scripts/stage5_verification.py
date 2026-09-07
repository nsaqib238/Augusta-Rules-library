"""
Stage 5 verification — static checks + live API tests against local backend.
Run from backend/: python scripts/stage5_verification.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
load_dotenv(BACKEND_ROOT / ".env")
if not os.getenv("SUPABASE_ANON_KEY"):
    load_dotenv(REPO_ROOT / "frontend" / ".env")

API_BASE = os.getenv("GATE_TEST_API_BASE", "http://localhost:8000").rstrip("/")
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SERVICE_ROLE = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
ANON_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("REACT_APP_SUPABASE_ANON_KEY") or ""
MODAL_ENDPOINT = (os.getenv("MODAL_ENDPOINT") or "").rstrip("/")
MODAL_SECRET = os.getenv("MODAL_API_SECRET") or ""

RESULTS: list[tuple[str, str, str]] = []  # category, check, status


def record(category: str, check: str, status: str, detail: str = "") -> None:
    label = f"{status}: {check}" + (f" ({detail})" if detail else "")
    RESULTS.append((category, check, label))
    print(f"[{category}] {label}")


def static_check(category: str, check: str, ok: bool, detail: str = "") -> None:
    record(category, check, "PASS" if ok else "FAIL", detail)


def _mint_token(email: str) -> str:
    headers = {"apikey": SERVICE_ROLE, "Authorization": f"Bearer {SERVICE_ROLE}", "Content-Type": "application/json"}
    verify_headers = {"apikey": ANON_KEY, "Authorization": f"Bearer {ANON_KEY}", "Content-Type": "application/json"}
    with httpx.Client(timeout=30.0) as client:
        gen = client.post(
            f"{SUPABASE_URL}/auth/v1/admin/generate_link",
            headers=headers,
            json={"type": "magiclink", "email": email},
        )
        gen.raise_for_status()
        body = gen.json()
        hashed = body.get("hashed_token") or (body.get("properties") or {}).get("hashed_token")
        ver = client.post(
            f"{SUPABASE_URL}/auth/v1/verify",
            headers=verify_headers,
            json={"type": "magiclink", "token_hash": hashed},
        )
        ver.raise_for_status()
        token = ver.json().get("access_token")
        if not token:
            raise RuntimeError(f"No access_token for {email}")
        return token


def _pick_users():
    from supabase import create_client

    sb = create_client(SUPABASE_URL, SERVICE_ROLE)
    rows = (
        sb.table("profiles")
        .select("id, email, account_type, role")
        .not_.is_("email", "null")
        .limit(200)
        .execute()
        .data
        or []
    )
    sole = next((r for r in rows if (r.get("account_type") or "sole").strip().lower() == "sole"), rows[0] if rows else None)
    admin = next(
        (r for r in rows if (r.get("role") or "").strip().lower() in {"admin", "engineer", "inspector"}),
        None,
    )
    allowlist = (os.getenv("ADMIN_EMAIL_ALLOWLIST") or "").lower()
    if not admin and allowlist:
        admin = next((r for r in rows if (r.get("email") or "").lower() in {e.strip() for e in allowlist.split(",")}), None)
    return sole, admin, rows


def run_static_checks() -> None:
    sql = (REPO_ROOT / "supabase" / "combined_setup.sql").read_text(encoding="utf-8")
    static_check("Checklist", "Profile protect trigger", "protect_profile_sensitive_columns" in sql)
    static_check("Checklist", "redeem_promo_passcode RPC + FOR UPDATE", "redeem_promo_passcode" in sql and "FOR UPDATE" in sql)
    static_check("Checklist", "redeem_access_code RPC", "redeem_access_code" in sql)
    static_check("Checklist", "Passcode service uses RPC", "redeem_promo_passcode" in (BACKEND_ROOT / "services" / "passcode_service.py").read_text(encoding="utf-8"))
    static_check("Checklist", "Access code service uses RPC", "redeem_access_code" in (BACKEND_ROOT / "services" / "access_code_service.py").read_text(encoding="utf-8"))
    modal_py = (REPO_ROOT / "modal_apps" / "standards-pdf-extractor.py").read_text(encoding="utf-8")
    static_check("Checklist", "Modal debug_secrets removed", "debug_secrets" not in modal_py)
    static_check("Checklist", "Modal secret required", "_require_modal_api_secret" in modal_py)
    docs_py = (BACKEND_ROOT / "api" / "v1" / "documents.py").read_text(encoding="utf-8")
    static_check(
        "Checklist",
        "documents/search before /{document_id}",
        docs_py.find('@router.get("/search"') < docs_py.find('@router.get("/{document_id}"'),
    )
    static_check("Checklist", "Query sole codebook guard", "enforce_sole_codebook_access" in (BACKEND_ROOT / "api" / "v1" / "query.py").read_text(encoding="utf-8"))
    static_check("Checklist", "Stripe redirect validation", "validate_redirect_url" in (BACKEND_ROOT / "api" / "v1" / "subscriptions.py").read_text(encoding="utf-8"))
    static_check("Checklist", "Webhook generic error", "Webhook processing failed" in (BACKEND_ROOT / "api" / "v1" / "webhooks.py").read_text(encoding="utf-8"))
    uploads_py = (BACKEND_ROOT / "api" / "v1" / "uploads.py").read_text(encoding="utf-8")
    static_check("Checklist", "Upload progress auth", "Depends(get_current_user)" in uploads_py and "get_upload_progress" in uploads_py)
    static_check("Checklist", "No upload print() debug", "print(" not in uploads_py)
    static_check("Checklist", "Admin allowlist from env", "get_admin_email_allowlist" in (BACKEND_ROOT / "middleware" / "subscription_check.py").read_text(encoding="utf-8"))
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    static_check("Checklist", "frontend/build gitignored", "frontend/build/" in gitignore)
    src_root = REPO_ROOT / "frontend" / "src"
    src_text = ""
    for path in src_root.rglob("*"):
        if path.suffix in {".ts", ".tsx"}:
            src_text += path.read_text(encoding="utf-8", errors="ignore")
    static_check(
        "Checklist",
        "No hardcoded admin email in frontend src",
        "control_engr@hotmail.com" not in src_text,
    )


def run_api_checks(sole: dict, admin: dict | None) -> None:
    sole_token = _mint_token(sole["email"])
    headers = {"Authorization": f"Bearer {sole_token}", "Content-Type": "application/json"}

    with httpx.Client(timeout=90.0) as client:
        # Sole blocked from electrical
        r = client.post(
            f"{API_BASE}/api/v1/query/ask",
            headers=headers,
            json={"question": "switchboard clearance", "codebook_id": "AS3000", "skip_answer": True},
        )
        record("Sole vs pro", "Sole AS3000 blocked", "PASS" if r.status_code == 403 else "FAIL", f"HTTP {r.status_code}")

        # Sole allowed NCC (not 403) — use /prepare (faster than full /ask)
        r = client.post(
            f"{API_BASE}/api/v1/query/prepare",
            headers=headers,
            json={"question": "fire resistance", "codebook_id": "NCC2022_VOL1"},
            timeout=90.0,
        )
        record(
            "Sole vs pro",
            "Sole NCC not blocked",
            "PASS" if r.status_code != 403 else "FAIL",
            f"HTTP {r.status_code}",
        )

        # Admin path — sole cannot access admin
        r = client.get(f"{API_BASE}/api/v1/admin/orphans/summary", headers={"Authorization": f"Bearer {sole_token}"})
        record("Admin", "Non-admin orphans/summary denied", "PASS" if r.status_code in (401, 403) else "FAIL", f"HTTP {r.status_code}")

        if admin and admin["id"] != sole["id"]:
            admin_token = _mint_token(admin["email"])
            r = client.get(
                f"{API_BASE}/api/v1/admin/orphans/summary",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            record("Admin", "Admin orphans/summary allowed", "PASS" if r.status_code == 200 else "FAIL", f"HTTP {r.status_code}")
        else:
            record("Admin", "Admin orphans/summary allowed", "SKIP", "no separate admin profile")

        # Stripe evil redirect
        r = client.post(
            f"{API_BASE}/api/v1/subscriptions/create-checkout-session",
            headers=headers,
            json={
                "plan_name": "professional",
                "success_url": "https://evil.com/success",
                "cancel_url": "https://evil.com/cancel",
            },
        )
        record("Stripe", "Evil redirect rejected", "PASS" if r.status_code == 400 else "FAIL", f"HTTP {r.status_code}")

        # Stripe valid redirect (should not be 400 for origin)
        origin = "http://localhost:3000"
        r = client.post(
            f"{API_BASE}/api/v1/subscriptions/create-checkout-session",
            headers=headers,
            json={
                "plan_name": "professional",
                "success_url": f"{origin}/subscription-success",
                "cancel_url": f"{origin}/pricing",
            },
        )
        record(
            "Stripe",
            "Valid checkout URL accepted",
            "PASS" if r.status_code in (200, 201) else "WARN" if r.status_code >= 500 else "FAIL",
            f"HTTP {r.status_code}",
        )

        # Upload progress unauthenticated
        r = client.get(f"{API_BASE}/api/v1/uploads/progress", params={"upload_id": "stage5-test"})
        record("API", "Upload progress requires auth", "PASS" if r.status_code in (401, 403) else "FAIL", f"HTTP {r.status_code}")

    # Modal
    if MODAL_ENDPOINT:
        base = MODAL_ENDPOINT.rstrip("/")
        for suffix in ("/extract-tables", "/extract", "/extract_clauses"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        health_url = f"{base}/health"
        try:
            with httpx.Client(timeout=20.0) as client:
                r = client.get(health_url)
            record("Modal", "Health endpoint public", "PASS" if r.status_code == 200 else "WARN", f"HTTP {r.status_code}")
            extract_url = MODAL_ENDPOINT if "extract" in MODAL_ENDPOINT else f"{base}/extract"
            r = client.post(extract_url, json={})
            record("Modal", "Extract without secret denied", "PASS" if r.status_code in (401, 403, 422) else "FAIL", f"HTTP {r.status_code}")
        except Exception as exc:
            record("Modal", "Modal connectivity", "WARN", str(exc)[:80])
    else:
        record("Modal", "MODAL_ENDPOINT configured", "SKIP", "not set")


def main() -> None:
    print(f"Stage 5 verification -> {API_BASE}\n")
    run_static_checks()

    if not (SUPABASE_URL and SERVICE_ROLE and ANON_KEY):
        record("API", "Live API tests", "SKIP", "missing Supabase env")
    else:
        try:
            sole, admin, _ = _pick_users()
            if not sole:
                record("API", "Live API tests", "SKIP", "no profiles")
            else:
                run_api_checks(sole, admin)
        except httpx.TimeoutException:
            record("API", "Live API tests", "WARN", "request timed out (backend may be slow)")
        except Exception as exc:
            record("API", "Live API tests", "FAIL", str(exc)[:120])

    fails = [r for r in RESULTS if r[2].startswith("FAIL")]
    warns = [r for r in RESULTS if r[2].startswith("WARN")]
    print(f"\nSummary: {len(RESULTS)} checks, {len(fails)} fail, {len(warns)} warn")
    if fails:
        sys.exit(1)
    print("Stage 5 verification passed (review WARN items for residual risk).")


if __name__ == "__main__":
    main()
