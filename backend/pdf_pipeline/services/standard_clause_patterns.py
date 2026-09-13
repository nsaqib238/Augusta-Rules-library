"""
Family-specific clause header patterns for PDF text → [CLAUSE] markers.

Each publisher uses different numbering (AS/NZS 3.2.1, ISO Annex A, NFPA 7.3.2, NBN Section 3.2, etc.).
"""
from __future__ import annotations

import re
from typing import List, Optional, Pattern, Tuple

from services.standard_families import DEFAULT_STANDARD_FAMILY, normalize_standard_family

ClauseMatch = Tuple[str, str]  # (number, title)

# PDF private-use / symbol-font garbage (common in standards publisher exports)
_PUA_GARBAGE_RE = re.compile(r"[\uE000-\uF8FF\uFFF0-\uFFFF]+")

# Shared annex / appendix headings (ISO, IEC, IEEE, NFPA, API, NBN)
_ANNEX_PATTERN = re.compile(
    r"^(Annex|Appendix)\s+([A-Z](?:\.\d+)*)\s*"
    r"(?:\((?:normative|informative)\)\s+)?"
    r"[-–—:.\s]*\s*"
    r"([A-Z][^\n]{0,120})"
)

# Dotted numeric section with optional Section/Clause prefix (ISO, IEC, IEEE, API, ASTM, NBN)
_NUMBERED_SECTION = re.compile(
    r"^(?:(?:Section|Clause|Part)\s+)?"
    r"(\d+(?:\.\d+)+|\d+)\s*"
    r"[-–—:)]\s+"
    r"([A-Z][^\n]{0,120})"
)

_NUMBERED_SECTION_ALT = re.compile(
    r"^(?:(?:Section|Clause)\s+)?"
    r"(\d+(?:\.\d+)+|\d+)\s+"
    r"([A-Z][^\n]{0,120})"
)


def sanitize_pdf_text(text: str) -> str:
    """Strip symbol-font garbage before clause/table text parsing."""
    if not text:
        return ""
    cleaned = _PUA_GARBAGE_RE.sub(" ", text)
    cleaned = cleaned.replace("\ufffd", " ")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned


_SPACED_DOTTED_NUMBER = re.compile(r"(?m)^((?:[A-Z])?\d+(?:\s*\.\s*\d+)+)")
_SENTENCE_START = re.compile(
    r"^(The|This|These|Those|A|An|Where|When|If|In|For|With|Without|To|All|Any|Each|Every|Such|It)\b"
)
_NORMATIVE_VERB = re.compile(r"\b(shall|must|may|should|is|are|has been|will be)\b", re.I)
_SECTION_PART_HEADER = re.compile(
    r"^(Section|Part)\s+([A-Z0-9]+(?:\.\d+)*)\s*[-–—:.]?\s+([A-Z][^\n]{0,120})$",
    re.I,
)
_TABLE_CAPTION = re.compile(
    r"(?i)\bTable\s+("
    r"(?:(?:NSW|QLD|VIC|SA|WA|TAS|NT|ACT)\s+)?"
    r"(?:"
    r"[A-Z]\d+[A-Z]\d+[a-z]?(?:_\d+)?"
    r"|[A-Z]?\d+(?:\.\d+)*"
    r")"
    r")\b"
)
_NCC_STATES = r"NSW|QLD|VIC|SA|WA|TAS|NT|ACT"
# C3P1, A6G10, S41C7, S2C26a, plus optional state variation prefix
_NCC_CLAUSE_ID = rf"(?:(?:{_NCC_STATES})\s+)*[A-Z]\d+[A-Z]\d+[a-z]?"
# Housing Provisions / spec subclauses: H1-4.2.12, B1-5.3.1 (not AS cites like B1-1562.3)
_NCC_HOUSING_ID = rf"(?:(?:{_NCC_STATES})\s+)?[A-Z]\d+-\d{{1,2}}(?:\.\d+){{1,4}}"
_NCC_HOUSING_STATE_DOTTED = rf"(?:{_NCC_STATES})\s+\d{{1,2}}(?:\.\d+){{1,4}}"
_NCC_HOUSING_TITLE = r"([A-Z][A-Za-z'-]{2,}[^\n]{0,400})"
_NCC_HEADER = re.compile(
    rf"^({_NCC_CLAUSE_ID})\s*[-–—:.]?\s+"
    r"(\S[^\n]{0,400})"
)
_NCC_HOUSING_HEADER = re.compile(
    rf"^({_NCC_HOUSING_ID})\s*[-–—:.]?\s+"
    rf"{_NCC_HOUSING_TITLE}"
)
_NCC_HOUSING_STATE_HEADER = re.compile(
    rf"^({_NCC_HOUSING_STATE_DOTTED})\s*[-–—:.]?\s+"
    rf"{_NCC_HOUSING_TITLE}"
)
_NCC_ID_ONLY = re.compile(rf"^({_NCC_CLAUSE_ID}|{_NCC_HOUSING_ID}|{_NCC_HOUSING_STATE_DOTTED})$")
_NCC_SPECIFICATION = re.compile(
    r"^(Specification\s+\d+)\s+"
    r"([A-Z\[\d][^\n]{0,200})"
)
_NCC_STATE_PREFIX = re.compile(rf"^({_NCC_STATES})\s+", re.I)
_NCC_PARENT = re.compile(r"^([A-Z]+\d+)[A-Z]\d+")


