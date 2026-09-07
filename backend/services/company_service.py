"""
Company seats + marketing referral partners (minimal v1).
"""
from __future__ import annotations

import logging
import secrets
import string
from typing import Any, Dict, List, Optional

from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

PLAN_SEATS = {
    "company_small": 25,
    "company_large": 50,
}

PLAN_DISPLAY = {
    "company_small": "Company Small",
    "company_large": "Company Large",
}


def _gen_join_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "CO-" + "".join(secrets.choice(alphabet) for _ in range(8))


def resolve_partner_id(referral_code: Optional[str]) -> Optional[str]:
    code = (referral_code or "").strip().upper()
    if not code:
        return None
    supabase = get_supabase_client()
    r = (
        supabase.table("referral_partners")
        .select("id")
        .eq("is_active", True)
        .eq("code", code)
        .limit(1)
        .execute()
    )
    rows = r.data or []
    return rows[0]["id"] if rows else None


def create_pending_company(
    owner_user_id: str,
    plan_name: str,
    company_name: Optional[str] = None,
    referral_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Create pending company + owner membership before Stripe checkout."""
    plan = (plan_name or "").strip().lower()
    if plan not in PLAN_SEATS:
        raise ValueError("Invalid company plan. Use company_small or company_large.")

    supabase = get_supabase_client()
    max_seats = PLAN_SEATS[plan]
    partner_id = resolve_partner_id(referral_code)

    # Reuse pending company for same owner if any
    existing = (
        supabase.table("companies")
        .select("*")
        .eq("owner_user_id", owner_user_id)
        .eq("status", "pending")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if existing.data:
        company = existing.data[0]
        updates: Dict[str, Any] = {"max_seats": max_seats}
        if company_name and company_name.strip():
            updates["name"] = company_name.strip()
        if partner_id and not company.get("referred_by_partner_id"):
            updates["referred_by_partner_id"] = partner_id
        supabase.table("companies").update(updates).eq("id", company["id"]).execute()
        company = (
            supabase.table("companies").select("*").eq("id", company["id"]).single().execute().data
        )
        return company

    join_code = _gen_join_code()
    for _ in range(5):
        clash = supabase.table("companies").select("id").eq("join_code", join_code).limit(1).execute()
        if not clash.data:
            break
        join_code = _gen_join_code()

    row = {
        "name": (company_name or "Company").strip() or "Company",
        "owner_user_id": owner_user_id,
        "join_code": join_code,
        "max_seats": max_seats,
        "status": "pending",
        "referred_by_partner_id": partner_id,
    }
    ins = supabase.table("companies").insert(row).execute()
    if not ins.data:
        raise RuntimeError("Failed to create company")
    company = ins.data[0]

    supabase.table("company_memberships").upsert(
        {
            "company_id": company["id"],
            "user_id": owner_user_id,
            "role": "owner",
            "status": "active",
        },
        on_conflict="company_id,user_id",
    ).execute()

    return company


def activate_company_from_checkout(
    company_id: str,
    owner_user_id: str,
    stripe_subscription_id: str,
    stripe_customer_id: Optional[str] = None,
    max_seats: Optional[int] = None,
    referral_code: Optional[str] = None,
) -> None:
    """Mark company active after successful Stripe checkout; grant owner Professional."""
    sub_id = (stripe_subscription_id or "").strip()
    if not sub_id:
        logger.error(
            "activate_company: refusing activate without stripe_subscription_id company=%s owner=%s",
            company_id,
            owner_user_id,
        )
        return

    supabase = get_supabase_client()
    partner_id = resolve_partner_id(referral_code)

    company = (
        supabase.table("companies").select("*").eq("id", company_id).limit(1).execute().data or [None]
    )[0]
    if not company:
        logger.error("activate_company: company %s not found", company_id)
        return

    updates: Dict[str, Any] = {
        "status": "active",
        "stripe_subscription_id": sub_id,
    }
    if stripe_customer_id:
        updates["stripe_customer_id"] = stripe_customer_id
    if max_seats and int(max_seats) > 0:
        updates["max_seats"] = int(max_seats)
    if partner_id and not company.get("referred_by_partner_id"):
        updates["referred_by_partner_id"] = partner_id

    supabase.table("companies").update(updates).eq("id", company_id).execute()

    # Verify Stripe id stuck (cancel webhooks depend on it)
    verify = (
        supabase.table("companies")
        .select("id, status, stripe_subscription_id")
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    verified = (verify.data or [None])[0]
    if not verified or (verified.get("stripe_subscription_id") or "").strip() != sub_id:
        logger.error(
            "activate_company: stripe_subscription_id not persisted company=%s expected=%s got=%s",
            company_id,
            sub_id,
            (verified or {}).get("stripe_subscription_id"),
        )

    supabase.table("company_memberships").upsert(
        {
            "company_id": company_id,
            "user_id": owner_user_id,
            "role": "owner",
            "status": "active",
        },
        on_conflict="company_id,user_id",
    ).execute()

    supabase.table("profiles").update(
        {
            "account_type": "professional",
            "subscription_status": "active",
            "subscription_source": "company",
            "company_id": company_id,
            "access_expires_at": None,
            "stripe_subscription_id": sub_id,
            **({"stripe_customer_id": stripe_customer_id} if stripe_customer_id else {}),
        }
    ).eq("id", owner_user_id).execute()

    from services.subscription_service import subscription_service

    subscription_service.invalidate_cache(owner_user_id)
    logger.info(
        "Activated company %s for owner %s stripe_sub=%s",
        company_id,
        owner_user_id,
        sub_id,
    )


def activate_pending_company_for_paid_subscription(
    owner_user_id: str,
    stripe_subscription_id: str,
    stripe_customer_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Pre-launch backstop: if Stripe payment succeeded but company stayed pending
    (missed webhook / missing metadata), activate the owner's latest pending company.
    Returns company_id when activated, else None.
    """
    oid = (owner_user_id or "").strip()
    sub_id = (stripe_subscription_id or "").strip()
    if not oid or not sub_id:
        return None

    meta = metadata or {}
    company_id = (meta.get("company_id") or "").strip() or None
    plan_name = (meta.get("plan_name") or "").strip().lower()
    referral_code = meta.get("referral_code")
    max_seats = meta.get("max_seats")
    try:
        max_seats_int = int(max_seats) if max_seats is not None else None
    except (TypeError, ValueError):
        max_seats_int = None

    supabase = get_supabase_client()

    # Prefer explicit company_id from Stripe metadata
    if not company_id:
        pending = (
            supabase.table("companies")
            .select("id, max_seats")
            .eq("owner_user_id", oid)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if pending.data:
            company_id = pending.data[0]["id"]
            if max_seats_int is None:
                max_seats_int = pending.data[0].get("max_seats")
        else:
            # Already active but missing stripe id
            active_missing = (
                supabase.table("companies")
                .select("id, max_seats")
                .eq("owner_user_id", oid)
                .eq("status", "active")
                .is_("stripe_subscription_id", "null")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if not active_missing.data:
                # Also match empty string stripe ids
                active_missing = (
                    supabase.table("companies")
                    .select("id, max_seats, stripe_subscription_id")
                    .eq("owner_user_id", oid)
                    .eq("status", "active")
                    .order("created_at", desc=True)
                    .limit(3)
                    .execute()
                )
                rows = [
                    r
                    for r in (active_missing.data or [])
                    if not (r.get("stripe_subscription_id") or "").strip()
                ]
                if rows:
                    company_id = rows[0]["id"]
                    if max_seats_int is None:
                        max_seats_int = rows[0].get("max_seats")
            elif active_missing.data:
                company_id = active_missing.data[0]["id"]
                if max_seats_int is None:
                    max_seats_int = active_missing.data[0].get("max_seats")

    if not company_id:
        logger.info(
            "No pending/active-unlinked company to activate for owner=%s sub=%s plan=%s",
            oid,
            sub_id,
            plan_name or "(none)",
        )
        return None

    if max_seats_int is None and plan_name in PLAN_SEATS:
        max_seats_int = PLAN_SEATS[plan_name]

    activate_company_from_checkout(
        company_id=company_id,
        owner_user_id=oid,
        stripe_subscription_id=sub_id,
        stripe_customer_id=stripe_customer_id,
        max_seats=max_seats_int,
        referral_code=referral_code,
    )
    return company_id


def _downgrade_company_members(company_ids: List[str]) -> None:
    """Deactivate memberships and set company-sourced profiles back to Sole."""
    if not company_ids:
        return
    supabase = get_supabase_client()
    from services.subscription_service import subscription_service

    for cid in company_ids:
        members = (
            supabase.table("company_memberships")
            .select("user_id")
            .eq("company_id", cid)
            .eq("status", "active")
            .execute()
        )
        for m in members.data or []:
            uid = m["user_id"]
            # Schema allows active | removed (not inactive)
            supabase.table("company_memberships").update({"status": "removed"}).eq(
                "company_id", cid
            ).eq("user_id", uid).execute()
            supabase.table("profiles").update(
                {
                    "account_type": "sole",
                    "subscription_status": "inactive",
                    "subscription_source": None,
                    "company_id": None,
                    "stripe_subscription_id": None,
                    "access_expires_at": None,
                }
            ).eq("id", uid).eq("subscription_source", "company").execute()
            # Also clear owner profile if still pointing at this company
            supabase.table("profiles").update(
                {
                    "account_type": "sole",
                    "subscription_status": "inactive",
                    "subscription_source": None,
                    "company_id": None,
                    "stripe_subscription_id": None,
                    "access_expires_at": None,
                }
            ).eq("id", uid).eq("company_id", cid).execute()
            subscription_service.invalidate_cache(uid)


def find_companies_for_stripe_subscription(
    stripe_subscription_id: Optional[str],
    *,
    company_id: Optional[str] = None,
    owner_user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Resolve company rows for a Stripe sub (with fallbacks when stripe id was never saved)."""
    supabase = get_supabase_client()
    found: Dict[str, Dict[str, Any]] = {}
    sub_id = (stripe_subscription_id or "").strip()

    if sub_id:
        r = (
            supabase.table("companies")
            .select("*")
            .eq("stripe_subscription_id", sub_id)
            .execute()
        )
        for c in r.data or []:
            found[c["id"]] = c

    cid = (company_id or "").strip()
    if cid and cid not in found:
        r = supabase.table("companies").select("*").eq("id", cid).limit(1).execute()
        for c in r.data or []:
            found[c["id"]] = c

    oid = (owner_user_id or "").strip()
    if oid:
        r = (
            supabase.table("companies")
            .select("*")
            .eq("owner_user_id", oid)
            .in_("status", ["active", "pending"])
            .execute()
        )
        for c in r.data or []:
            # Prefer rows missing stripe id or already matching this sub
            existing_sub = (c.get("stripe_subscription_id") or "").strip()
            if not existing_sub or (sub_id and existing_sub == sub_id) or c["id"] == cid:
                found[c["id"]] = c

    return list(found.values())


def set_company_status_by_subscription(
    stripe_subscription_id: str,
    status: str,
    *,
    company_id: Optional[str] = None,
    owner_user_id: Optional[str] = None,
) -> List[str]:
    """
    Sync company status from Stripe subscription updates/cancels.
    Returns company ids touched. Uses metadata fallbacks when stripe id was not stored.
    """
    mapped = "active" if (status or "").strip().lower() in ("active", "trialing", "past_due") else "inactive"
    sub_id = (stripe_subscription_id or "").strip()
    companies = find_companies_for_stripe_subscription(
        sub_id,
        company_id=company_id,
        owner_user_id=owner_user_id,
    )
    if not companies:
        logger.warning(
            "set_company_status: no company for sub=%s company_id=%s owner=%s status=%s",
            sub_id,
            company_id,
            owner_user_id,
            status,
        )
        return []

    supabase = get_supabase_client()
    touched: List[str] = []
    for company in companies:
        cid = company["id"]
        updates: Dict[str, Any] = {"status": mapped}
        # Backfill Stripe id so future cancels match cleanly
        if sub_id and not (company.get("stripe_subscription_id") or "").strip():
            updates["stripe_subscription_id"] = sub_id
        supabase.table("companies").update(updates).eq("id", cid).execute()
        touched.append(cid)

    if mapped == "inactive":
        _downgrade_company_members(touched)

    return touched


def downgrade_owner_after_company_cancel(owner_user_id: Optional[str]) -> None:
    """Force owner to Sole after company subscription ends (even if membership flags are messy)."""
    oid = (owner_user_id or "").strip()
    if not oid:
        return
    supabase = get_supabase_client()
    supabase.table("profiles").update(
        {
            "account_type": "sole",
            "subscription_status": "inactive",
            "subscription_source": None,
            "company_id": None,
            "stripe_subscription_id": None,
            "access_expires_at": None,
        }
    ).eq("id", oid).execute()
    from services.subscription_service import subscription_service

    subscription_service.invalidate_cache(oid)
    logger.info("Downgraded owner %s to sole after company cancel", oid)


def join_company(user_id: str, code: str) -> Dict[str, Any]:
    supabase = get_supabase_client()
    try:
        result = supabase.rpc(
            "join_company_by_code",
            {"p_code": code, "p_user_id": user_id},
        ).execute()
    except Exception as exc:
        # PostgREST / supabase-py may put the DB message on .message, args, or JSON body
        raw = str(getattr(exc, "message", None) or exc)
        details = getattr(exc, "details", None) or getattr(exc, "args", None)
        msg = f"{raw} {details or ''}".lower()
        logger.warning("join_company failed for %s code=%r: %s", user_id, code, raw)
        if "no seats" in msg:
            raise ValueError("This company has no seats left.") from exc
        if "not active" in msg:
            raise ValueError(
                "This company subscription is not active yet. "
                "The owner must complete Stripe payment and the company status must be active."
            ) from exc
        if "already belong" in msg:
            raise ValueError("You already belong to a company.") from exc
        if "required" in msg:
            raise ValueError("Company code is required.") from exc
        if "access denied" in msg:
            raise ValueError("Could not join company (access denied). Contact support.") from exc
        if "invalid company code" in msg:
            raise ValueError("Invalid company code.") from exc
        raise ValueError(f"Could not join company: {raw}") from exc

    data = result.data
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        raise ValueError("Invalid company code.")

    from services.subscription_service import subscription_service

    # Re-assert Professional in case passcode residue / prior Sole stuck on the profile
    company_id = data.get("company_id")
    if company_id:
        try:
            supabase.table("profiles").update(
                {
                    "account_type": "professional",
                    "subscription_status": "active",
                    "subscription_source": "company",
                    "company_id": company_id,
                    "access_expires_at": None,
                }
            ).eq("id", user_id).execute()
        except Exception as profile_err:
            logger.warning(
                "join_company: profile Professional sync failed for %s: %s",
                user_id,
                profile_err,
            )

    subscription_service.invalidate_cache(user_id)
    return data


def get_user_company(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Resolve the user's company from memberships.
    Prefers active, then pending (checkout in progress). Soft-removes only
    inactive/orphan seats — never wipe pending (that broke Company Small after pay).
    """
    try:
        supabase = get_supabase_client()
        m = (
            supabase.table("company_memberships")
            .select("id, company_id, role, status")
            .eq("user_id", user_id)
            .eq("status", "active")
            .execute()
        )
        rows = m.data or []
        if not rows:
            return None

        active_pick: Optional[tuple] = None
        pending_pick: Optional[tuple] = None

        for membership in rows:
            cid = membership.get("company_id")
            if not cid:
                continue
            c = (
                supabase.table("companies")
                .select("*")
                .eq("id", cid)
                .limit(1)
                .execute()
            )
            if not c.data:
                try:
                    supabase.table("company_memberships").update({"status": "removed"}).eq(
                        "id", membership["id"]
                    ).execute()
                except Exception as clean_err:
                    logger.warning(
                        "Failed to remove orphan membership %s: %s",
                        membership.get("id"),
                        clean_err,
                    )
                continue

            company = c.data[0]
            status = (company.get("status") or "").strip().lower()
            if status == "active":
                active_pick = (membership, company)
                break
            if status == "pending" and pending_pick is None:
                pending_pick = (membership, company)
            elif status == "inactive":
                # Canceled company — free seat so user can buy/join again
                try:
                    supabase.table("company_memberships").update({"status": "removed"}).eq(
                        "id", membership["id"]
                    ).execute()
                except Exception as clean_err:
                    logger.warning(
                        "Failed to remove inactive membership %s: %s",
                        membership.get("id"),
                        clean_err,
                    )

        chosen = active_pick or pending_pick
        if not chosen:
            return None

        membership, company = chosen
        seats = (
            supabase.table("company_memberships")
            .select("id", count="exact")
            .eq("company_id", company["id"])
            .eq("status", "active")
            .execute()
        )
        company["role"] = membership["role"]
        company["seats_used"] = seats.count if seats.count is not None else len(seats.data or [])
        return company
    except Exception as e:
        logger.error("get_user_company failed for %s: %s", user_id, e, exc_info=True)
        return None


def get_active_company_id_for_user(user_id: str) -> Optional[str]:
    co = get_user_company(user_id)
    if co and co.get("status") == "active":
        return co["id"]
    return None


def list_company_members(requester_user_id: str) -> List[Dict[str, Any]]:
    """Owner-only: list active members of their company."""
    company = get_user_company(requester_user_id)
    if not company:
        raise ValueError("You are not in a company.")
    if company.get("role") != "owner":
        raise ValueError("Only the company owner can view the member list.")

    supabase = get_supabase_client()
    members = (
        supabase.table("company_memberships")
        .select("user_id, role, status, joined_at")
        .eq("company_id", company["id"])
        .eq("status", "active")
        .order("joined_at")
        .execute()
    )
    out: List[Dict[str, Any]] = []
    for m in members.data or []:
        profile = (
            supabase.table("profiles")
            .select("email, full_name")
            .eq("id", m["user_id"])
            .limit(1)
            .execute()
        )
        row = (profile.data or [{}])[0]
        out.append(
            {
                "user_id": m["user_id"],
                "role": m["role"],
                "joined_at": m.get("joined_at"),
                "email": row.get("email"),
                "full_name": row.get("full_name"),
                "is_self": m["user_id"] == requester_user_id,
            }
        )
    return out


def remove_company_member(owner_user_id: str, member_user_id: str) -> Dict[str, Any]:
    """Owner removes an employee seat. Does not delete the employee's Augusta account."""
    member_user_id = (member_user_id or "").strip()
    if not member_user_id:
        raise ValueError("Member user id is required.")
    if member_user_id == owner_user_id:
        raise ValueError("You cannot remove yourself. Transfer ownership or cancel the subscription instead.")

    company = get_user_company(owner_user_id)
    if not company:
        raise ValueError("You are not in a company.")
    if company.get("role") != "owner":
        raise ValueError("Only the company owner can remove members.")
    if company.get("status") != "active":
        raise ValueError("Company must be active to manage seats.")

    supabase = get_supabase_client()
    membership = (
        supabase.table("company_memberships")
        .select("id, role, status")
        .eq("company_id", company["id"])
        .eq("user_id", member_user_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not membership.data:
        raise ValueError("That user is not an active member of your company.")
    if membership.data[0].get("role") == "owner":
        raise ValueError("Cannot remove the company owner.")

    supabase.table("company_memberships").update({"status": "removed"}).eq(
        "id", membership.data[0]["id"]
    ).execute()

    # Drop company Professional access if it came from this seat
    profile = (
        supabase.table("profiles")
        .select("subscription_source, company_id")
        .eq("id", member_user_id)
        .limit(1)
        .execute()
    )
    prof = (profile.data or [{}])[0]
    if (prof.get("subscription_source") or "").strip().lower() == "company" or prof.get(
        "company_id"
    ) == company["id"]:
        supabase.table("profiles").update(
            {
                "account_type": "sole",
                "subscription_status": "inactive",
                "subscription_source": None,
                "company_id": None,
                "access_expires_at": None,
            }
        ).eq("id", member_user_id).execute()

    from services.subscription_service import subscription_service

    subscription_service.invalidate_cache(member_user_id)
    subscription_service.invalidate_cache(owner_user_id)

    logger.info(
        "Owner %s removed member %s from company %s",
        owner_user_id,
        member_user_id,
        company["id"],
    )
    return {"success": True, "message": "Member removed. Seat is free for someone else to join."}


def list_referral_partners() -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    r = supabase.table("referral_partners").select("*").order("created_at", desc=True).execute()
    return r.data or []


def _normalize_login_email(email: Optional[str]) -> Optional[str]:
    e = (email or "").strip().lower()
    return e or None


def create_referral_partner(
    name: str,
    code: str,
    notes: Optional[str] = None,
    commission_note: Optional[str] = None,
    login_email: Optional[str] = None,
) -> Dict[str, Any]:
    supabase = get_supabase_client()
    code_n = (code or "").strip().upper()
    name_n = (name or "").strip()
    email_n = _normalize_login_email(login_email)
    if not name_n or not code_n:
        raise ValueError("Name and code are required.")
    if email_n:
        existing = (
            supabase.table("referral_partners")
            .select("id, code")
            .ilike("login_email", email_n)
            .limit(1)
            .execute()
        )
        if existing.data:
            raise ValueError(
                f"Login email already linked to partner code {existing.data[0].get('code')}."
            )
    row: Dict[str, Any] = {
        "name": name_n,
        "code": code_n,
        "notes": notes,
        "commission_note": commission_note,
        "is_active": True,
    }
    if email_n:
        row["login_email"] = email_n
    ins = supabase.table("referral_partners").insert(row).execute()
    if not ins.data:
        raise RuntimeError("Failed to create referral partner")
    return ins.data[0]


def get_partner_by_login_email(email: Optional[str]) -> Optional[Dict[str, Any]]:
    email_n = _normalize_login_email(email)
    if not email_n:
        return None
    supabase = get_supabase_client()
    r = (
        supabase.table("referral_partners")
        .select("*")
        .eq("is_active", True)
        .ilike("login_email", email_n)
        .limit(1)
        .execute()
    )
    rows = r.data or []
    return rows[0] if rows else None


def plan_label_from_seats(max_seats: Optional[int]) -> str:
    if max_seats is None:
        return "Company"
    if max_seats <= 25:
        return PLAN_DISPLAY["company_small"]
    return PLAN_DISPLAY["company_large"]


def get_my_partner_dashboard(user_email: Optional[str]) -> Dict[str, Any]:
    """Scoped partner view: only companies referred by this partner."""
    partner = get_partner_by_login_email(user_email)
    if not partner:
        return {"is_partner": False}

    supabase = get_supabase_client()
    partner_id = partner["id"]
    companies = (
        supabase.table("companies")
        .select("*")
        .eq("referred_by_partner_id", partner_id)
        .order("created_at", desc=True)
        .execute()
    )
    out_companies: List[Dict[str, Any]] = []
    summary = {"active": 0, "pending": 0, "inactive": 0, "total": 0}
    for c in companies.data or []:
        status = (c.get("status") or "pending").lower()
        if status in summary:
            summary[status] += 1
        summary["total"] += 1
        seats = (
            supabase.table("company_memberships")
            .select("id", count="exact")
            .eq("company_id", c["id"])
            .eq("status", "active")
            .execute()
        )
        owner = (
            supabase.table("profiles")
            .select("email, full_name")
            .eq("id", c["owner_user_id"])
            .limit(1)
            .execute()
        )
        owner_row = (owner.data or [{}])[0]
        out_companies.append(
            {
                "id": c["id"],
                "name": c.get("name") or "Company",
                "status": c.get("status"),
                "max_seats": c.get("max_seats"),
                "seats_used": seats.count if seats.count is not None else 0,
                "plan_label": plan_label_from_seats(c.get("max_seats")),
                "owner_email": owner_row.get("email"),
                "owner_name": owner_row.get("full_name"),
                "created_at": c.get("created_at"),
            }
        )

    return {
        "is_partner": True,
        "partner": {
            "id": partner["id"],
            "name": partner.get("name"),
            "code": partner.get("code"),
            "commission_note": partner.get("commission_note"),
            "login_email": partner.get("login_email"),
        },
        "summary": summary,
        "companies": out_companies,
    }


def list_companies_admin() -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    companies = supabase.table("companies").select("*").order("created_at", desc=True).execute()
    partners = {
        p["id"]: p
        for p in (supabase.table("referral_partners").select("id, name, code").execute().data or [])
    }
    out = []
    for c in companies.data or []:
        seats = (
            supabase.table("company_memberships")
            .select("id", count="exact")
            .eq("company_id", c["id"])
            .eq("status", "active")
            .execute()
        )
        partner = partners.get(c.get("referred_by_partner_id") or "")
        owner = (
            supabase.table("profiles")
            .select("email, full_name")
            .eq("id", c["owner_user_id"])
            .limit(1)
            .execute()
        )
        out.append(
            {
                **c,
                "seats_used": seats.count if seats.count is not None else 0,
                "partner_name": partner.get("name") if partner else None,
                "partner_code": partner.get("code") if partner else None,
                "owner_email": (owner.data or [{}])[0].get("email") if owner.data else None,
            }
        )
    return out
