"""Validate Stripe/checkout redirect URLs against ALLOWED_ORIGINS."""
from __future__ import annotations

import os
from urllib.parse import urlparse


class RedirectUrlError(ValueError):
    """User-fixable redirect URL validation failure."""


def _allowed_origins() -> list[str]:
    raw = (os.getenv("ALLOWED_ORIGINS") or "").strip()
    if not raw or raw == "*":
        return []
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def validate_redirect_url(url: str, *, field_name: str = "redirect URL") -> str:
    """
    Ensure redirect URL uses http(s) and matches an entry in ALLOWED_ORIGINS.
    Raises RedirectUrlError when invalid.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        raise RedirectUrlError(f"{field_name} is required")

    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RedirectUrlError(f"Invalid {field_name}")

    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    allowed = _allowed_origins()
    if not allowed:
        raise RedirectUrlError(
            f"{field_name} rejected: set ALLOWED_ORIGINS in backend .env (no wildcard redirects)"
        )

    if origin not in allowed:
        raise RedirectUrlError(f"{field_name} must use an allowed application origin")

    return cleaned