def normalize_spaced_dotted_number(line: str) -> str:
    """Turn '2. 4. 2 Heading' into '2.4.2 Heading' (Word/PDF spacing)."""
    if not line:
        return ""
    return _SPACED_DOTTED_NUMBER.sub(lambda m: re.sub(r"\s*\.\s*", ".", m.group(1)), line)


def looks_like_clause_title(title: str) -> bool:
    """Reject paragraph sentences that only happen to follow a number."""
    title = (title or "").strip()
    if len(title) < 3 or len(title) > 160:
        return False
    words = title.split()
    if len(words) == 1 and len(words[0]) < 3:
        return False
    if len(title) > 80 and _NORMATIVE_VERB.search(title):
        return False
    if len(title) > 90 and _SENTENCE_START.match(title):
        return False
    return True


def extract_table_caption_id(text: str) -> Optional[str]:
    """Generic Table 3.1 / Table C1 / Table S1C2a / Table H7D2_8 / Table NSW I4D12_1 caption."""
    if not text:
        return None
    m = _TABLE_CAPTION.search(text)
    if not m:
        return None
    return f"Table {m.group(1).strip()}"


def parent_clauses_from_table_id(table_number: Optional[str]) -> List[str]:
    """Likely parent clause ids for a table caption (NCC S2C26a / H7D2_8 and dotted 3.6.1)."""
    raw = re.sub(r"(?i)^table\s+", "", (table_number or "").strip())
    if not raw or raw.upper().startswith("UNNUMBERED") or raw.upper().startswith("MODAL_"):
        return []

    ncc = re.match(
        rf"^((?:{_NCC_STATES})\s+)?([A-Z]\d+[A-Z]\d+)[a-z]?(?:_\d+)?$",
        raw,
    )
    if ncc:
        state, base = (ncc.group(1) or "").strip(), ncc.group(2)
        out = [f"{state} {base}".strip()] if state else []
        out.append(base)
        return out

    housing = re.match(
        rf"^((?:{_NCC_STATES})\s+)?([A-Z]\d+-\d{{1,2}}(?:\.\d+){{1,4}})$",
        raw,
    )
    if housing:
        return [housing.group(0).strip()]

    dotted = re.match(r"^(\d+(?:\.\d+)+)$", raw)
    if dotted:
        parts = dotted.group(1).split(".")
        return [".".join(parts[:i]) for i in range(len(parts), 0, -1)]
    return []


