"""Merge subscription_billing_migration.sql into combined_setup.sql (single source of truth)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMBINED = ROOT / "supabase" / "combined_setup.sql"
BILLING = ROOT / "supabase" / "subscription_billing_migration.sql"

combined = COMBINED.read_text(encoding="utf-8")
billing_lines = BILLING.read_text(encoding="utf-8").splitlines()
billing_body = "\n".join(billing_lines[90:]).strip()

start_marker = "-- ===========================================\n-- 11. SUBSCRIPTION / BILLING"
do_marker = "DO $$\nBEGIN\n    RAISE NOTICE"

start = combined.index(start_marker)
end = combined.index(do_marker)
prefix = combined[:start]
suffix = combined[end:]

extra_indexes = """
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_stripe_customer ON user_subscriptions(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_stripe_subscription ON user_subscriptions(stripe_subscription_id);

"""
grant_marker = "GRANT SELECT, INSERT, UPDATE, DELETE ON user_subscriptions TO service_role;"
if "idx_user_subscriptions_stripe_customer" not in prefix:
    prefix = prefix.replace(grant_marker, grant_marker + extra_indexes)

section11 = """-- ===========================================
-- 11. SUBSCRIPTION / BILLING (Stripe, passcodes, views)
-- Plans: sole (free, NCC+SIR) | professional (paid, all features)
-- Idempotent — safe to re-run with the rest of this file
-- ===========================================

""" + billing_body + "\n\n"

out = prefix + section11 + suffix
out = out.replace(
    "--           search_chunks_vector (incl. shared library), conversations, user_subscriptions.",
    "--           search_chunks_vector (incl. shared library), conversations, billing/Stripe, passcodes.",
)
out = out.replace(
    "    RAISE NOTICE '   Billing: profiles subscription columns + user_sessions (full Stripe: subscription_billing_migration.sql)';",
    "    RAISE NOTICE '   Billing: Stripe, passcodes, profiles plan columns, user_sessions, subscription views';",
)

COMBINED.write_text(out, encoding="utf-8", newline="\n")
BILLING.unlink()
print(f"Merged into {COMBINED.name} ({len(out.splitlines())} lines)")
print(f"Removed {BILLING.name}")
