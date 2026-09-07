"""Admin email allowlist from ADMIN_EMAIL_ALLOWLIST env (comma-separated)."""
from __future__ import annotations

import os
from functools import lru_cache


@lru_cache
def get_admin_email_allowlist() -> frozenset[str]:
    """
    Emails granted admin API access without profiles.role.
    Unset or empty in production — use profiles.role=admin instead.
    """
    raw = (os.getenv("ADMIN_EMAIL_ALLOWLIST") or "").strip()
    if not raw:
        return frozenset()
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())
