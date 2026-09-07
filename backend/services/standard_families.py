"""Standard publisher families for clause parsing (upload + PDF pipeline)."""
from __future__ import annotations

import re
from typing import Optional

STANDARD_FAMILIES = frozenset(
    {"AS_NZS", "ISO", "IEC", "ASTM", "NFPA", "API", "IEEE", "NBN"}
)
DEFAULT_STANDARD_FAMILY = "AS_NZS"

FAMILY_LABELS = {
    "AS_NZS": "AS/NZS — Standards Australia / New Zealand",
    "ISO": "ISO — International Organization for Standardization",
    "IEC": "IEC — International Electrotechnical Commission",
    "ASTM": "ASTM — American Society for Testing and Materials",
    "NFPA": "NFPA — National Fire Protection Association",
    "API": "API — American Petroleum Institute",
    "IEEE": "IEEE — Institute of Electrical and Electronics Engineers",
    "NBN": "NBN — NBN Co guidelines (SDU / MDU / telecom)",
}

_LABEL_INFER = [
    (re.compile(r"\bNBN\b", re.I), "NBN"),
    (re.compile(r"\b(MDU|SDU)\b.*\b(TELECOM|FIBRE|FIBER|NBN)\b|\b(TELECOM|FIBRE|FIBER|NBN)\b.*\b(MDU|SDU)\b", re.I), "NBN"),
    (re.compile(r"\bAS/?NZS\b|\bAS\s+\d", re.I), "AS_NZS"),
    (re.compile(r"\bIEC\b", re.I), "IEC"),
    (re.compile(r"\bISO\b", re.I), "ISO"),
    (re.compile(r"\bASTM\b", re.I), "ASTM"),
    (re.compile(r"\bNFPA\b", re.I), "NFPA"),
    (re.compile(r"\bAPI\b", re.I), "API"),
    (re.compile(r"\bIEEE\b", re.I), "IEEE"),
]

_ID_PREFIX_INFER = [
    (re.compile(r"^NBN", re.I), "NBN"),
    (re.compile(r"^IEC", re.I), "IEC"),
    (re.compile(r"^ISO", re.I), "ISO"),
    (re.compile(r"^ASTM", re.I), "ASTM"),
    (re.compile(r"^NFPA", re.I), "NFPA"),
    (re.compile(r"^API", re.I), "API"),
    (re.compile(r"^IEEE", re.I), "IEEE"),
    (re.compile(r"^AS", re.I), "AS_NZS"),
]


def normalize_standard_family(raw: Optional[str]) -> str:
    fam = (raw or "").strip().upper().replace("-", "_").replace("/", "_")
    if fam in ("ASNZS", "AS_NZ"):
        return "AS_NZS"
    return fam if fam in STANDARD_FAMILIES else DEFAULT_STANDARD_FAMILY


def infer_standard_family(
    codebook_id: str,
    *,
    codebook_label: Optional[str] = None,
    explicit: Optional[str] = None,
    catalogue_family: Optional[str] = None,
) -> str:
    """Resolve parser family from upload form, catalogue, or codebook id/label."""
    if explicit:
        normalized = normalize_standard_family(explicit)
        if normalized in STANDARD_FAMILIES:
            return normalized
    if catalogue_family:
        normalized = normalize_standard_family(catalogue_family)
        if normalized in STANDARD_FAMILIES:
            return normalized

    cid = (codebook_id or "").strip()
    for pattern, family in _ID_PREFIX_INFER:
        if pattern.search(cid):
            return family

    label = (codebook_label or "").strip()
    for text in (label, cid):
        if not text:
            continue
        for pattern, family in _LABEL_INFER:
            if pattern.search(text):
                return family

    return DEFAULT_STANDARD_FAMILY
