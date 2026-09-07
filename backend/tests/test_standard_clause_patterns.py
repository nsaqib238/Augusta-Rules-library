"""Tests for family-specific clause header parsing."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from pdf_pipeline.services.standard_clause_patterns import match_clause_header
from services.standard_families import infer_standard_family


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
