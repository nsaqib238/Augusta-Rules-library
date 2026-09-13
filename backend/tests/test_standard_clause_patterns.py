"""Tests for family-specific clause header parsing."""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

csv.field_size_limit(min(sys.maxsize, 20 * 1024 * 1024))

from pdf_pipeline.services.standard_clause_patterns import (
    extract_table_caption_id,
    match_clause_header,
    parent_clauses_from_table_id,
)
from services.codebooks import clause_record_id
from services.standard_families import infer_standard_family


def test_clause_id_uses_entered_code_name():
    assert clause_record_id("NCC2022_VOL3", "C3P1") == "NCC2022_VOL3:C3P1"
    assert clause_record_id("ncc 2022 volume three", "Part C3") == "NCC_2022_VOLUME_THREE:Part C3"
    assert clause_record_id("NCC2022_VOL3", "") == ""


def test_infer_nbn_family_from_label():
    assert infer_standard_family("CUSTOM_DOC", codebook_label="NBN MDU installation guide") == "NBN"
    assert infer_standard_family("NBN_SDU", codebook_label="NBN SDU Guidelines") == "NBN"


def test_as_nzs_clause_header():
    match = match_clause_header("3.2.1 General requirements", "AS_NZS")
    assert match == ("3.2.1", "General requirements")


def test_nfpa_clause_header():
    match = match_clause_header("7.3.2 Location.", "NFPA")
    assert match == ("7.3.2", "Location.")


def test_ieee_clause_header():
    match = match_clause_header("4.2 Scope", "IEEE")
    assert match == ("4.2", "Scope")


def test_nbn_section_header():
    match = match_clause_header("Section 3.2 - MDF room requirements", "NBN")
    assert match is not None
    assert match[0] == "Section 3.2"
    assert "MDF room" in match[1]


def test_nbn_dotted_header():
    match = match_clause_header("2.4 Minimum pathway width", "NBN")
    assert match == ("2.4", "Minimum pathway width")


def test_nbn_requirement_header():
    match = match_clause_header("Requirement R-12 Pit location setback", "NBN")
    assert match == ("R-12", "Pit location setback")


def test_spaced_dotted_number_is_normalized():
    match = match_clause_header("2. 4. 2 Protective devices", "AS_NZS")
    assert match == ("2.4.2", "Protective devices")


def test_body_sentence_is_not_a_clause_header():
    match = match_clause_header(
        "2.4.2 The protective device shall be installed as close as practicable to the origin of the circuit and shall interrupt the current.",
        "AS_NZS",
    )
    assert match is None


def test_section_part_header_is_generic():
    match = match_clause_header("Section B — Structure", "AS_NZS")
    assert match is not None
    assert match[0] == "Section B"
    assert "Structure" in match[1]


def test_infer_ncc_family():
    assert infer_standard_family("NCC2022_VOL3") == "NCC"
    assert infer_standard_family("NCC_VOL_3") == "NCC"
    assert infer_standard_family("CUSTOM", codebook_label="NCC 2022 Volume Three") == "NCC"
    assert infer_standard_family("NCC2022_VOL3", catalogue_family="NCC") == "NCC"


def test_ncc_clause_headers():
    assert match_clause_header("C3P1 Health impacts", "NCC") == ("C3P1", "Health impacts")
    assert match_clause_header("TAS A1G4 Interpretation", "NCC") == ("TAS A1G4", "Interpretation")
    assert match_clause_header("QLD B1D2 Water efficiency", "NCC") == ("QLD B1D2", "Water efficiency")
    assert match_clause_header("S3C1 Scope", "NCC") == ("S3C1", "Scope")
    assert match_clause_header("S41C7 Fire-fighting water services", "NCC") == (
        "S41C7",
        "Fire-fighting water services",
    )
    assert match_clause_header("A6G10 Class 9 buildings", "NCC") == ("A6G10", "Class 9 buildings")
    spec = match_clause_header("Specification 3 Fire hazard properties", "NCC")
    assert spec == ("Specification 3", "Fire hazard properties")
    part = match_clause_header("Part C3 On-site wastewater management", "NCC")
    assert part is not None
    assert part[0] == "Part C3"


