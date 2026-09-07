"""Standard / codebook catalogue for uploads (search + LLM context later)."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from services.standard_families import infer_standard_family, normalize_standard_family

CodebookEntry = Dict[str, str]

# id = stored in documents.codebook; label = human name (documents.source)
CODEBOOKS: List[CodebookEntry] = [
    # Electrical
    {"id": "AS3000", "label": "AS/NZS 3000", "discipline": "electrical"},
    {"id": "AS3003", "label": "AS/NZS 3003", "discipline": "electrical"},
    {"id": "AS3017", "label": "AS/NZS 3017", "discipline": "electrical"},
    {"id": "AS3008_1_1", "label": "AS/NZS 3008.1.1", "discipline": "electrical"},
    {"id": "AS3012", "label": "AS/NZS 3012", "discipline": "electrical"},
    {"id": "AS3010", "label": "AS/NZS 3010", "discipline": "electrical"},
    {"id": "AS3015", "label": "AS/NZS 3015", "discipline": "electrical"},
    {"id": "AS3019", "label": "AS/NZS 3019", "discipline": "electrical"},
    {"id": "AS3001", "label": "AS/NZS 3001", "discipline": "electrical"},
    {"id": "AS3007", "label": "AS/NZS 3007", "discipline": "electrical"},
    {"id": "AS5033", "label": "AS/NZS 5033", "discipline": "electrical"},
    {"id": "AS4777_1", "label": "AS/NZS 4777.1", "discipline": "electrical"},
    {"id": "AS4777_2", "label": "AS/NZS 4777.2", "discipline": "electrical"},
    {"id": "AS5139", "label": "AS/NZS 5139", "discipline": "electrical"},
    {"id": "AS61439", "label": "AS/NZS 61439 (Switchboards)", "discipline": "electrical"},
    {"id": "IEC61439", "label": "IEC 61439", "discipline": "electrical", "family": "IEC"},
    {"id": "ISO50001", "label": "ISO 50001 (Energy management)", "discipline": "electrical", "family": "ISO"},
    {"id": "IEEE1584", "label": "IEEE 1584 (Arc flash)", "discipline": "electrical", "family": "IEEE"},
    {"id": "IEEE519", "label": "IEEE 519 (Harmonics)", "discipline": "electrical", "family": "IEEE"},
    {"id": "NBN_MDU", "label": "NBN MDU Guidelines", "discipline": "electrical", "family": "NBN"},
    {"id": "NBN_SDU", "label": "NBN SDU Guidelines", "discipline": "electrical", "family": "NBN"},
    {"id": "NBN_TELECOM", "label": "NBN Telecom / Pit & Pipe Guidelines", "discipline": "electrical", "family": "NBN"},
    {"id": "AS1768", "label": "AS/NZS 1768 (Lightning)", "discipline": "electrical"},
    {"id": "AS2067", "label": "AS 2067 (HV)", "discipline": "electrical"},
    {"id": "AS1158", "label": "AS/NZS 1158 (Road lighting)", "discipline": "electrical"},
    # Fire
    {"id": "AS1670_1", "label": "AS 1670.1", "discipline": "fire"},
    {"id": "AS1670_3", "label": "AS 1670.3", "discipline": "fire"},
    {"id": "AS2118_1", "label": "AS 2118.1", "discipline": "fire"},
    {"id": "AS2118_4", "label": "AS 2118.4", "discipline": "fire"},
    {"id": "AS2419_1", "label": "AS 2419.1", "discipline": "fire"},
    {"id": "AS1851", "label": "AS 1851", "discipline": "fire"},
    {"id": "AS2293_1", "label": "AS 2293.1", "discipline": "fire"},
    {"id": "AS2293_2", "label": "AS 2293.2", "discipline": "fire"},
    {"id": "AS2293_3", "label": "AS 2293.3", "discipline": "fire"},
    {"id": "AS4428", "label": "AS 4428 Series", "discipline": "fire"},
    {"id": "AS7240", "label": "AS 7240 Series", "discipline": "fire"},
    {"id": "AS1530", "label": "AS 1530 Series", "discipline": "fire"},
    {"id": "AS4072_1", "label": "AS 4072.1", "discipline": "fire"},
    {"id": "AS1530_4", "label": "AS 1530.4", "discipline": "fire"},
    {"id": "NFPA13", "label": "NFPA 13 (Sprinklers)", "discipline": "fire", "family": "NFPA"},
    {"id": "NFPA72", "label": "NFPA 72 (Fire alarm)", "discipline": "fire", "family": "NFPA"},
    {"id": "NFPA101", "label": "NFPA 101 (Life safety)", "discipline": "fire", "family": "NFPA"},
    # Hydraulics
    {"id": "AS3500_0", "label": "AS/NZS 3500.0", "discipline": "hydraulics"},
    {"id": "AS3500_1", "label": "AS/NZS 3500.1", "discipline": "hydraulics"},
    {"id": "AS3500_2", "label": "AS/NZS 3500.2", "discipline": "hydraulics"},
    {"id": "AS3500_3", "label": "AS/NZS 3500.3", "discipline": "hydraulics"},
    {"id": "AS3500_4", "label": "AS/NZS 3500.4", "discipline": "hydraulics"},
    {"id": "AS1546", "label": "AS 1546", "discipline": "hydraulics"},
    {"id": "AS1547", "label": "AS 1547", "discipline": "hydraulics"},
    {"id": "AS6400", "label": "AS/NZS 6400", "discipline": "hydraulics"},
    {"id": "AS3688", "label": "AS 3688", "discipline": "hydraulics"},
    {"id": "AS2845", "label": "AS 2845 Series", "discipline": "hydraulics"},
    {"id": "API650", "label": "API 650 (Welded tanks)", "discipline": "hydraulics", "family": "API"},
    {"id": "API620", "label": "API 620 (Low-pressure tanks)", "discipline": "hydraulics", "family": "API"},
    # Mechanical (HVAC)
    {"id": "AS1668_1", "label": "AS 1668.1", "discipline": "mechanical"},
    {"id": "AS1668_2", "label": "AS 1668.2", "discipline": "mechanical"},
    {"id": "AS3666_1", "label": "AS/NZS 3666.1", "discipline": "mechanical"},
    {"id": "AS3666_2", "label": "AS/NZS 3666.2", "discipline": "mechanical"},
    {"id": "AS3666_3", "label": "AS/NZS 3666.3", "discipline": "mechanical"},
    {"id": "AS3666_4", "label": "AS/NZS 3666.4", "discipline": "mechanical"},
    {"id": "AS4254", "label": "AS/NZS 4254 Series", "discipline": "mechanical"},
    {"id": "AS4254_1", "label": "AS 4254.1", "discipline": "mechanical"},
    {"id": "AS4254_2", "label": "AS 4254.2", "discipline": "mechanical"},
    {"id": "AS4254_3", "label": "AS 4254.3", "discipline": "mechanical"},
    {"id": "AS4254_4", "label": "AS 4254.4", "discipline": "mechanical"},
    {"id": "AS1324", "label": "AS 1324", "discipline": "mechanical"},
    {"id": "AS1851_HVAC", "label": "AS 1851 (HVAC fire systems)", "discipline": "mechanical"},
    {"id": "ISO16890", "label": "ISO 16890 (Air filters)", "discipline": "mechanical", "family": "ISO"},
    {"id": "ASTM_A36", "label": "ASTM A36 (Structural steel)", "discipline": "mechanical", "family": "ASTM"},
    # Shared library (admin)
    {"id": "NCC2022_VOL1", "label": "NCC 2022 Vol 1 — Class 2–9", "discipline": "fire"},
    {"id": "NCC2022_VOL2", "label": "NCC 2022 Vol 2 — Class 1 & 10", "discipline": "fire"},
    {"id": "NCC2022_VOL3", "label": "NCC 2022 Vol 3 — Plumbing Code", "discipline": "hydraulics"},
    {"id": "NSW_SIR_2018", "label": "NSW SIR 2018", "discipline": "electrical"},
    {"id": "SA_SIR_2025", "label": "South Australia SIR 2025", "discipline": "electrical"},
    {"id": "TASNETWORK_SIR_V85", "label": "TasNetwork SIR V8-5", "discipline": "electrical"},
    {"id": "VIC_SIR_2025", "label": "Victorian SIR 2025", "discipline": "electrical"},
]

_CODEBOOK_BY_ID: Dict[str, CodebookEntry] = {c["id"].upper(): c for c in CODEBOOKS}

VALID_DISCIPLINES = frozenset({"electrical", "mechanical", "fire", "hydraulics"})

SHARED_LIBRARY_IDS = frozenset(
    {
        "NCC2022_VOL1",
        "NCC2022_VOL2",
        "NCC2022_VOL3",
        "NSW_SIR_2018",
        "SA_SIR_2025",
        "TASNETWORK_SIR_V85",
        "VIC_SIR_2025",
    }
)


def sanitize_custom_codebook_id(raw: str) -> str:
    """Turn free text into a stable codebook id (max 64 chars)."""
    s = (raw or "").strip()
    if not s:
        return "CUSTOM"
    s = re.sub(r"[^\w\s\-/]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "_", s).strip("_").upper()
    return (s[:64] if s else "CUSTOM")


def resolve_upload_codebook(
    codebook_id: str,
    *,
    codebook_custom: Optional[str] = None,
    codebook_label: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Resolve form values to (codebook_id, display_label).
    display_label is stored in documents.source for LLM/search context.
    """
    cid = (codebook_id or "").strip().upper()
    if cid in ("OTHER", "CUSTOM", ""):
        custom = (codebook_custom or codebook_label or "").strip()
        if not custom:
            raise ValueError("Enter a code or standard name when selecting Other")
        sid = sanitize_custom_codebook_id(custom)
        label = (codebook_label or custom).strip() or sid
        return sid, label

    known = _CODEBOOK_BY_ID.get(cid)
    if known:
        label = (codebook_label or known["label"]).strip() or known["label"]
        return known["id"], label

    label = (codebook_label or cid).strip() or cid
    return sanitize_custom_codebook_id(cid), label


