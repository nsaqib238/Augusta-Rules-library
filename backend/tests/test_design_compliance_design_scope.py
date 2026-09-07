"""Design planning structured design scope helpers."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.design_compliance_service import (
    _deterministic_merge_items,
    _domain_merge_key,
    _drop_superseded_needs_code_items,
    _ensure_ewis_topic_row,
    _empty_design_scope,
    _extract_design_scope_from_text,
    _finalize_item_design_scope,
    _normalize_design_scope,
)


def test_extract_design_scope_from_markdown_bullets():
    text = """
Drawings:
- EWIS zone layout aligned to fire compartments
- Speaker layout and coverage schedule

Clearances:
- MECP adjacent to FIP with working access
- Minimum working space at switchboard

Coordinate with:
- Fire engineer for evacuation zones

Confirm:
- Effective height if not final
"""
    scope = _extract_design_scope_from_text(text)
    assert any("zone layout" in item.lower() for item in scope["deliverables"])
    assert any("mecp" in item.lower() or "switchboard" in item.lower() for item in scope["design_checks"])
    assert any("fire engineer" in item.lower() for item in scope["coordination"])
    assert scope["confirm"]


def test_finalize_item_design_scope_uses_fallback_for_applies():
    item = {
        "requirement_id": "req_1",
        "status": "applies",
        "summary": "EWIS applies to this building.",
        "reason": "Height and class trigger emergency warning.",
        "design_actions": [],
        "evidence_ids": ["clause-1"],
    }
    requirement = {
        "id": "req_1",
        "domain": "Emergency warning (EWIS)",
        "question": "What must the design address for EWIS?",
        "applicability": "Triggered by building height and Class 3 occupancy.",
    }
    finalized = _finalize_item_design_scope(
        item,
        requirement=requirement,
        source_result={"evidence": []},
        status="applies",
        brief="34-storey Class 3 student accommodation.",
    )
    scope = _normalize_design_scope(finalized["design_scope"])
    assert scope["deliverables"]
    assert scope["design_checks"]
    assert finalized["design_actions"]


def test_deterministic_merge_combines_same_domain_and_scopes():
    items = [
        {
            "requirement_id": "req_a",
            "domain": "Emergency warning (EWIS)",
            "status": "applies",
            "summary": "EWIS applies.",
            "reason": "Height trigger.",
            "design_scope": {"deliverables": ["Zone layout"], "design_checks": [], "coordination": [], "confirm": []},
            "design_actions": [],
            "evidence_ids": ["c1"],
            "source_section": "AS 1670.4",
        },
        {
            "requirement_id": "req_b",
            "domain": "Emergency Warning EWIS",
            "status": "conditional",
            "summary": "Confirm staging strategy.",
            "reason": "Generator TBC.",
            "design_scope": {"deliverables": [], "design_checks": ["WIP locations"], "coordination": ["Fire engineer"], "confirm": []},
            "design_actions": [],
            "evidence_ids": ["c2"],
            "source_section": "NCC Vol 1",
        },
    ]
    merged = _deterministic_merge_items(items)
    assert len(merged) == 1
    assert merged[0]["status"] == "applies"
    assert "Zone layout" in merged[0]["design_scope"]["deliverables"]
    assert "WIP locations" in merged[0]["design_scope"]["design_checks"]
    assert set(merged[0]["evidence_ids"]) == {"c1", "c2"}


def test_deterministic_merge_groups_supply_topics():
    items = [
        {
            "requirement_id": "req_a",
            "domain": "electrical supply",
            "status": "conditional",
            "summary": "Substation required.",
            "reason": "450 kVA",
            "design_scope": _empty_design_scope(),
            "design_actions": [],
            "evidence_ids": ["c1"],
            "source_section": "SIR",
        },
        {
            "requirement_id": "req_b",
            "domain": "supply and main switchboard",
            "status": "conditional",
            "summary": "MSB segregation.",
            "reason": "NCC C3D14",
            "design_scope": _empty_design_scope(),
            "design_actions": [],
            "evidence_ids": ["c2"],
            "source_section": "NCC",
        },
    ]
    merged = _deterministic_merge_items(items)
    assert len(merged) == 1
    assert set(merged[0]["evidence_ids"]) == {"c1", "c2"}


def test_deterministic_merge_groups_der_topics():
    items = [
        {
            "requirement_id": "req_a",
            "domain": "Distributed Energy Resources (PV, BESS & Embedded Gen)",
            "status": "conditional",
            "summary": "Integrated DER package.",
            "reason": "150 kW PV and 250 kWh BESS.",
            "design_scope": {"deliverables": ["DER SLD"], "design_checks": [], "coordination": [], "confirm": []},
            "design_actions": [],
            "evidence_ids": ["c1"],
            "source_section": "SIR",
        },
        {
            "requirement_id": "req_b",
            "domain": "rooftop solar photovoltaic",
            "status": "needs_code",
            "summary": "Select codes and re-run this planning section.",
            "reason": "AS/NZS 5033 missing.",
            "design_scope": _empty_design_scope(),
            "design_actions": [],
            "evidence_ids": [],
            "source_section": "AS 1670.4",
        },
        {
            "requirement_id": "req_c",
            "domain": "embedded generation",
            "status": "conditional",
            "summary": "Anti-islanding and export limits.",
            "reason": "VSIR embedded generation.",
            "design_scope": {"deliverables": [], "design_checks": ["Export limit"], "coordination": [], "confirm": []},
            "design_actions": [],
            "evidence_ids": ["c2"],
            "source_section": "SIR",
        },
    ]
    merged = _deterministic_merge_items(items)
    assert len(merged) == 1
    assert merged[0]["status"] == "conditional"
    assert "Integrated DER package." in merged[0]["summary"]
    assert set(merged[0]["evidence_ids"]) == {"c1", "c2"}


def test_drop_superseded_needs_code_items():
    items = [
        {
            "domain": "Metering & Energy Efficiency",
            "status": "conditional",
            "summary": "Section J monitoring and ODDs.",
            "reason": "NCC J9D3 applies.",
            "design_scope": {"deliverables": ["Meter SLD"], "design_checks": [], "coordination": [], "confirm": []},
            "evidence_ids": ["c1"],
        },
        {
            "domain": "energy efficiency",
            "status": "needs_code",
            "summary": "NCC Section J missing from selected codes.",
            "reason": "Re-run with NCC.",
            "design_scope": _empty_design_scope(),
            "evidence_ids": [],
        },
    ]
    filtered = _drop_superseded_needs_code_items(items)
    assert len(filtered) == 1
    assert filtered[0]["status"] == "conditional"
    assert "energy efficiency" not in filtered[0]["domain"].lower() or filtered[0]["domain"] == "Metering & Energy Efficiency"


def test_domain_merge_key_routes_1670_4_warning_to_ewis():
    assert _domain_merge_key("Fire alarm and warning systems", "AS_16704") == "ewis_warning"
    assert _domain_merge_key("Fire Detection & Alarm", "AS 1670.1") == "fire_detection"


def test_deterministic_merge_keeps_fire_detection_and_ewis_separate():
    items = [
        {
            "requirement_id": "req_fd",
            "domain": "Fire Detection & Alarm",
            "status": "conditional",
            "summary": "FDCIE and FBP for 8 storeys.",
            "reason": "Class 3 detection.",
            "design_scope": {"deliverables": ["Detection layout"], "design_checks": [], "coordination": [], "confirm": []},
            "design_actions": [],
            "evidence_ids": ["c1"],
            "source_section": "AS 1670.1",
        },
        {
            "requirement_id": "req_ewis",
            "domain": "Fire alarm and warning systems",
            "status": "conditional",
            "summary": "EWIS zones and WIP locations.",
            "reason": "Effective height trigger.",
            "design_scope": {"deliverables": ["EWIS layout"], "design_checks": ["WIP access"], "coordination": [], "confirm": []},
            "design_actions": [],
            "evidence_ids": ["c2"],
            "source_section": "AS_16704",
        },
    ]
    merged = _deterministic_merge_items(items)
    assert len(merged) == 2
    keys = {item["requirement_id"] for item in merged}
    assert "req_fd" in keys
    assert "req_ewis" in keys


def test_ensure_ewis_topic_row_adds_missing_row():
    merged_items = [
        {
            "requirement_id": "req_ewis",
            "domain": "Occupant warning",
            "status": "conditional",
            "summary": "EWIS speaker coverage and WIP layout.",
            "reason": "AS 1670.4 applies.",
            "design_scope": {"deliverables": ["EWIS layout"], "design_checks": ["WIP locations"], "coordination": [], "confirm": []},
            "design_actions": [],
            "evidence_ids": ["c1"],
            "source_sections": ["AS_16704"],
        }
    ]
    items = [
        {
            "requirement_id": "req_fd",
            "domain": "Fire Detection & Alarm",
            "status": "conditional",
            "summary": "FDCIE only.",
            "reason": "1670.1",
            "design_scope": _empty_design_scope(),
            "evidence_ids": ["c2"],
            "source_sections": ["AS 1670.1"],
        }
    ]
    result = _ensure_ewis_topic_row(items, merged_items, ["AS_16704"])
    assert len(result) == 2
    assert any("EWIS" in item["domain"] for item in result)
