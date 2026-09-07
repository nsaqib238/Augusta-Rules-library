"""
Check Stripe subscription sync for a user email.
Usage (from backend/): python scripts/stripe_status_check.py control_engr@hotmail.com
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_ROOT / ".env")


def main() -> None:
    email = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if not email:
        print("Usage: python scripts/stripe_status_check.py user@example.com")
        sys.exit(1)

    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        sys.exit(1)

    sb = create_client(url, key)
    profile = (
        sb.table("profiles")
        .select("id, email, account_type, subscription_status, stripe_customer_id")
        .eq("email", email)
        .single()
        .execute()
    )
    if not profile.data:
        print(f"No profile for {email}")
        sys.exit(1)

    p = profile.data
    user_id = p["id"]
    print(f"Profile: {p.get('email')}")
    print(f"  user_id: {user_id}")
    print(f"  account_type: {p.get('account_type')}")
    print(f"  subscription_status: {p.get('subscription_status')}")
    print(f"  stripe_customer_id: {p.get('stripe_customer_id')}")

    subs = (
        sb.table("user_subscriptions")
        .select("id, plan_name, status, stripe_subscription_id, current_period_end, cancel_at_period_end")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )
    rows = subs.data or []
    print(f"\nDB subscriptions ({len(rows)}):")
    if not rows:
        print("  (none — webhook may not have fired yet)")
    for row in rows:
        print(
            f"  - {row.get('stripe_subscription_id')} status={row.get('status')} "
            f"plan={row.get('plan_name')} cancel_at_period_end={row.get('cancel_at_period_end')}"
        )

    import stripe

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    cid = p.get("stripe_customer_id")
    if cid and stripe.api_key:
        print(f"\nStripe API (customer {cid}):")
        try:
            stripe_subs = stripe.Subscription.list(customer=cid, status="all", limit=5)
            if not stripe_subs.data:
                print("  (no subscriptions in Stripe)")
            for s in stripe_subs.data:
                print(f"  - {s.id} status={s.status} cancel_at_period_end={s.cancel_at_period_end}")
        except Exception as exc:
            print(f"  Stripe error: {exc}")


if __name__ == "__main__":
    main()
