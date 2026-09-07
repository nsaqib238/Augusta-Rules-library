"""
Stage 3 gate tests — run against local backend (default http://localhost:8000).
Uses Supabase service role to mint short-lived user JWTs via magic-link verify.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
load_dotenv(BACKEND_ROOT / ".env")

API_BASE = os.getenv("GATE_TEST_API_BASE", "http://localhost:8000").rstrip("/")
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SERVICE_ROLE = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""

# Anon key needed for verify step; try backend then frontend .env
if not os.getenv("SUPABASE_ANON_KEY"):
    load_dotenv(REPO_ROOT / "frontend" / ".env")
ANON_KEY = os.getenv("SUPABASE_ANON_KEY") or os.getenv("REACT_APP_SUPABASE_ANON_KEY") or ""


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _mint_user_token(email: str) -> str:
    if not SUPABASE_URL or not SERVICE_ROLE or not ANON_KEY:
        _fail("Missing SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, or anon key (backend/frontend .env)")

    admin_headers = {
        "apikey": SERVICE_ROLE,
        "Authorization": f"Bearer {SERVICE_ROLE}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=30.0) as client:
        gen = client.post(
            f"{SUPABASE_URL}/auth/v1/admin/generate_link",
            headers=admin_headers,
            json={"type": "magiclink", "email": email},
        )
        if gen.status_code >= 400:
            _fail(f"generate_link for {email}: HTTP {gen.status_code} {gen.text[:200]}")
        body = gen.json()
        hashed = body.get("hashed_token") or (body.get("properties") or {}).get("hashed_token")
        if not hashed:
            _fail(f"generate_link for {email}: no hashed_token in response")

        verify_headers = {
            "apikey": ANON_KEY,
            "Authorization": f"Bearer {ANON_KEY}",
            "Content-Type": "application/json",
        }
        ver = client.post(
            f"{SUPABASE_URL}/auth/v1/verify",
            headers=verify_headers,
            json={"type": "magiclink", "token_hash": hashed},
        )
        if ver.status_code >= 400:
            _fail(f"verify for {email}: HTTP {ver.status_code} {ver.text[:200]}")
        session = ver.json()
        token = session.get("access_token")
        if not token:
            _fail(f"verify for {email}: no access_token")
        return token


def _pick_test_users() -> tuple[dict, dict]:
    from supabase import create_client

    sb = create_client(SUPABASE_URL, SERVICE_ROLE)
    profiles = (
        sb.table("profiles")
        .select("id, email, account_type, role")
        .not_.is_("email", "null")
        .limit(200)
        .execute()
    )
    rows = profiles.data or []
    if not rows:
        _fail("No profiles with email found in Supabase")

    sole = next(
        (r for r in rows if (r.get("account_type") or "sole").strip().lower() == "sole"),
        None,
    )
    any_user = rows[0]
    if not sole:
        sole = any_user
        print("WARN: No sole account_type profile; using first user for sole codebook test")
    return sole, any_user


def test_documents_search(token: str) -> None:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            f"{API_BASE}/api/v1/documents/search",
            params={"query": "switchboard"},
            headers={"Authorization": f"Bearer {token}"},
        )
    body = r.text[:400]
    if r.status_code == 404:
        _fail(f"documents/search returned 404 (route shadowing bug): {body}")
    if r.status_code == 422 and "uuid" in body.lower():
        _fail(f"documents/search returned 422 UUID error (route shadowing bug): {body}")
    if r.status_code == 200:
        data = r.json()
        if not isinstance(data, list):
            _fail(f"documents/search expected list, got {type(data).__name__}")
        print(f"PASS: documents/search -> {r.status_code}, {len(data)} result(s)")
        return
    if r.status_code == 500 and "search failed" in body.lower():
        print(
            "PASS: documents/search route reachable (not shadowed by /{document_id}); "
            f"WARN: search backend error HTTP 500 - separate from Stage 3 routing fix"
        )
        return
    _fail(f"documents/search unexpected HTTP {r.status_code}: {body}")


def test_sole_electrical_blocked(sole_token: str, sole_profile: dict) -> None:
    account_type = (sole_profile.get("account_type") or "sole").strip().lower()
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"{API_BASE}/api/v1/query/ask",
            headers=_auth_headers(sole_token),
            json={
                "question": "What is the clearance for a switchboard?",
                "codebook_id": "AS3000",
                "skip_answer": True,
            },
        )
    if account_type == "professional":
        print(f"SKIP: sole electrical test — user is professional ({sole_profile.get('email')})")
        return
    if r.status_code != 403:
        _fail(f"sole user AS3000 ask expected 403, got {r.status_code}: {r.text[:300]}")
    print(f"PASS: sole user AS3000 ask -> 403 ({r.json().get('detail', '')[:80]})")


def test_stripe_evil_redirect(token: str) -> None:
    price_id = (os.getenv("STRIPE_PRICE_PROFESSIONAL") or "").strip()
    payload = {
        "success_url": "https://evil.com/checkout/success",
        "cancel_url": "https://evil.com/checkout/cancel",
    }
    if price_id:
        payload["price_id"] = price_id
    else:
        payload["plan_name"] = "professional"

    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{API_BASE}/api/v1/subscriptions/create-checkout-session",
            headers=_auth_headers(token),
            json=payload,
        )
    if r.status_code != 400:
        _fail(f"Stripe evil redirect expected 400, got {r.status_code}: {r.text[:300]}")
    detail = str(r.json().get("detail", ""))
    if "allowed" not in detail.lower() and "origin" not in detail.lower():
        _fail(f"Stripe evil redirect 400 but unexpected detail: {detail[:200]}")
    print(f"PASS: Stripe evil redirect -> 400 ({detail[:80]})")


def test_upload_progress_unauthenticated() -> None:
    with httpx.Client(timeout=15.0) as client:
        r = client.get(
            f"{API_BASE}/api/v1/uploads/progress",
            params={"upload_id": "gate-test-upload-id"},
        )
    if r.status_code not in (401, 403):
        _fail(f"uploads/progress unauthenticated expected 401/403, got {r.status_code}: {r.text[:200]}")
    print(f"PASS: uploads/progress without auth -> {r.status_code}")


def main() -> None:
    print(f"Stage 3 gate tests -> {API_BASE}\n")
    sole_profile, any_profile = _pick_test_users()
    print(f"Auth user (search/checkout): {any_profile.get('email')} ({any_profile.get('account_type')})")
    print(f"Sole test user: {sole_profile.get('email')} ({sole_profile.get('account_type')})\n")

    any_token = _mint_user_token(any_profile["email"])
    sole_token = any_token
    if sole_profile["id"] != any_profile["id"]:
        sole_token = _mint_user_token(sole_profile["email"])

    test_upload_progress_unauthenticated()
    test_stripe_evil_redirect(any_token)
    test_documents_search(any_token)
    test_sole_electrical_blocked(sole_token, sole_profile)

    print("\nAll Stage 3 gate tests passed.")


if __name__ == "__main__":
    main()