def codebook_family(entry: CodebookEntry) -> str:
    if entry.get("family"):
        return normalize_standard_family(entry["family"])
    return infer_standard_family(entry["id"], codebook_label=entry.get("label"))


def family_for_codebook_id(codebook_id: str) -> str:
    known = _CODEBOOK_BY_ID.get((codebook_id or "").strip().upper())
    if known:
        return codebook_family(known)
    return infer_standard_family(codebook_id)


def codebooks_for_discipline(discipline: str, standard_family: Optional[str] = None) -> List[CodebookEntry]:
    d = (discipline or "electrical").strip().lower()
    fam = normalize_standard_family(standard_family) if standard_family else None
    out = [c for c in CODEBOOKS if c["discipline"] == d]
    if fam:
        out = [c for c in out if codebook_family(c) == fam]
    return out


def codebooks_for_user_uploads(discipline: str, standard_family: Optional[str] = None) -> List[CodebookEntry]:
    return [
        c
        for c in codebooks_for_discipline(discipline, standard_family)
        if c["id"] not in SHARED_LIBRARY_IDS
    ]


def resolve_upload_standard_family(
    codebook_id: str,
    *,
    standard_family: Optional[str] = None,
    codebook_label: Optional[str] = None,
) -> str:
    known = _CODEBOOK_BY_ID.get((codebook_id or "").strip().upper())
    catalogue_family = codebook_family(known) if known else None
    return infer_standard_family(
        codebook_id,
        codebook_label=codebook_label,
        explicit=standard_family,
        catalogue_family=catalogue_family,
    )