def normalize_raw_text(raw_text: str, family: str) -> str:
    """OCR / edition fixes before clause header scanning."""
    fam = normalize_standard_family(family)
    text = sanitize_pdf_text(raw_text or "")

    if fam == "AS_NZS":
        text = re.sub(
            r"(?m)^\*+\s*(?=(?:\d+(?:\.\d+)*|[A-Z]\d+(?:\.\d+)*|Appendix\s+[A-Z])\s)",
            "",
            text,
        )

    # Common OCR: commas/spaces inside dotted numbers
    text = re.sub(r"(\d)\s*,\s*(\d)", r"\1.\2", text)
    text = "\n".join(normalize_spaced_dotted_number(ln) for ln in text.split("\n"))

    if fam in ("ISO", "IEC", "IEEE"):
        text = re.sub(r"(?m)^Clause\s+", "", text)

    if fam == "ASTM":
        text = re.sub(r"(?m)^Section\s+", "", text)

    if fam == "NFPA":
        text = re.sub(r"(?m)^CHAPTER\s+(\d+)\s*$", r"Chapter \1", text, flags=re.IGNORECASE)
        text = re.sub(r"(?m)^Section\s+", "", text)

    if fam == "NBN":
        # Normalise common NBN guideline headings
        text = re.sub(r"(?m)^SECTION\s+", "Section ", text)
        text = re.sub(r"(?m)^Section\s+(\d+(?:\.\d+)*)", r"Section \1", text)
        text = re.sub(r"(?m)^Part\s+(\d+)", r"Part \1", text)

    if fam == "API":
        text = re.sub(r"(?m)^Section\s+", "", text)

    if fam == "NCC":
        # ABCB Word/PDF often puts the id on one line and the title on the next.
        text = re.sub(
            rf"(?m)^({_NCC_CLAUSE_ID}|{_NCC_HOUSING_ID})\s*\n(?!(?:{_NCC_STATES})\s|[A-Z]\d+[A-Z]\d+|[A-Z]\d+-)",
            r"\1 ",
            text,
        )

    return text


def _annex_patterns() -> List[Pattern[str]]:
    return [_ANNEX_PATTERN]


def _dotted_section_patterns(*, allow_part_prefix: bool = False) -> List[Pattern[str]]:
    patterns: List[Pattern[str]] = [_NUMBERED_SECTION, _NUMBERED_SECTION_ALT]
    if allow_part_prefix:
        patterns.append(
            re.compile(
                r"^(Part\s+\d+(?:\.\d+)*)\s*"
                r"[-–—:.\s]*\s*"
                r"([A-Z][^\n]{0,120})"
            )
        )
    return patterns


def _patterns_for_family(family: str) -> list[re.Pattern[str]]:
    fam = normalize_standard_family(family)

    if fam == "AS_NZS":
        return [
            re.compile(
                r"^(?:\*+\s*)?"
                r"([A-Z]\d+(?:\.\d+)*|\d+(?:\.\d+)*|Appendix\s+[A-Z])\s+"
                r"([A-Z][^\n]{0,120})"
            ),
        ]

    if fam == "ISO":
        return _dotted_section_patterns() + _annex_patterns()

    if fam == "IEC":
        return (
            _dotted_section_patterns(allow_part_prefix=True)
            + _annex_patterns()
            + [
                re.compile(
                    r"^(Part\s+\d+(?:\.\d+)*)\s+"
                    r"([A-Z][^\n]{0,120})"
                ),
            ]
        )

    if fam == "ASTM":
        return _dotted_section_patterns()

    if fam == "NFPA":
        return [
            re.compile(
                r"^(?:Chapter\s+(\d+)\s+[-–—:.\s]*)?"
                r"(\d+(?:\.\d+)*|[A-Z]\.\d+(?:\.\d+)*)\s+"
                r"([A-Z][^\n]{0,120})"
            ),
            re.compile(
                r"^(\d+(?:\.\d+)*)\s+"
                r"([A-Z][^\n]{0,120})"
            ),
        ] + _annex_patterns()

    if fam == "API":
        return _dotted_section_patterns() + _annex_patterns()

    if fam == "IEEE":
        return (
            _dotted_section_patterns()
            + _annex_patterns()
            + [
                re.compile(
                    r"^(\d+(?:\.\d+)*)\s+"
                    r"([A-Z][^\n]{0,120})"
                ),
            ]
        )

    if fam == "NCC":
        return [_NCC_HEADER, _NCC_HOUSING_HEADER, _NCC_HOUSING_STATE_HEADER, _NCC_SPECIFICATION]

    if fam == "NBN":
        return [
            re.compile(
                r"^(?:Section\s+)?"
                r"(\d+(?:\.\d+)+|\d+)\s*"
                r"[-–—:)]\s+"
                r"([A-Z0-9][^\n]{0,120})"
            ),
            re.compile(
                r"^(?:Section\s+)?"
                r"(\d+(?:\.\d+)+|\d+)\s+"
                r"([A-Z0-9][^\n]{0,120})"
            ),
            re.compile(
                r"^(Part\s+\d+(?:\.\d+)*)\s*"
                r"[-–—:.\s]*\s*"
                r"([A-Z][^\n]{0,120})"
            ),
            re.compile(
                r"^(?:Requirement|Req\.?)\s+"
                r"([A-Z]?-?\d+(?:\.\d+)*)\s*"
                r"[-–—:\s]+\s*"
                r"([A-Z][^\n]{0,120})"
            ),
            re.compile(
                r"^([A-Z]{1,4}\d+(?:\.\d+)*)\s+"
                r"([A-Z][^\n]{0,120})"
            ),
        ] + _annex_patterns()

    return _patterns_for_family(DEFAULT_STANDARD_FAMILY)


