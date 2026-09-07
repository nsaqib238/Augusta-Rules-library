"""Country → document-type → catalog document tree (admin library)."""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from services.shared_library_service import library_catalog
from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

DEFAULT_AU_TYPES = [
    {"slug": "legislation", "name": "Legislation", "sort_order": 10},
    {"slug": "regulatory-instruments", "name": "Regulatory instruments", "sort_order": 20},
    {"slug": "network-rules", "name": "Network rules", "sort_order": 30},
    {"slug": "authority-requirements", "name": "Authority requirements", "sort_order": 40},
    {"slug": "technical-specifications", "name": "Technical specifications", "sort_order": 50},
    {"slug": "guidance", "name": "Guidance", "sort_order": 60},
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str, fallback: str = "item") -> str:
    slug = _SLUG_RE.sub("-", (value or "").strip().lower()).strip("-")[:80]
    return slug or fallback


def _tables_missing(exc: Exception) -> bool:
    text = str(exc).lower()
    return "library_countries" in text or "does not exist" in text or "42p01" in text


def list_countries(*, active_only: bool = False) -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    try:
        query = supabase.table("library_countries").select("*").order("sort_order").order("name")
        if active_only:
            query = query.eq("is_active", True)
        result = query.execute()
        rows = list(result.data or [])
    except Exception as exc:
        if _tables_missing(exc):
            raise ValueError("Library catalog tables are missing. Run supabase/combined_setup.sql (section 10b).") from exc
        raise
    if rows:
        return rows
    return _seed_australia()


def _seed_australia() -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    try:
        existing = (
            supabase.table("library_countries").select("*").eq("code", "AU").limit(1).execute()
        )
        if existing.data:
            country = existing.data[0]
        else:
            inserted = (
                supabase.table("library_countries")
                .insert({"code": "AU", "name": "Australia", "is_active": True, "sort_order": 10})
                .execute()
            )
            country = inserted.data[0] if inserted.data else {"code": "AU", "name": "Australia"}
        for row in DEFAULT_AU_TYPES:
            try:
                supabase.table("library_document_types").upsert(
                    {**row, "country_id": country["id"]},
                    on_conflict="country_id,slug",
                ).execute()
            except Exception as exc:
                logger.warning("Could not seed AU type %s: %s", row["slug"], exc)
        result = supabase.table("library_countries").select("*").order("sort_order").execute()
        return list(result.data or [])
    except Exception as exc:
        logger.warning("Could not seed Australia catalog: %s", exc)
        return []


def create_country(code: str, name: str, *, is_active: bool = True) -> Dict[str, Any]:
    cid = (code or "").strip().upper()
    display = (name or "").strip()
    if not re.match(r"^[A-Z]{2}$", cid):
        raise ValueError("country code must be 2 letters (e.g. AU, NZ)")
    if not display:
        raise ValueError("country name is required")
    supabase = get_supabase_client()
    existing = supabase.table("library_countries").select("id").eq("code", cid).limit(1).execute()
    if existing.data:
        raise ValueError(f"Country already exists: {cid}")
    inserted = (
        supabase.table("library_countries")
        .insert({"code": cid, "name": display, "is_active": is_active, "sort_order": 100})
        .execute()
    )
    country = inserted.data[0]
    for row in DEFAULT_AU_TYPES:
        supabase.table("library_document_types").insert({**row, "country_id": country["id"]}).execute()
    return country


