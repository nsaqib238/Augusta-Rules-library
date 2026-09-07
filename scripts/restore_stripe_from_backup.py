"""Copy Stripe/subscription stack from AUS-Augusta backup into parent project."""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "AUS-Augusta"

COPIES = [
    # Backend services
    ("backend/services/stripe_service.py", "backend/services/stripe_service.py"),
    ("backend/services/subscription_service.py", "backend/services/subscription_service.py"),
    ("backend/services/subscription_service_v2.py", "backend/services/subscription_service_v2.py"),
    ("backend/services/passcode_service.py", "backend/services/passcode_service.py"),
    ("backend/services/access_code_service.py", "backend/services/access_code_service.py"),
    ("backend/services/session_limit_service.py", "backend/services/session_limit_service.py"),
    # Backend API
    ("backend/api/v1/subscriptions.py", "backend/api/v1/subscriptions.py"),
    ("backend/api/v1/subscriptions_v2.py", "backend/api/v1/subscriptions_v2.py"),
    ("backend/api/v1/webhooks.py", "backend/api/v1/webhooks.py"),
    ("backend/api/v1/access_codes.py", "backend/api/v1/access_codes.py"),
    ("backend/middleware/subscription_check.py", "backend/middleware/subscription_check.py"),
    # Frontend
    ("frontend/src/hooks/useSubscription.ts", "frontend/src/hooks/useSubscription.ts"),
    ("frontend/src/hooks/useAccessCode.ts", "frontend/src/hooks/useAccessCode.ts"),
    ("frontend/src/pages/Pricing.tsx", "frontend/src/pages/Pricing.tsx"),
    ("frontend/src/pages/PricingV2.tsx", "frontend/src/pages/PricingV2.tsx"),
    ("frontend/src/pages/PricingStripe.tsx", "frontend/src/pages/PricingStripe.tsx"),
    ("frontend/src/pages/AccessCodePage.tsx", "frontend/src/pages/AccessCodePage.tsx"),
    (
        "frontend/src/components/subscription/StripeSubscriptionDetails.tsx",
        "frontend/src/components/subscription/StripeSubscriptionDetails.tsx",
    ),
    (
        "frontend/src/components/subscription/SubscriptionStatus.tsx",
        "frontend/src/components/subscription/SubscriptionStatus.tsx",
    ),
    (
        "frontend/src/components/pricing/StripePricingTable.tsx",
        "frontend/src/components/pricing/StripePricingTable.tsx",
    ),
    (
        "frontend/src/components/admin/PasscodeManagementPanel.tsx",
        "frontend/src/components/admin/PasscodeManagementPanel.tsx",
    ),
    ("frontend/src/types/stripe-pricing-table.d.ts", "frontend/src/types/stripe-pricing-table.d.ts"),
]


def main() -> None:
    for src_rel, dst_rel in COPIES:
        src = BACKUP / src_rel
        dst = ROOT / dst_rel
        if not src.is_file():
            print(f"SKIP missing: {src_rel}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"OK {dst_rel}")

    print("Billing SQL: use supabase/combined_setup.sql section 11 (single master script).")
    print("To refresh billing blocks from backup, re-run scripts/merge_billing_into_combined_setup.py after updating backup SQL.")


if __name__ == "__main__":
    main()
