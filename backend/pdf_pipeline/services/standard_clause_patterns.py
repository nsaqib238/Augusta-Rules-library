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
    r"(?i)\bTable\s+([A-Z]?\d+(?:\.\d+)*|[A-Z]\d+[A-Z]\d+[a-z]?)\b"
)


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
    """Generic Table 3.1 / Table C1 / Table S1C2a caption."""
    if not text:
        return None
    m = _TABLE_CAPTION.search(text)
    if not m:
        return None
    return f"Table {m.group(1).strip()}"


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
    if not looks_like_clause_title(title):
        return False
    if title[0].isupper():
        return True
    fam = normalize_standard_family(family)
    if fam == "NBN" and (title[0].isdigit() or title[0].isupper()):
        return True
    return False


def match_clause_header(line: str, family: str) -> Optional[ClauseMatch]:
    """Return (clause_number, title) if line looks like a clause header."""
    s = normalize_spaced_dotted_number((line or "").strip())
    if not s or s.count("\t") >= 2:
        return None

    fam = normalize_standard_family(family)

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

    if num.startswith("Part "):
        parts = num.replace("Part ", "").split(".")
        level = len(parts)
        parent = ".".join(["Part"] + parts[:-1]) if level > 1 else None
        return level, parent

    parts = num.split(".")
    level = len(parts)
    parent = ".".join(parts[:-1]) if level > 1 else None
    return level, parent