def get_country(code: str) -> Optional[Dict[str, Any]]:
    supabase = get_supabase_client()
    result = (
        supabase.table("library_countries")
        .select("*")
        .eq("code", (code or "").strip().upper())
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def list_types(country_id: str) -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    result = (
        supabase.table("library_document_types")
        .select("*")
        .eq("country_id", country_id)
        .order("sort_order")
        .order("name")
        .execute()
    )
    return list(result.data or [])


def create_type(country_id: str, name: str, slug: Optional[str] = None, sort_order: Optional[int] = None) -> Dict[str, Any]:
    display = (name or "").strip()
    if not display:
        raise ValueError("type name is required")
    row = {
        "country_id": country_id,
        "name": display,
        "slug": slugify(slug or display, "type"),
        "sort_order": sort_order if sort_order is not None else 100,
    }
    supabase = get_supabase_client()
    inserted = supabase.table("library_document_types").insert(row).execute()
    return inserted.data[0] if inserted.data else row


def update_type(type_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {k: v for k, v in patch.items() if k in {"name", "slug", "sort_order"} and v is not None}
    if "name" in allowed:
        allowed["name"] = str(allowed["name"]).strip()
    if "slug" in allowed:
        allowed["slug"] = slugify(str(allowed["slug"]))
    if not allowed:
        raise ValueError("no type fields to update")
    supabase = get_supabase_client()
    updated = supabase.table("library_document_types").update(allowed).eq("id", type_id).execute()
    if not updated.data:
        raise ValueError("document type not found")
    return updated.data[0]


def list_documents(*, country_id: Optional[str] = None, type_id: Optional[str] = None) -> List[Dict[str, Any]]:
    supabase = get_supabase_client()
    query = supabase.table("library_documents").select("*").order("sort_order").order("title")
    if country_id:
        query = query.eq("country_id", country_id)
    if type_id:
        query = query.eq("document_type_id", type_id)
    result = query.execute()
    return list(result.data or [])


def get_document(document_id: str) -> Optional[Dict[str, Any]]:
    supabase = get_supabase_client()
    result = supabase.table("library_documents").select("*").eq("id", document_id).limit(1).execute()
    return result.data[0] if result.data else None


def create_document(
    *,
    country_id: str,
    document_type_id: str,
    title: str,
    discipline: str = "Electrical",
    publisher: Optional[str] = None,
    priority: str = "medium",
    slug: Optional[str] = None,
    admin_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    display = (title or "").strip()
    if not display:
        raise ValueError("document title is required")
    pri = (priority or "medium").strip().lower()
    if pri not in {"critical", "high", "medium"}:
        raise ValueError("priority must be critical, high, or medium")
    row = {
        "id": str(uuid.uuid4()),
        "country_id": country_id,
        "document_type_id": document_type_id,
        "title": display,
        "slug": slugify(slug or display, "document"),
        "discipline": (discipline or "Electrical").strip() or "Electrical",
        "publisher": (publisher or "").strip() or None,
        "priority": pri,
        "sort_order": 100,
        "is_active": True,
        "created_by": admin_user_id,
    }
    supabase = get_supabase_client()
    inserted = supabase.table("library_documents").insert(row).execute()
    return inserted.data[0] if inserted.data else row


def update_document(document_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    allowed_keys = {
        "title",
        "discipline",
        "publisher",
        "priority",
        "document_type_id",
        "is_active",
        "sort_order",
        "slug",
    }
    allowed = {k: v for k, v in patch.items() if k in allowed_keys}
    if "title" in allowed and allowed["title"] is not None:
        allowed["title"] = str(allowed["title"]).strip()
        if not allowed["title"]:
            raise ValueError("title cannot be empty")
    if "priority" in allowed and allowed["priority"] is not None:
        pri = str(allowed["priority"]).strip().lower()
        if pri not in {"critical", "high", "medium"}:
            raise ValueError("priority must be critical, high, or medium")
        allowed["priority"] = pri
    if "slug" in allowed and allowed["slug"] is not None:
        allowed["slug"] = slugify(str(allowed["slug"]))
    if not allowed:
        raise ValueError("no document fields to update")
    supabase = get_supabase_client()
    updated = supabase.table("library_documents").update(allowed).eq("id", document_id).execute()
    if not updated.data:
        raise ValueError("catalog document not found")
    return updated.data[0]


def infer_edition_family(type_slug: Optional[str], title: str = "") -> str:
    slug = (type_slug or "").lower()
    text = f"{title} {slug}".lower()
    if "ncc" in text or slug == "regulatory-instruments":
        if "ncc" in text:
            return "NCC"
    if slug == "network-rules" or "sir" in text:
        return "SIR"
    return "LIB"


def catalog_tree() -> Dict[str, Any]:
    countries = list_countries()
    types = []
    documents = []
    supabase = get_supabase_client()
    if countries:
        ids = [c["id"] for c in countries]
        type_rows = supabase.table("library_document_types").select("*").in_("country_id", ids).execute()
        types = list(type_rows.data or [])
        doc_rows = supabase.table("library_documents").select("*").in_("country_id", ids).execute()
        documents = list(doc_rows.data or [])
    editions = library_catalog()
    return {
        "countries": countries,
        "types": types,
        "documents": documents,
        "editions": editions,
    }