def _title_valid(title: str, family: str) -> bool:
    title = (title or "").strip()
    fam = normalize_standard_family(family)
    if fam == "NCC":
        return 2 <= len(title) <= 400
    if not looks_like_clause_title(title):
        return False
    if title[0].isupper():
        return True
    if fam == "NBN" and (title[0].isdigit() or title[0].isupper()):
        return True
    return False


def match_clause_header(line: str, family: str) -> Optional[ClauseMatch]:
    """Return (clause_number, title) if line looks like a clause header."""
    s = normalize_spaced_dotted_number((line or "").strip())
    if not s or s.count("\t") >= 2:
        return None

    fam = normalize_standard_family(family)

    if fam == "NCC":
        id_only = _NCC_ID_ONLY.match(s)
        if id_only:
            return id_only.group(1), ""

    section = _SECTION_PART_HEADER.match(s)
    if section and looks_like_clause_title(section.group(3)):
        kind, number, title = section.group(1).title(), section.group(2).strip(), section.group(3).strip()
        return f"{kind} {number}", title

    for pattern in _patterns_for_family(family):
        m = pattern.match(s)
        if not m:
            continue
        groups = m.groups()

        number: Optional[str] = None
        title: Optional[str] = None

        if len(groups) == 2:
            number, title = groups[0].strip(), groups[1].strip()
        elif len(groups) == 3:
            if groups[0] in ("Annex", "Appendix"):
                number = f"{groups[0]} {groups[1]}".strip()
                title = groups[2].strip()
            elif fam == "NFPA":
                number, title = groups[1].strip(), groups[2].strip()
            else:
                number, title = groups[1].strip(), groups[2].strip()
        else:
            continue

        if not number or not title or not _title_valid(title, fam):
            continue

        if fam == "NBN" and re.match(r"(?i)^section\s", s) and not number.lower().startswith("section"):
            number = f"Section {number}"

        return number, title
    return None


def clause_level_and_parent(number: str, family: str) -> Tuple[int, Optional[str]]:
    """Derive hierarchy level and parent clause id."""
    fam = normalize_standard_family(family)
    num = (number or "").strip()

    if num.startswith("Appendix ") or num.startswith("Annex "):
        return 1, None

    if num.startswith("Section "):
        num = num.replace("Section ", "", 1)

    if num.startswith("Requirement ") or num.startswith("Req "):
        return 1, None

    if fam == "AS_NZS" and num and num[0].isupper() and not num.startswith("Appendix"):
        parts = num[1:].split(".") if len(num) > 1 else []
        level = len(parts) + 1 if parts else 2
        if "." in num:
            parent = num.rsplit(".", 1)[0]
        else:
            parent = num[0]
        return level, parent

    if fam == "NFPA" and num and num[0].isalpha() and "." in num:
        parts = num.split(".")
        level = len(parts)
        parent = ".".join(parts[:-1]) if level > 1 else None
        return level, parent

    if fam == "NCC":
        if num.startswith("Specification "):
            return 1, None
        bare = _NCC_STATE_PREFIX.sub("", num)
        if "-" in bare:
            _, numeric = bare.split("-", 1)
            level = numeric.count(".") + 2
            parent = bare.rsplit(".", 1)[0] if "." in numeric else bare.split("-", 1)[0]
            return level, parent
        parent_match = _NCC_PARENT.match(bare)
        if parent_match:
            return 2, parent_match.group(1)
        if "." in bare:
            parts = bare.split(".")
            return len(parts), ".".join(parts[:-1])
        return 2, None

    if num.startswith("Part "):
        parts = num.replace("Part ", "").split(".")
        level = len(parts)
        parent = ".".join(["Part"] + parts[:-1]) if level > 1 else None
        return level, parent

    parts = num.split(".")
    level = len(parts)
    parent = ".".join(parts[:-1]) if level > 1 else None
    return level, parent