def test_as_nzs_does_not_split_ncc_ids():
    assert match_clause_header("C3P1 Health impacts", "AS_NZS") is None


def test_ncc_variation_title_may_be_lowercase():
    match = match_clause_header(
        "S3C4 does not apply to joints, perforations, recesses or the like that are large",
        "NCC",
    )
    assert match is not None
    assert match[0] == "S3C4"


def test_ncc_housing_and_hyphen_headers():
    assert match_clause_header("H1-1.1 Introduction", "NCC") == ("H1-1.1", "Introduction")
    assert match_clause_header("H1-4.2.12 Footing and slab construction", "NCC") == (
        "H1-4.2.12",
        "Footing and slab construction",
    )
    assert match_clause_header("B1-5.3.1 Internal pressure", "NCC") == ("B1-5.3.1", "Internal pressure")
    assert match_clause_header("NSW 4.2.8 Vapour barriers", "NCC") == ("NSW 4.2.8", "Vapour barriers")


def test_ncc_ignores_measurement_and_as_cites():
    assert match_clause_header("C4-1.5 m horizontal distance from the opening.", "NCC") is None
    assert match_clause_header("E4-1.5 MW", "NCC") is None
    assert match_clause_header("B1-1562.3 clause 2.4.3.2 except for plastic sheeting.", "NCC") is None


def test_ncc_table_captions():
    assert extract_table_caption_id("Table S1C2a Fire-resistance") == "Table S1C2a"
    assert extract_table_caption_id("Table H7D2_8") == "Table H7D2_8"
    assert extract_table_caption_id("Table NSW I4D12_1") == "Table NSW I4D12_1"
    assert extract_table_caption_id("Table 13.4") == "Table 13.4"
    assert extract_table_caption_id("Table UNNUMBERED_2") is None


def test_ncc_table_links_to_parent_clause():
    assert parent_clauses_from_table_id("Table S2C26a") == ["S2C26"]
    assert parent_clauses_from_table_id("Table H7D2_8") == ["H7D2"]
    assert parent_clauses_from_table_id("Table NSW I4D12_1") == ["NSW I4D12", "I4D12"]
    assert parent_clauses_from_table_id("Table 3.6.1")[0] == "3.6.1"
    assert parent_clauses_from_table_id("Table UNNUMBERED_2") == []


def _ncc_reference_misses(path: Path):
    missed = []
    if not path.is_file():
        return missed
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            label = (row.get("unit_label") or "").strip()
            title = (row.get("title") or "").strip()
            state = (row.get("state_variation") or "").strip()
            if not label or not title:
                continue
            if len(title) < 3 or not title[0].isupper():
                continue
            if re.match(r"^(NSW|QLD|VIC|SA|WA|TAS|NT|ACT)\s+\d", title):
                continue
            bare = label.split()[-1] if label[:3] in {"NSW", "QLD", "VIC", "TAS", "ACT"} or label[:2] in {"SA", "WA", "NT"} else label
            if re.match(r"^[A-Z]\d+-\d{3,}", bare):
                continue
            if re.match(r"^[CE]\d+-1\.5$", bare):
                continue
            if state and not label.upper().startswith(f"{state.upper()} "):
                line = f"{state} {label} {title}"
                expected = f"{state} {label}"
            else:
                line = f"{label} {title}"
                expected = label
            match = match_clause_header(line, "NCC")
            got = match[0] if match else ""
            if got != expected:
                missed.append((line[:90], got))
    return missed


def test_ncc_headers_cover_volume_references():
    roots = [
        BACKEND_ROOT.parent / "csv" / "New folder",
        BACKEND_ROOT.parent / "csv",
    ]
    files = [
        "ncc2022_volume1_full_final.csv",
        "ncc2022_volume2_full_final.csv",
        "ncc2022_volume3_full_final.csv",
        "referecne ncc2022_volume3_full_final.csv",
    ]
    missed = []
    found = False
    for root in roots:
        for name in files:
            path = root / name
            if path.is_file():
                found = True
                missed.extend((name, *row) for row in _ncc_reference_misses(path))
    if not found:
        return
    assert missed == [], f"{len(missed)} reference headers not recognised, e.g. {missed[:8]}"
