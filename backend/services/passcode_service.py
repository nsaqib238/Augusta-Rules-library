"""
Passcode subscription service – redeem promo passcodes for professional trial.
Professional access until access_expires_at; then user falls back to sole (handled in subscription_service).
"""
import logging
import secrets
import string
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

# Rate limit: max redeem attempts per user per minute (simple in-memory; use Redis in production if needed)
_redeem_attempts: Dict[str, List[float]] = {}
_REDEEM_RATE_LIMIT = 5
_REDEEM_WINDOW_SEC = 60


def _check_rate_limit(user_id: str) -> None:
    """Raise if user has exceeded redeem rate limit."""
    now = datetime.now(timezone.utc).timestamp()
    if user_id not in _redeem_attempts:
        _redeem_attempts[user_id] = []
    attempts = _redeem_attempts[user_id]
    attempts[:] = [t for t in attempts if now - t < _REDEEM_WINDOW_SEC]
    if len(attempts) >= _REDEEM_RATE_LIMIT:
        raise ValueError("Too many redemption attempts. Please try again later.")
    attempts.append(now)


def _rpc_error_message(exc: Exception) -> str:
    """Extract a human-readable message from a Supabase/PostgREST RPC error."""
    if hasattr(exc, "message") and exc.message:
        return str(exc.message)
    if getattr(exc, "args", None):
        return str(exc.args[0])
    return str(exc)


def redeem_passcode(user_id: str, code: str) -> Dict[str, Any]:
    """
    Redeem a passcode for the given user. Grants professional access until access_expires_at.
    Returns dict with success, access_expires_at (iso), message. Raises ValueError on validation failure.
    """
    _check_rate_limit(user_id)
    code = (code or "").strip()
    if not code:
        raise ValueError("Passcode is required.")

    supabase = get_supabase_client()

    try:
        result = supabase.rpc(
            "redeem_promo_passcode",
            {"p_code": code, "p_user_id": user_id},
        ).execute()
    except Exception as exc:
        msg = _rpc_error_message(exc)
        logger.warning("Passcode redeem failed for user %s: %s", user_id, msg)
        if "Passcode is required" in msg:
            raise ValueError("Passcode is required.") from exc
        if "already been used" in msg or "already used" in msg:
            raise ValueError("You have already used this passcode.") from exc
        if "not yet valid" in msg:
            raise ValueError("This passcode is not yet valid.") from exc
        if "Not authenticated" in msg or "Access denied" in msg:
            raise ValueError(
                "Passcode redeem could not authenticate to the database. Please try again or contact support."
            ) from exc
        raise ValueError("Invalid or expired passcode.") from exc

    data = result.data
    if data is None:
        raise ValueError("Invalid or expired passcode.")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        raise ValueError("Invalid or expired passcode.")

    from services.subscription_service import subscription_service

    subscription_service.invalidate_cache(user_id)

    # Detach any leftover company seat so usage-stats does not wipe passcode Professional
    try:
        supabase.table("profiles").update({"company_id": None}).eq("id", user_id).execute()
    except Exception as detach_err:
        logger.warning("Could not clear company_id after passcode redeem for %s: %s", user_id, detach_err)

    logger.info("Passcode redeemed for user %s until %s", user_id, data.get("access_expires_at"))
    return {
        "success": True,
        "access_expires_at": data.get("access_expires_at"),
        "message": data.get("message", "Passcode redeemed."),
    }


def create_passcodes(
    count: int,
    duration_months: int,
    notes: Optional[str] = None,
    created_by: Optional[str] = None,
    prefix: str = "PILOT-",
) -> List[Dict[str, Any]]:
    """
    Create a batch of passcodes. Returns list of created rows (including code).
    """
    if count < 1 or count > 500:
        raise ValueError("Count must be between 1 and 500.")
    if duration_months not in (3, 6):
        raise ValueError("duration_months must be 3 or 6.")

    supabase = get_supabase_client()
    alphabet = string.ascii_uppercase + string.digits
    created: List[Dict[str, Any]] = []
    for i in range(count):
        # Unique code: prefix + random 6 chars (retry if collision)
        for _ in range(5):
            code = prefix + "".join(secrets.choice(alphabet) for _ in range(6))
            r = supabase.table("promo_passcodes").select("id").eq("code", code).limit(1).execute()
            if not r.data:
                break
        else:
            raise ValueError("Could not generate unique code after retries.")

        row = {
            "code": code,
            "duration_months": duration_months,
            "max_redemptions": 1,
            "redemptions_used": 0,
            "is_active": True,
            "notes": notes or None,
            "created_by": created_by or None,
        }
        ins = supabase.table("promo_passcodes").insert(row).execute()
        if ins.data and len(ins.data) > 0:
            created.append(ins.data[0])
    return created


def list_passcodes(unused_only: bool = False) -> List[Dict[str, Any]]:
    """List all passcodes; if unused_only, filter to redemptions_used < max_redemptions."""
    supabase = get_supabase_client()
    q = supabase.table("promo_passcodes").select("*").order("created_at", desc=True)
    r = q.execute()
    rows = r.data or []
    if unused_only:
        rows = [x for x in rows if (x.get("redemptions_used") or 0) < (x.get("max_redemptions") or 1)]
    return rows


def list_redemptions() -> List[Dict[str, Any]]:
    """List passcode redemptions with passcode code and user email (join profiles for email)."""
    supabase = get_supabase_client()
    r = supabase.table("passcode_redemptions").select(
        "id, passcode_id, user_id, redeemed_at, access_expires_at"
    ).order("redeemed_at", desc=True).execute()
    redemptions = r.data or []
    if not redemptions:
        return []
    passcode_ids = list({x["passcode_id"] for x in redemptions})
    user_ids = list({x["user_id"] for x in redemptions})
    codes = {}
    for pid in passcode_ids:
        pr = supabase.table("promo_passcodes").select("id, code").eq("id", pid).limit(1).execute()
        if pr.data and len(pr.data) > 0:
            codes[pid] = pr.data[0].get("code", "")
    emails = {}
    for uid in user_ids:
        pr = supabase.table("profiles").select("id, email").eq("id", uid).limit(1).execute()
        if pr.data and len(pr.data) > 0:
            emails[uid] = pr.data[0].get("email", "")
    for x in redemptions:
        x["code"] = codes.get(x["passcode_id"], "")
        x["user_email"] = emails.get(x["user_id"], "")
    return redemptions
