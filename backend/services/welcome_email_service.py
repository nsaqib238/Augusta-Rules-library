"""Customer welcome email (signup / first subscribe) — idempotent per profile."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from services.async_utils import run_blocking
from services.email_service import send_email, smtp_configured
from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "welcome_email.html"


def _truthy(name: str, default: bool = True) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def _frontend_base() -> str:
    return (os.getenv("FRONTEND_URL") or "https://ausstd.augustasearch.com").rstrip("/")


def _support_email() -> str:
    return (
        os.getenv("SUPPORT_EMAIL")
        or os.getenv("ADMIN_EMAIL")
        or "naajm@augustasearch.com"
    ).strip()


def _legal_urls() -> Dict[str, str]:
    base = _frontend_base()
    marketing = (os.getenv("MARKETING_SITE_URL") or "https://www.augustasearch.com").rstrip("/")
    return {
        "terms_url": (os.getenv("LEGAL_TERMS_URL") or f"{marketing}/terms.html").strip(),
        "privacy_url": (os.getenv("LEGAL_PRIVACY_URL") or f"{marketing}/privacy.html").strip(),
        "site_url": marketing,
        "cta_url": f"{base}/login",
        "logo_url": (
            os.getenv("WELCOME_EMAIL_LOGO_URL")
            or f"{base}/img/NewOfficetools.png"
        ).strip(),
    }


def _first_name(full_name: Optional[str], email: Optional[str]) -> str:
    if full_name and full_name.strip():
        return full_name.strip().split()[0]
    if email and "@" in email:
        local = email.split("@", 1)[0].strip()
        if local:
            return local[:1].upper() + local[1:]
    return "there"


def _render_template(values: Dict[str, str]) -> str:
    html = _TEMPLATE_PATH.read_text(encoding="utf-8")
    for key, val in values.items():
        html = html.replace("{{" + key + "}}", val)
    return html


def _plain_body(first_name: str, urls: Dict[str, str], support: str) -> str:
    return (
        f"Hi {first_name},\n\n"
        "Welcome to Augusta Search. Open your workspace here:\n"
        f"{urls['cta_url']}\n\n"
        f"Terms of use: {urls['terms_url']}\n"
        f"Privacy policy: {urls['privacy_url']}\n"
        f"Support: {support}\n\n"
        "IMPORTANT DISCLAIMER: Augusta Search is an AI assistance / research tool. "
        "It is NOT a substitute for professional engineering judgment, legal advice, "
        "or formal compliance review. Always verify citations against the official "
        "publication and confirm requirements on site before relying on any answer.\n\n"
        "— Augusta Search team\n"
    )


def _mark_sent(user_id: str) -> None:
    from datetime import datetime, timezone

    supabase = get_supabase_client()
    supabase.table("profiles").update(
        {"welcome_email_sent_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", user_id).execute()


async def maybe_send_welcome_email(
    user_id: str,
    *,
    reason: str = "account",
    force: bool = False,
) -> Dict[str, Any]:
    """
    Send welcome email once per user (unless force=True).
    Safe to call from signup, dashboard, and Stripe webhooks.
    """
    if not user_id:
        return {"sent": False, "reason": "missing_user_id"}

    if not _truthy("WELCOME_EMAIL_ENABLED", True):
        return {"sent": False, "reason": "disabled"}

    if not smtp_configured():
        logger.info("Welcome email skipped (SMTP not configured) user=%s reason=%s", user_id, reason)
        return {"sent": False, "reason": "smtp_not_configured"}

    def _load_profile() -> Optional[Dict[str, Any]]:
        supabase = get_supabase_client()
        res = (
            supabase.table("profiles")
            .select("id, email, full_name, welcome_email_sent_at, created_at")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    try:
        profile = await run_blocking(_load_profile)
    except Exception as e:
        # Column may not exist yet — retry without welcome_email_sent_at
        logger.warning("Profile load for welcome email failed (%s); retrying minimal select", e)

        def _load_minimal() -> Optional[Dict[str, Any]]:
            supabase = get_supabase_client()
            res = (
                supabase.table("profiles")
                .select("id, email, full_name, created_at")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            return rows[0] if rows else None

        profile = await run_blocking(_load_minimal)

    if not profile:
        return {"sent": False, "reason": "profile_not_found"}

    email = (profile.get("email") or "").strip()
    if not email:
        return {"sent": False, "reason": "no_email"}

    if not force and profile.get("welcome_email_sent_at"):
        return {"sent": False, "reason": "already_sent"}

    # Dashboard/client backstop: only for recent signups (avoid emailing every legacy user once SMTP is on)
    if not force and reason in ("client", "dashboard"):
        created_raw = profile.get("created_at")
        if created_raw:
            try:
                from datetime import datetime, timezone, timedelta

                created = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - created > timedelta(days=14):
                    return {"sent": False, "reason": "account_too_old"}
            except Exception:
                pass

    urls = _legal_urls()
    first = _first_name(profile.get("full_name"), email)
    support = _support_email()
    values = {
        "first_name": first,
        "support_email": support,
        **urls,
    }
    html = _render_template(values)
    text = _plain_body(first, urls, support)
    subject = "Welcome to Augusta Search"

    try:
        ok = await send_email(email, subject, text, html)
    except Exception as e:
        logger.warning("Welcome email send failed user=%s: %s", user_id, e)
        return {"sent": False, "reason": "send_failed", "error": str(e)}

    if not ok:
        return {"sent": False, "reason": "smtp_not_configured"}

    try:
        await run_blocking(_mark_sent, user_id)
    except Exception as e:
        logger.warning(
            "Welcome email sent but could not stamp welcome_email_sent_at user=%s: %s",
            user_id,
            e,
        )

    logger.info("Welcome email sent user=%s to=%s trigger=%s", user_id, email, reason)
    return {"sent": True, "reason": reason, "to": email}
