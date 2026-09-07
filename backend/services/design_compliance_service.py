"""Planner-first multi-code design planning reports."""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field

from services.ask_pipeline import run_ask_with_retry
from services.codebooks import _CODEBOOK_BY_ID
from services.company_service import get_active_company_id_for_user
from services.rag_llm import chat_json
from services.rag_settings import rag_settings
from services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

MAX_SELECTED_DOCUMENTS = 24
MAX_REQUIREMENTS = 32
MAX_SCOPING_QUESTIONS = 8
MAX_RECOMMENDATIONS = 12
MAX_REPORT_EVIDENCE_CHARS = 3500

# Report size guardrails — too many topics × codes causes worker timeouts / LLM failures.
SOFT_WARN_ESTIMATED_RAG_CALLS = 40
HARD_MAX_ESTIMATED_RAG_CALLS = 72
SOFT_WARN_SELECTED_DOCUMENTS = 6
HARD_MAX_SELECTED_DOCUMENTS = 10
HARD_MAX_REQUIREMENTS_WITH_MANY_DOCS = 10

PLANNING_STATUSES = frozenset(
    {"applies", "conditional", "not_applicable", "needs_code", "conflict"}
)
LEGACY_STATUS_TO_PLANNING = {
    "compliant": "applies",
    "non_compliant": "conflict",
    "not_assessed": "conditional",
}

DISCIPLINES = frozenset(
    {"electrical", "mechanical", "fire", "hydraulics", "multi-discipline", "other"}
)

# This is routing metadata, not a fixed requirement checklist. New uploaded
# standards can still be used; they simply arrive with an unknown map entry
# until their domains are curated.
DOCUMENT_MAP: Dict[str, Dict[str, Any]] = {
    "NCC2022_VOL1": {
        "label": "NCC 2022 Volume One",
        "domains": ["classification", "fire safety", "lifts", "accessibility", "energy"],
        "sections": ["A", "C", "D", "E", "J"],
        "disciplines": ["multi-discipline", "electrical", "mechanical", "fire"],
    },
    "NCC2022_VOL2": {
        "label": "NCC 2022 Volume Two",
        "domains": ["classification", "fire safety", "energy", "accessibility"],
        "sections": ["H", "J"],
        "disciplines": ["multi-discipline", "mechanical", "fire", "hydraulics"],
    },
    "NCC2022_VOL3": {
        "label": "NCC 2022 Volume Three",
        "domains": ["plumbing", "hydraulics", "water services"],
        "sections": [],
        "disciplines": ["hydraulics", "multi-discipline"],
    },
    "NSW_SIR_2018": {
        "label": "NSW Service and Installation Rules 2018",
        "domains": ["electrical supply", "service connection", "metering"],
        "sections": [],
        "disciplines": ["electrical", "multi-discipline"],
    },
    "SA_SIR_2025": {
        "label": "South Australia Service and Installation Rules 2025",
        "domains": ["electrical supply", "service connection", "metering"],
        "sections": [],
        "disciplines": ["electrical", "multi-discipline"],
    },
    "TASNETWORK_SIR_V85": {
        "label": "TasNetworks Service and Installation Rules V8-5",
        "domains": ["electrical supply", "service connection", "metering"],
        "sections": [],
        "disciplines": ["electrical", "multi-discipline"],
    },
    "VIC_SIR_2025": {
        "label": "Victorian Service and Installation Rules 2025",
        "domains": ["electrical supply", "service connection", "metering"],
        "sections": [],
        "disciplines": ["electrical", "multi-discipline"],
    },
    "AS3000": {
        "label": "AS/NZS 3000",
        "domains": ["electrical installation", "supply", "switchboards", "segregation"],
        "sections": [],
        "disciplines": ["electrical", "multi-discipline"],
    },
    "AS1668_1": {
        "label": "AS 1668.1",
        "domains": ["smoke control", "fire engineering interfaces", "ventilation"],
        "sections": [],
        "disciplines": ["mechanical", "fire", "multi-discipline"],
    },
    "AS1668_2": {
        "label": "AS 1668.2",
        "domains": ["mechanical ventilation", "indoor air", "car park ventilation"],
        "sections": [],
        "disciplines": ["mechanical", "multi-discipline"],
    },
    "AS1670_1": {
        "label": "AS 1670.1",
        "domains": ["fire detection", "fire alarm", "fdcie", "smoke detection"],
        "sections": [],
        "disciplines": ["fire", "electrical", "multi-discipline"],
    },
    "AS1670_4": {
        "label": "AS 1670.4",
        "domains": ["emergency warning", "ewis", "occupant warning", "intercom", "wip", "sound system"],
        "sections": [],
        "disciplines": ["fire", "electrical", "multi-discipline"],
    },
    "AS2293_1": {
        "label": "AS 2293.1",
        "domains": ["emergency lighting", "exit signs"],
        "sections": [],
        "disciplines": ["fire", "electrical", "multi-discipline"],
    },
    "AS3500_1": {
        "label": "AS/NZS 3500.1",
        "domains": ["water services", "potable water", "hydraulics"],
        "sections": [],
        "disciplines": ["hydraulics", "multi-discipline"],
    },
    "AS3500_2": {
        "label": "AS/NZS 3500.2",
        "domains": ["sanitary plumbing", "wastewater", "hydraulics"],
        "sections": [],
        "disciplines": ["hydraulics", "multi-discipline"],
    },
    "AS3666_1": {
        "label": "AS/NZS 3666.1",
        "domains": ["air-handling systems", "health", "maintenance"],
        "sections": [],
        "disciplines": ["mechanical", "multi-discipline"],
    },
    "NBN_MDU": {
        "label": "NBN MDU Guidelines",
        "domains": [
            "telecommunications",
            "structured cabling",
            "MDF",
            "IDF",
            "pathways",
            "pit and pipe",
            "multi-dwelling",
        ],
        "sections": [],
        "disciplines": ["electrical", "multi-discipline"],
    },
    "NBN_SDU": {
        "label": "NBN SDU Guidelines",
        "domains": [
            "telecommunications",
            "structured cabling",
            "premises connection",
            "pit and pipe",
            "single-dwelling",
        ],
        "sections": [],
        "disciplines": ["electrical", "multi-discipline"],
    },
    "NBN_TELECOM": {
        "label": "NBN Telecom / Pit & Pipe Guidelines",
        "domains": ["telecommunications", "pathways", "pit and pipe", "lead-in"],
        "sections": [],
        "disciplines": ["electrical", "multi-discipline"],
    },
}


class DesignDocument(BaseModel):
    id: str
    codebook: str
    label: str
    filename: str = ""
    status: str = "ready_for_search"


class ScopingQuestion(BaseModel):
    id: str
    question: str
    why: str = ""


class Requirement(BaseModel):
    id: str
    domain: str
    question: str
    preferred_sources: List[str] = Field(default_factory=list)
    applicability: str = ""
    priority: str = "normal"


def normalize_discipline(value: str) -> str:
    discipline = (value or "").strip().lower()
    if discipline not in DISCIPLINES:
        raise ValueError(
            "Discipline must be electrical, mechanical, fire, hydraulics, "
            "multi-discipline, or other"
        )
    return discipline


def _clean_list(value: Any, limit: int) -> List[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))[:limit]


DESIGN_SCOPE_KEYS = ("deliverables", "design_checks", "coordination", "confirm")
DESIGN_SCOPE_LABELS = {
    "deliverables": "Drawings & specifications",
    "design_checks": "Design checks (clearances, sizing, access)",
    "coordination": "Coordinate with",
    "confirm": "Confirm before detailing",
}
MAX_DESIGN_SCOPE_ITEMS = 8


def _empty_design_scope() -> Dict[str, List[str]]:
    return {key: [] for key in DESIGN_SCOPE_KEYS}


def _normalize_design_scope(raw: Any) -> Dict[str, List[str]]:
    scope = _empty_design_scope()
    if not isinstance(raw, dict):
        return scope
    for key in DESIGN_SCOPE_KEYS:
        scope[key] = _clean_list(raw.get(key), MAX_DESIGN_SCOPE_ITEMS)
    return scope


def _design_scope_has_content(scope: Dict[str, List[str]]) -> bool:
    return any(scope.get(key) for key in DESIGN_SCOPE_KEYS)


def _flatten_design_scope(scope: Dict[str, List[str]], limit: int = 12) -> List[str]:
    actions: List[str] = []
    for key in DESIGN_SCOPE_KEYS:
        label = DESIGN_SCOPE_LABELS[key]
        for item in scope.get(key) or []:
            actions.append(f"{label}: {item}")
            if len(actions) >= limit:
                return actions
    return actions


def _merge_design_scope(primary: Dict[str, List[str]], secondary: Dict[str, List[str]]) -> Dict[str, List[str]]:
    merged = _empty_design_scope()
    for key in DESIGN_SCOPE_KEYS:
        merged[key] = _clean_list(
            (primary.get(key) or []) + (secondary.get(key) or []),
            MAX_DESIGN_SCOPE_ITEMS,
        )
    return merged


_SECTION_HINTS: Dict[str, tuple[str, ...]] = {
    "deliverables": (
        "drawing",
        "drawings",
        "schedule",
        "schedules",
        "specification",
        "specifications",
        "spec",
        "layout",
        "single-line",
        "single line",
        "schematic",
        "diagram",
        "documentation",
        "document on",
        "show on",
        "include on",
    ),
    "design_checks": (
        "clearance",
        "clearances",
        "access",
        "spacing",
        "dimension",
        "size",
        "sizing",
        "location",
        "route",
        "routing",
        "coverage",
        "capacity",
        "rating",
        "segregation",
        "setback",
        "working space",
        "maintenance",
        "intelligibility",
        "zone",
        "zoning",
    ),
    "coordination": (
        "coordinate",
        "coordination",
        "liaise",
        "consult",
        "fire engineer",
        "structural",
        "mechanical",
        "hydraulic",
        "distributor",
        "builder",
        "architect",
        "acoustic",
        "services engineer",
    ),
    "confirm": (
        "confirm",
        "tbc",
        "to be confirmed",
        "verify",
        "check whether",
        "depends on",
        "subject to",
        "proposed",
        "not confirmed",
        "uncertain",
    ),
}


def _classify_scope_line(line: str) -> str:
    lowered = line.lower()
    scores = {key: 0 for key in DESIGN_SCOPE_KEYS}
    for key, hints in _SECTION_HINTS.items():
        for hint in hints:
            if hint in lowered:
                scores[key] += 1
    best_key = max(DESIGN_SCOPE_KEYS, key=lambda key: scores[key])
    if scores[best_key] == 0:
        return "design_checks"
    return best_key


def _extract_design_scope_from_text(text: str) -> Dict[str, List[str]]:
    scope = _empty_design_scope()
    if not text.strip():
        return scope

    current_key = "design_checks"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        heading = line.rstrip(":").lower()
        if line.endswith(":") and len(line) < 90:
            if any(token in heading for token in ("drawing", "schedule", "spec", "deliver", "document")):
                current_key = "deliverables"
                continue
            if any(token in heading for token in ("clearance", "access", "check", "size", "spatial", "layout requirement")):
                current_key = "design_checks"
                continue
            if any(token in heading for token in ("coord", "liais", "consult", "interface")):
                current_key = "coordination"
                continue
            if any(token in heading for token in ("confirm", "tbc", "open item", "unknown")):
                current_key = "confirm"
                continue

        bullet = line.lstrip("-•* ").strip()
        if line.startswith(("- ", "• ", "* ")) or (line[:2].isdigit() and line[2:3] in (".", ")")):
            if line[:2].isdigit() and line[2:3] in (".", ")"):
                bullet = line[3:].strip()
            bucket = _classify_scope_line(bullet) if current_key == "design_checks" else current_key
            scope[bucket].append(bullet[:240])

    for key in DESIGN_SCOPE_KEYS:
        scope[key] = _clean_list(scope[key], MAX_DESIGN_SCOPE_ITEMS)
    return scope


def _fallback_design_scope(
    *,
    requirement: Dict[str, Any],
    source_result: Dict[str, Any],
    status: str,
    brief: str,
) -> Dict[str, List[str]]:
    snippets = [
        str(evidence.get("answer") or "").strip()
        for evidence in source_result.get("evidence") or []
        if str(evidence.get("answer") or "").strip()
    ]
    extracted = _extract_design_scope_from_text("\n".join(snippets))
    if _design_scope_has_content(extracted):
        return extracted

    domain = str(requirement.get("domain") or "this topic").strip()
    question = str(requirement.get("question") or "").strip()
    applicability = str(requirement.get("applicability") or "").strip()
    scope = _empty_design_scope()

    if status == "needs_code":
        scope["confirm"] = [
            f"Select or upload the code(s) needed for {domain}, then re-run this planning section."
        ]
        return scope

    if status == "not_applicable":
        return scope

    if status == "conditional":
        confirm_items = [applicability] if applicability else [f"Confirm whether {domain} applies to this project."]
        if _text_suggests_conditional(brief, question, applicability):
            confirm_items.append("Resolve TBC or proposed items in the brief before finalising this part of the design.")
        scope["confirm"] = _clean_list(confirm_items, MAX_DESIGN_SCOPE_ITEMS)
        scope["deliverables"] = [
            f"If confirmed, add {domain} to the drawing set and specifications using the cited code provisions."
        ]
        return scope

    if status == "conflict":
        scope["confirm"] = [
            applicability or f"Resolve conflicting brief facts for {domain} with the project team before detailing."
        ]
        return scope

    # applies — generic fallback only when synthesis/RAG did not produce scope bullets
    scope["deliverables"] = [
        f"Document {domain} on drawings and in specifications for this project.",
        f"Address the planning topic: {question[:180]}" if question else f"Capture {domain} requirements in the design package.",
    ]
    scope["design_checks"] = [
        "Check spatial requirements, clearances, access, and maintenance against the cited code provisions."
    ]
    scope["coordination"] = [
        "Coordinate interfaces with other services and disciplines where this topic crosses trade boundaries."
    ]
    if applicability:
        scope["confirm"] = [applicability[:240]]
    return scope


def _finalize_item_design_scope(
    item: Dict[str, Any],
    *,
    requirement: Dict[str, Any],
    source_result: Dict[str, Any],
    status: str,
    brief: str,
) -> Dict[str, Any]:
    scope = _normalize_design_scope(item.get("design_scope"))
    legacy_actions = _clean_list(item.get("design_actions"), MAX_DESIGN_SCOPE_ITEMS)
    if legacy_actions and not _design_scope_has_content(scope):
        scope = _merge_design_scope(scope, _extract_design_scope_from_text("\n".join(legacy_actions)))

    if status in {"applies", "conditional", "conflict", "needs_code"} and not _design_scope_has_content(scope):
        scope = _merge_design_scope(
            scope,
            _fallback_design_scope(
                requirement=requirement,
                source_result=source_result,
                status=status,
                brief=brief,
            ),
        )

    item["design_scope"] = scope
    if _design_scope_has_content(scope):
        item["design_actions"] = _flatten_design_scope(scope, 12)
    else:
        item["design_actions"] = legacy_actions
    return item

def _known_code_label(codebook_id: str) -> str:
    normalized = (codebook_id or "").strip().upper()
    entry = _CODEBOOK_BY_ID.get(normalized)
    if entry:
        return entry.get("label", normalized)
    mapped = DOCUMENT_MAP.get(normalized)
    return mapped.get("label", normalized) if mapped else normalized


def _document_is_visible(document: Dict[str, Any], user_id: str, company_id: Optional[str]) -> bool:
    if document.get("is_shared_library"):
        return True
    if document.get("user_id") == user_id:
        return True
    return bool(company_id and document.get("company_id") == company_id)


def load_selected_documents(user_id: str, document_ids: Iterable[str]) -> List[DesignDocument]:
    ids = list(dict.fromkeys(str(doc_id).strip() for doc_id in document_ids if str(doc_id).strip()))
    if not ids:
        raise ValueError("Select at least one ready document")
    if len(ids) > MAX_SELECTED_DOCUMENTS:
        raise ValueError(f"Select no more than {MAX_SELECTED_DOCUMENTS} documents")

    company_id = get_active_company_id_for_user(user_id)
    result = (
        get_supabase_client()
        .table("documents")
        .select("id, codebook, source, filename, original_filename, status, user_id, company_id, is_shared_library")
        .in_("id", ids)
        .execute()
    )
    rows = result.data or []
    by_id = {str(row.get("id")): row for row in rows}
    if len(by_id) != len(ids):
        raise ValueError("One or more selected documents could not be found")

    documents: List[DesignDocument] = []
    for doc_id in ids:
        row = by_id[doc_id]
        if not _document_is_visible(row, user_id, company_id):
            raise PermissionError("You do not have access to one of the selected documents")
        if row.get("status") != "ready_for_search":
            raise ValueError("All selected documents must be ready for search")
        codebook = str(row.get("codebook") or "").strip().upper()
        if not codebook:
            raise ValueError("Every selected document must have a codebook")
        documents.append(
            DesignDocument(
                id=doc_id,
                codebook=codebook,
                label=str(row.get("source") or _known_code_label(codebook)),
                filename=str(row.get("original_filename") or row.get("filename") or ""),
                status=str(row.get("status") or ""),
            )
        )
    return documents


def _map_context(documents: List[DesignDocument]) -> List[Dict[str, Any]]:
    context = []
    for document in documents:
        entry = DOCUMENT_MAP.get(document.codebook, {})
        context.append(
            {
                "document_id": document.id,
                "codebook_id": document.codebook,
                "label": document.label or entry.get("label") or _known_code_label(document.codebook),
                "domains": entry.get("domains", []),
                "sections": entry.get("sections", []),
                "discipline_tags": entry.get("disciplines", []),
            }
        )
    return context


def _catalogue_context() -> List[Dict[str, Any]]:
    return [
        {
            "codebook_id": codebook_id,
            "label": entry["label"],
            "domains": entry.get("domains", []),
            "sections": entry.get("sections", []),
            "discipline_tags": entry.get("disciplines", []),
        }
        for codebook_id, entry in DOCUMENT_MAP.items()
    ]


def _planner_prompt() -> str:
    return """You are the planning engine for an Australian multi-discipline design briefing.
The user supplies a discipline, an early-phase project brief (not construction drawings),
and the searchable standards selected for this run. Write like a senior engineer briefing
a junior: given THIS brief and THESE codes, explain what the project requires in the
developing design, drawings, and specifications.

This is NOT a drawing audit. Do not ask for as-built documents, IFC packages, or signed
calculations. Ask scoping questions only for project facts that materially change which
requirements apply. If the brief already states a fact, do not ask again.

PLANNING LOGIC (discipline-agnostic — adapt to the brief, do not use a fixed checklist):
1. For every system or feature named in the brief (supply, generator, EV, PV, BESS,
   fire detection, emergency lighting, metering, ventilation, hydraulics, lifts, etc.),
   create one or more design-planning topics explaining what the engineer must address
   under the selected codes.
2. Split broad systems into sub-topics when standards distinguish components — e.g. a
   "fire detection and warning" line in the brief may become separate topics for detection,
   emergency warning/intercom, manual controls, and interfaces to mechanical shutdown —
   but only when the brief or scoping makes those relevant. Let the brief drive depth.
   When AS 1670.4 is among the selected codes, always include a dedicated emergency warning /
   EWIS / occupant warning topic separate from AS 1670.1 fire detection and alarm.
3. In each requirement's "applicability", state WHY the topic is triggered by the brief
   and, if a code threshold depends on a fact not fully stated, say what to confirm
   (e.g. effective height, distributor, generator in scope, occupancy detail).
4. Phrase "question" as a planning brief, not a compliance audit:
   - Good: "What must the design address for embedded BESS connection and isolation?"
   - Bad: "Does the submitted design comply with AS 5139?"
5. Prefer more focused topics over one vague mega-topic when the brief lists several
   distinct systems.
6. Write so the later report can tell a junior what drawings, checks, and coordination
   they must produce — not just that a code applies.

After scoping answers are supplied, generate the full topic set for the chosen discipline.
Decide the count dynamically from the brief — only include topics triggered by stated
features or confirmed scoping facts.

Check coverage against selected documents and the code catalogue. Recommend additional
codes when a brief feature clearly needs a standard that is not selected. Mark
"available_not_selected" only when in the catalogue; otherwise "upload_required".

Return JSON only:
{
  "scoping_questions": [{"id": "...", "question": "...", "why": "..."}],
  "requirements": [{
    "id": "...", "domain": "...", "question": "...",
    "preferred_sources": ["CODEBOOK_ID"], "applicability": "...",
    "priority": "high|normal|low"
  }],
  "recommended_codes": [{
    "codebook_id": "...", "label": "...", "reason": "...",
    "availability": "available_not_selected|upload_required|conditional"
  }],
  "planner_notes": "..."
}
"""


def _normalize_plan(raw: Dict[str, Any], documents: List[DesignDocument]) -> Dict[str, Any]:
    selected_codes = {doc.codebook for doc in documents}
    scoping: List[Dict[str, str]] = []
    for index, item in enumerate(raw.get("scoping_questions") or []):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if question:
            scoping.append(
                {
                    "id": str(item.get("id") or f"scope_{index + 1}").strip(),
                    "question": question,
                    "why": str(item.get("why") or "").strip(),
                }
            )
    scoping = scoping[:MAX_SCOPING_QUESTIONS]

    requirements: List[Dict[str, Any]] = []
    for index, item in enumerate(raw.get("requirements") or []):
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or "General").strip()
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        requirements.append(
            {
                "id": str(item.get("id") or f"req_{index + 1}").strip(),
                "domain": domain,
                "question": question,
                "preferred_sources": [
                    source.upper()
                    for source in _clean_list(item.get("preferred_sources"), 8)
                ],
                "applicability": str(item.get("applicability") or "").strip(),
                "priority": str(item.get("priority") or "normal").strip().lower(),
            }
        )
    requirements = requirements[:MAX_REQUIREMENTS]

    recommendations: List[Dict[str, str]] = []
    seen_recommendations = set()
    for item in raw.get("recommended_codes") or []:
        if not isinstance(item, dict):
            continue
        codebook_id = str(item.get("codebook_id") or "").strip().upper()
        if not codebook_id or codebook_id in selected_codes or codebook_id in seen_recommendations:
            continue
        seen_recommendations.add(codebook_id)
        map_entry = DOCUMENT_MAP.get(codebook_id, {})
        recommendations.append(
            {
                "codebook_id": codebook_id,
                "label": str(item.get("label") or map_entry.get("label") or _known_code_label(codebook_id)),
                "reason": str(item.get("reason") or "This source may be needed for full coverage.").strip(),
                "availability": str(item.get("availability") or "upload_required").strip(),
            }
        )

    # Add deterministic recommendations for sources named by the planner but
    # omitted from its recommendation list.
    for requirement in requirements:
        for source in requirement["preferred_sources"]:
            if source in selected_codes or source in seen_recommendations:
                continue
            seen_recommendations.add(source)
            recommendations.append(
                {
                    "codebook_id": source,
                    "label": _known_code_label(source),
                    "reason": f"Required or preferred for the {requirement['domain']} review.",
                    "availability": "upload_required",
                }
            )
    return {
        "scoping_questions": scoping,
        "requirements": requirements,
        "recommended_codes": recommendations[:MAX_RECOMMENDATIONS],
        "planner_notes": str(raw.get("planner_notes") or "").strip(),
    }


def plan_report(
    *,
    discipline: str,
    question: str,
    documents: List[DesignDocument],
    scoping_answers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    normalized_discipline = normalize_discipline(discipline)
    brief = (question or "").strip()
    if not brief:
        raise ValueError("Describe the design or compliance review")
    answers = {
        str(key): str(value).strip()
        for key, value in (scoping_answers or {}).items()
        if str(key).strip() and str(value).strip()
    }
    payload = {
        "discipline": normalized_discipline,
        "brief": brief,
        "scoping_answers": answers,
        "selected_documents": [doc.model_dump() for doc in documents],
        "document_map": _map_context(documents),
        "code_catalogue": _catalogue_context(),
    }
    raw = chat_json(
        system_prompt=_planner_prompt(),
        user_content=json.dumps(payload, ensure_ascii=False),
        model=rag_settings.rag_router_model,
        temperature=0.1,
    )
    plan = _normalize_plan(raw, documents)
    plan["discipline"] = normalized_discipline
    plan["brief"] = brief
    plan["scoping_answers"] = answers
    plan["selected_documents"] = [doc.model_dump() for doc in documents]
    plan["needs_scoping"] = bool(plan["scoping_questions"] and not answers)
    return attach_report_scope_to_plan(plan, documents)


def estimate_report_scope(
    requirements: List[Dict[str, Any]],
    documents: List[DesignDocument],
) -> Dict[str, Any]:
    """Estimate how many routed code searches the report will run."""
    req_count = len(requirements)
    doc_count = len(documents)
    rag_calls = 0
    for requirement in requirements:
        routed = _route_requirement(requirement, documents)
        rag_calls += max(1, len(routed))

    level = "ok"
    if (
        rag_calls > HARD_MAX_ESTIMATED_RAG_CALLS
        or (doc_count > HARD_MAX_SELECTED_DOCUMENTS and req_count > HARD_MAX_REQUIREMENTS_WITH_MANY_DOCS)
    ):
        level = "block"
    elif rag_calls > SOFT_WARN_ESTIMATED_RAG_CALLS or doc_count > SOFT_WARN_SELECTED_DOCUMENTS:
        level = "warn"

    return {
        "requirement_count": req_count,
        "document_count": doc_count,
        "estimated_rag_calls": rag_calls,
        "level": level,
    }


def scope_reduction_message(scope: Dict[str, Any]) -> str:
    req_count = int(scope.get("requirement_count") or 0)
    doc_count = int(scope.get("document_count") or 0)
    rag_calls = int(scope.get("estimated_rag_calls") or 0)
    level = str(scope.get("level") or "ok")

    if level == "block":
        return (
            f"This report is too large to run reliably ({req_count} planning topics × "
            f"{doc_count} selected codes ≈ {rag_calls} code searches). "
            f"Deselect some codes — aim for about 3–5 key standards — or shorten the brief "
            f"and click Plan this review again."
        )
    if level == "warn":
        return (
            f"This is a large report ({req_count} topics, {doc_count} codes, ≈ {rag_calls} searches). "
            f"If generation fails or stalls, remove non-essential codes and re-plan."
        )
    return ""


def attach_report_scope_to_plan(
    plan: Dict[str, Any],
    documents: List[DesignDocument],
) -> Dict[str, Any]:
    scope = estimate_report_scope(plan.get("requirements") or [], documents)
    plan["scope"] = scope
    warning = scope_reduction_message(scope)
    if warning:
        plan["scope_warning"] = warning
    else:
        plan.pop("scope_warning", None)
    return plan


def validate_report_scope_or_raise(
    requirements: List[Dict[str, Any]],
    documents: List[DesignDocument],
) -> Dict[str, Any]:
    scope = estimate_report_scope(requirements, documents)
    if scope["level"] == "block":
        raise ValueError(scope_reduction_message(scope))
    return scope


def _route_requirement(
    requirement: Dict[str, Any], documents: List[DesignDocument]
) -> List[DesignDocument]:
    def normalize_source(source: str) -> str:
        return "".join(character for character in source.upper() if character.isalnum())

    preferred = {
        normalize_source(str(source).strip())
        for source in requirement.get("preferred_sources") or []
        if str(source).strip()
    }
    if preferred:
        return [
            doc
            for doc in documents
            if normalize_source(doc.codebook) in preferred
            or normalize_source(doc.label) in preferred
            or any(
                normalize_source(_known_code_label(doc.codebook)) == source
                for source in preferred
            )
        ]
    return documents


def _planning_ask_query(
    requirement: Dict[str, Any],
    brief: str,
    scoping_answers: Dict[str, str],
) -> str:
    parts = [
        "Context: early design phase — no construction drawings are being reviewed.",
        f"Project brief: {brief[:1400]}",
    ]
    if scoping_answers:
        parts.append(
            "Confirmed project facts: "
            + json.dumps(scoping_answers, ensure_ascii=False)
        )
    applicability = str(requirement.get("applicability") or "").strip()
    if applicability:
        parts.append(f"Why this topic applies: {applicability}")
    parts.append(
        f"Design planning topic ({requirement.get('domain')}): {requirement['question']}\n"
        "Answer as a senior engineer briefing a junior who must produce drawings and specs.\n"
        "Cover:\n"
        "1) What code requirements apply to THIS project and why\n"
        "2) What to show on drawings or schedules (layouts, zones, routes, single-lines, etc.)\n"
        "3) What to specify (equipment, performance, interfaces)\n"
        "4) Spatial checks — clearances, access, maintenance, sizing — where relevant to this topic\n"
        "5) Who to coordinate with (other disciplines, distributor, fire engineer, etc.)\n"
        "6) Project facts still to confirm before final design\n"
        "Use bullet points under short headings. Do not refuse because construction drawings are missing."
    )
    return "\n\n".join(parts)


def _run_requirement(
    user_id: str,
    requirement: Dict[str, Any],
    documents: List[DesignDocument],
    brief: str,
    scoping_answers: Dict[str, str],
) -> Dict[str, Any]:
    routed = _route_requirement(requirement, documents)
    if not routed:
        missing = ", ".join(requirement.get("preferred_sources") or []) or "a relevant selected code"
        return {
            "requirement": requirement,
            "status": "needs_code",
            "reason": f"Missing corpus: select or upload {missing}.",
            "evidence": [],
            "routed_documents": [],
        }

    ask_query = _planning_ask_query(requirement, brief, scoping_answers)
    results: List[Dict[str, Any]] = []
    for document in routed:
        try:
            result = run_ask_with_retry(
                user_id,
                ask_query,
                document.codebook,
                codebook_label=document.label,
                document_id=document.id,
                top_k=min(12, rag_settings.rag_top_k),
            )
            answer = result.get("answer") or {}
            clauses = result.get("clauses") or []
            results.append(
                {
                    "document_id": document.id,
                    "codebook": document.codebook,
                    "codebook_label": document.label,
                    "conclusion": answer.get("conclusion"),
                    "confidence": answer.get("confidence"),
                    "answer": (answer.get("answer_markdown") or "")[:1800],
                    "gaps": (answer.get("gaps") or "")[:600],
                    "clauses": [
                        {
                            "id": clause.get("id"),
                            "clause_number": clause.get("clause_number"),
                            "heading": clause.get("heading"),
                            "page_number": clause.get("page_number"),
                            "text": (clause.get("text") or "")[:900],
                        }
                        for clause in clauses[:6]
                    ],
                }
            )
        except Exception as exc:
            logger.exception("Design requirement failed: %s", requirement.get("id"))
            results.append(
                {
                    "document_id": document.id,
                    "codebook": document.codebook,
                    "codebook_label": document.label,
                    "error": str(exc),
                    "clauses": [],
                }
            )
    return {
        "requirement": requirement,
        "status": "evidence_retrieved" if any(item.get("clauses") for item in results) else "needs_code",
        "reason": "",
        "evidence": results,
        "routed_documents": [doc.model_dump() for doc in routed],
    }


def _format_clause_citation(source: Dict[str, Any]) -> str:
    label = source.get("codebook_label") or source.get("codebook") or "Source"
    clause = str(source.get("clause_number") or "").strip()
    heading = str(source.get("heading") or "").strip()
    page = source.get("page_number")

    detail_parts: List[str] = []
    if clause:
        detail_parts.append(clause)
    if heading and heading.lower() != clause.lower():
        detail_parts.append(heading)
    if not detail_parts and heading:
        detail_parts.append(heading)

    citation = f"{label} — {' — '.join(detail_parts)}" if detail_parts else label
    if page is not None:
        citation = f"{citation}, p. {page}"
    return citation


def _compact_evidence_for_synthesis(
    requirement_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Trim retrieval payload so synthesis JSON fits model output limits."""
    compact: List[Dict[str, Any]] = []
    for result in requirement_results:
        requirement = result.get("requirement") or {}
        clauses: List[Dict[str, Any]] = []
        for evidence in result.get("evidence") or []:
            for clause in (evidence.get("clauses") or [])[:3]:
                clauses.append(
                    {
                        "id": clause.get("id"),
                        "codebook": evidence.get("codebook_label") or evidence.get("codebook"),
                        "clause_number": clause.get("clause_number"),
                        "heading": clause.get("heading"),
                        "text": (clause.get("text") or "")[:400],
                    }
                )
                if len(clauses) >= 8:
                    break
            if len(clauses) >= 8:
                break
        compact.append(
            {
                "requirement_id": requirement.get("id"),
                "domain": requirement.get("domain"),
                "question": requirement.get("question"),
                "pre_status": result.get("status"),
                "reason": (result.get("reason") or "")[:400],
                "conclusions": [
                    str(item.get("conclusion") or "").strip()
                    for item in result.get("evidence") or []
                    if str(item.get("conclusion") or "").strip()
                ][:4],
                "answer_snippets": [
                    (str(item.get("answer") or "").strip())[:500]
                    for item in result.get("evidence") or []
                    if str(item.get("answer") or "").strip()
                ][:2],
                "gaps": [
                    (str(item.get("gaps") or "").strip())[:300]
                    for item in result.get("evidence") or []
                    if str(item.get("gaps") or "").strip()
                ][:2],
                "clauses": clauses,
            }
        )
    return compact


_UNCERTAINTY_MARKERS = (
    "not confirmed",
    "tbc",
    "to be confirmed",
    "proposed but",
    "subject to",
    "may include",
    "if required",
    "depends on",
    "confirm ",
    "to confirm",
    "uncertain",
    "under review",
)


def _text_suggests_conditional(*parts: str) -> bool:
    combined = " ".join(part.strip() for part in parts if part and str(part).strip()).lower()
    if not combined:
        return False
    return any(marker in combined for marker in _UNCERTAINTY_MARKERS)


def _build_item_from_evidence(
    requirement: Dict[str, Any],
    source_result: Dict[str, Any],
    *,
    brief: str = "",
    scoping_answers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    evidence_ids = [
        str(clause.get("id"))
        for evidence in source_result.get("evidence") or []
        for clause in evidence.get("clauses") or []
        if clause.get("id")
    ][:8]
    gaps = [
        str(evidence.get("gaps") or "").strip()
        for evidence in source_result.get("evidence") or []
        if str(evidence.get("gaps") or "").strip()
    ]
    snippets = [
        str(evidence.get("answer") or "").strip()
        for evidence in source_result.get("evidence") or []
        if str(evidence.get("answer") or "").strip()
    ]
    applicability = str(requirement.get("applicability") or "").strip()
    corpus_reason = (source_result.get("reason") or "").strip()
    if corpus_reason.startswith("Missing corpus"):
        status = "needs_code"
    elif evidence_ids:
        status = "conditional" if _text_suggests_conditional(
            applicability,
            requirement.get("question") or "",
            brief,
            json.dumps(scoping_answers or {}, ensure_ascii=False),
        ) else "applies"
    else:
        status = "not_applicable"

    reason = applicability or corpus_reason
    if not reason and gaps:
        reason = gaps[0][:500]
    elif not reason and status == "applies":
        reason = "This topic is triggered by features stated in the project brief."

    summary = ""
    if snippets:
        summary = snippets[0].split("\n\n")[0][:600]
    elif applicability:
        summary = applicability[:600]
    elif status == "needs_code":
        summary = "Select or upload the relevant code to retrieve design requirements for this topic."
    else:
        summary = "No applicable code provisions were identified for this topic on the selected corpus."

    design_scope = _extract_design_scope_from_text(snippets[0] if snippets else "")
    design_actions = _flatten_design_scope(design_scope) or _extract_design_actions(snippets[0] if snippets else "")

    return {
        "requirement_id": str(requirement.get("id")),
        "status": status,
        "summary": summary,
        "reason": reason or "Confirm applicability during detailed design.",
        "design_scope": design_scope,
        "design_actions": design_actions,
        "evidence_ids": evidence_ids,
    }


def _extract_design_actions(answer_text: str, limit: int = 5) -> List[str]:
    if not answer_text:
        return []
    actions: List[str] = []
    for line in answer_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "• ", "* ")):
            actions.append(stripped.lstrip("-•* ").strip()[:240])
        elif stripped[:2].isdigit() and stripped[2:3] in (".", ")"):
            actions.append(stripped[3:].strip()[:240])
        if len(actions) >= limit:
            break
    return actions


def _normalize_planning_status(
    status: str,
    source_result: Dict[str, Any],
    evidence_ids: Iterable[str],
    *,
    requirement: Optional[Dict[str, Any]] = None,
    brief: str = "",
    scoping_answers: Optional[Dict[str, str]] = None,
) -> str:
    normalized = LEGACY_STATUS_TO_PLANNING.get(status.strip().lower(), status.strip().lower())
    if normalized not in PLANNING_STATUSES:
        normalized = "conditional"

    corpus_reason = (source_result.get("reason") or "").strip()
    has_evidence = any(str(item).strip() for item in evidence_ids)
    if corpus_reason.startswith("Missing corpus"):
        return "needs_code"
    if normalized == "applies" and not has_evidence:
        return "conditional"
    applicability = str((requirement or {}).get("applicability") or "")
    question = str((requirement or {}).get("question") or "")
    if normalized == "applies" and _text_suggests_conditional(
        applicability,
        question,
        brief,
        json.dumps(scoping_answers or {}, ensure_ascii=False),
    ):
        return "conditional"
    return normalized


def _classify_report(
    *,
    discipline: str,
    brief: str,
    scoping_answers: Dict[str, str],
    plan: Dict[str, Any],
    requirement_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    evidence_payload = _compact_evidence_for_synthesis(requirement_results)
    system = """You are the synthesizer for an early-phase design planning briefing.
The engineer has a project brief (not construction drawings). Use the retrieved code
evidence and scoping answers to explain what applies to THIS project and what must be
addressed in the developing design — like a senior engineer advising a junior who must
produce drawings, schedules, and specifications.

Do NOT audit submitted drawings. Do NOT mark items as unassessable because drawings are
missing. Instead explain what the design must include or coordinate.

STATUS RULES:
- "applies" — the brief (or confirmed scoping) clearly triggers this topic; cite what to
  design under the selected codes.
- "conditional" — the topic may apply but a key fact is missing, uncertain, or marked
  "proposed / not confirmed / TBC" in the brief or scoping; say exactly what to confirm.
  Use conditional even when good clause evidence exists.
- "not_applicable" — not triggered by this brief on the evidence supplied.
- "needs_code" — relevant standard not in the selected/uploaded set.
- "conflict" — brief and scoping appear to disagree, or the brief conflicts with cited
  requirements; name both sides briefly.

DESIGN SCOPE (mandatory for "applies", "conditional", "needs_code", and "conflict"):
For each such item, populate design_scope so a junior knows what to do next:
- deliverables: drawings, schedules, specs, diagrams to produce (e.g. zone layout, single-line, pit plan)
- design_checks: clearances, access, sizing, routing, coverage, segregation — spatial/technical checks
- coordination: other disciplines, distributor, fire engineer, builder, etc.
- confirm: project facts still TBC before final design

Use 2–6 concrete bullets per populated category. Be specific to the topic (EWIS, switchboard,
kiosk substation, BESS, sprinklers, AHU plant, etc.) using evidence and brief — not generic
"comply with the code" lines. Omit categories that genuinely do not apply.

Return JSON only:
{
  "executive_summary": "...",
  "items": [{
    "requirement_id": "...",
    "status": "applies|conditional|not_applicable|needs_code|conflict",
    "summary": "what the engineer must design/coordinate",
    "reason": "why this applies based on brief features, or what to confirm if conditional",
    "design_scope": {
      "deliverables": ["..."],
      "design_checks": ["..."],
      "coordination": ["..."],
      "confirm": ["..."]
    },
    "design_actions": [],
    "evidence_ids": ["actual clause ids from supplied evidence"]
  }],
  "cross_domain_notes": ["..."],
  "open_items": ["facts to confirm during design — not requests for construction drawings"],
  "recommendations": ["..."]
}
"""
    raw = chat_json(
        system_prompt=system,
        user_content=json.dumps(
            {
                "discipline": discipline,
                "brief": brief[:2500],
                "scoping_answers": scoping_answers,
                "selected_codes": [
                    {
                        "codebook": doc.get("codebook"),
                        "label": doc.get("label"),
                    }
                    for doc in (plan.get("selected_documents") or [])
                ],
                "requirements": evidence_payload,
            },
            ensure_ascii=False,
        ),
        model=rag_settings.rag_synthesis_model,
        temperature=0.1,
    )
    return raw


def _planning_status_label(status: str) -> str:
    labels = {
        "applies": "Applies to this project",
        "conditional": "Conditional",
        "not_applicable": "Not applicable",
        "needs_code": "Code not selected",
        "conflict": "Potential conflict",
        "compliant": "Applies to this project",
        "non_compliant": "Potential conflict",
        "not_assessed": "Conditional",
    }
    return labels.get(status, "Conditional")


def _build_markdown(
    *,
    discipline: str,
    brief: str,
    plan: Dict[str, Any],
    report: Dict[str, Any],
) -> str:
    lines = [
        f"# Design Planning Review — {discipline.title()}",
        "",
        f"**Brief:** {brief}",
        f"**Selected codes:** {', '.join(doc['label'] for doc in plan.get('selected_documents', []))}",
        "",
        "## Executive summary",
        report.get("executive_summary")
        or "A code-based design planning briefing was generated from the project brief.",
        "",
        "## Design requirements checklist",
    ]
    sources_by_id = {
        str(source.get("id")): source for source in report.get("sources") or [] if source.get("id")
    }
    for item in report.get("items") or []:
        status = _planning_status_label(str(item.get("status") or "conditional"))
        domain = str(item.get("domain") or item.get("requirement_id") or "Requirement").replace("_", " ")
        lines.extend(
            [
                "",
                f"### {domain} — **{status}**",
                item.get("summary") or "",
                f"**Why it applies:** {item.get('reason') or 'Confirm during detailed design.'}",
            ]
        )
        design_actions = _clean_list(item.get("design_actions"), 8)
        design_scope = _normalize_design_scope(item.get("design_scope"))
        if design_actions and not _design_scope_has_content(design_scope):
            design_scope = _merge_design_scope(
                design_scope,
                _extract_design_scope_from_text("\n".join(design_actions)),
            )
        if _design_scope_has_content(design_scope):
            lines.append("**Your design package should address:**")
            for key in DESIGN_SCOPE_KEYS:
                entries = design_scope.get(key) or []
                if not entries:
                    continue
                lines.append(f"*{DESIGN_SCOPE_LABELS[key]}:*")
                lines.extend(f"- {entry}" for entry in entries)
        elif design_actions:
            lines.append("**Address in drawings/specs:**")
            lines.extend(f"- {action}" for action in design_actions)
        evidence_ids = item.get("evidence_ids") or []
        if evidence_ids:
            citations = [
                _format_clause_citation(sources_by_id[evidence_id])
                for evidence_id in evidence_ids
                if evidence_id in sources_by_id
            ]
            if citations:
                lines.append(f"**Key clauses:** {'; '.join(citations)}")
    if report.get("open_items"):
        lines.extend(["", "## Confirm during design"])
        lines.extend(f"- {item}" for item in report["open_items"])
    if report.get("recommendations"):
        lines.extend(["", "## Recommendations"])
        lines.extend(f"- {item}" for item in report["recommendations"])
    if report.get("cross_domain_notes"):
        lines.extend(["", "## Cross-domain notes"])
        lines.extend(f"- {item}" for item in report["cross_domain_notes"])
    if report.get("sources"):
        lines.extend(["", "## Source index"])
        for index, source in enumerate(report["sources"], start=1):
            lines.append(f"- [{index}] {_format_clause_citation(source)}")
    lines.extend(
        [
            "",
            "## Disclaimer",
            "AI-assisted design planning only. This briefing is not a compliance certificate, "
            "construction approval, or professional sign-off.",
        ]
    )
    return "\n".join(lines)


def run_report(
    *,
    user_id: str,
    discipline: str,
    question: str,
    documents: List[DesignDocument],
    plan: Dict[str, Any],
    scoping_answers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    normalized_discipline = normalize_discipline(discipline)
    requirements = plan.get("requirements") or []
    if not requirements:
        raise ValueError("The planner did not generate any review requirements")
    if len(requirements) > MAX_REQUIREMENTS:
        requirements = requirements[:MAX_REQUIREMENTS]
    validate_report_scope_or_raise(requirements, documents)
    answers = {
        str(key): str(value).strip()
        for key, value in (scoping_answers or {}).items()
        if str(key).strip() and str(value).strip()
    }

    results: List[Optional[Dict[str, Any]]] = [None] * len(requirements)
    workers = min(4, len(requirements))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="design-review") as pool:
        futures = {
            pool.submit(
                _run_requirement,
                user_id,
                requirement,
                documents,
                question,
                answers,
            ): index
            for index, requirement in enumerate(requirements)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    requirement_results = [result for result in results if result is not None]

    try:
        synthesized = _classify_report(
            discipline=normalized_discipline,
            brief=question,
            scoping_answers=answers,
            plan=plan,
            requirement_results=requirement_results,
        )
    except Exception:
        logger.exception("Design compliance synthesis failed; using evidence fallback")
        synthesized = {
            "executive_summary": (
                "Automated synthesis could not complete in one pass. Each topic below "
                "was drafted from retrieved code provisions and the project brief."
            ),
            "items": [],
            "cross_domain_notes": [],
            "open_items": [],
            "recommendations": [],
        }

    result_by_id = {
        str(item["requirement"].get("id")): item for item in requirement_results
    }
    normalized_items = []
    for requirement in requirements:
        req_id = str(requirement.get("id"))
        item = next(
            (candidate for candidate in synthesized.get("items", []) if str(candidate.get("requirement_id")) == req_id),
            None,
        )
        source_result = result_by_id.get(req_id, {})
        evidence_ids = {
            str(clause.get("id"))
            for evidence in source_result.get("evidence") or []
            for clause in evidence.get("clauses") or []
            if clause.get("id")
        }
        if not item:
            item = _build_item_from_evidence(
                requirement,
                source_result or {},
                brief=question,
                scoping_answers=answers,
            )
        status = _normalize_planning_status(
            str(item.get("status") or "conditional"),
            source_result or {},
            evidence_ids,
            requirement=requirement,
            brief=question,
            scoping_answers=answers,
        )
        item["status"] = status
        item["domain"] = requirement.get("domain")
        item["question"] = requirement.get("question")
        filtered_ids = [
            evidence_id
            for evidence_id in _clean_list(item.get("evidence_ids"), 12)
            if evidence_id in evidence_ids
        ]
        if not filtered_ids and status == "applies":
            filtered_ids = list(evidence_ids)[:6]
        item["evidence_ids"] = filtered_ids
        item = _finalize_item_design_scope(
            item,
            requirement=requirement,
            source_result=source_result or {},
            status=status,
            brief=question,
        )
        normalized_items.append(item)

    final_report = {
        "executive_summary": str(
            synthesized.get("executive_summary")
            or "A code-based design planning briefing was generated from the project brief."
        ),
        "items": normalized_items,
        "cross_domain_notes": _clean_list(synthesized.get("cross_domain_notes"), 20),
        "open_items": _clean_list(synthesized.get("open_items"), 30),
        "recommendations": _clean_list(synthesized.get("recommendations"), 30),
    }
    sources = []
    seen_source_ids = set()
    for result in requirement_results:
        for evidence in result.get("evidence") or []:
            for clause in evidence.get("clauses") or []:
                source_id = str(clause.get("id") or "").strip()
                if not source_id or source_id in seen_source_ids:
                    continue
                seen_source_ids.add(source_id)
                sources.append(
                    {
                        "id": source_id,
                        "document_id": evidence.get("document_id"),
                        "codebook": evidence.get("codebook"),
                        "codebook_label": evidence.get("codebook_label"),
                        "clause_number": clause.get("clause_number"),
                        "heading": clause.get("heading"),
                        "page_number": clause.get("page_number"),
                    }
                )
    final_report["sources"] = sources
    final_report["report_markdown"] = _build_markdown(
        discipline=normalized_discipline,
        brief=question,
        plan=plan,
        report=final_report,
    )
    return {
        "discipline": normalized_discipline,
        "brief": question,
        "scoping_answers": answers,
        "plan": plan,
        "report": final_report,
        "requirement_results": requirement_results,
    }


STATUS_RANK = {
    "conflict": 5,
    "applies": 4,
    "conditional": 3,
    "needs_code": 2,
    "not_applicable": 1,
}

_DOMAIN_STOP_WORDS = frozenset(
    {
        "and",
        "the",
        "for",
        "with",
        "from",
        "into",
        "design",
        "requirements",
        "requirement",
        "electrical",
        "installation",
        "system",
        "systems",
        "services",
        "service",
        "project",
        "building",
    }
)

_DOMAIN_TOPIC_HINTS: Tuple[Tuple[str, frozenset], ...] = (
    ("supply_switchboard", frozenset({"supply", "switchboard", "substation", "incoming", "msb", "point", "kiosk", "demand", "diversity", "kva", "scheduling"})),
    ("emergency_lighting", frozenset({"emergency", "lighting", "exit", "sign", "signage", "2293"})),
    ("ewis_warning", frozenset({"ewis", "warning", "intercom", "wip", "ewcie", "occupant", "1670.4", "speaker", "sound"})),
    ("fire_detection", frozenset({"detection", "fdcie", "fip", "smoke", "detector", "detectors", "1670.1"})),
    ("ev_charging", frozenset({"ev", "charging", "charger", "vehicle", "j9d4"})),
    (
        "der_embedded",
        frozenset(
            {
                "embedded",
                "generation",
                "pv",
                "photovoltaic",
                "solar",
                "rooftop",
                "bess",
                "battery",
                "storage",
                "inverter",
                "der",
                "distributed",
                "renewable",
            }
        ),
    ),
    ("metering", frozenset({"metering", "meter", "meters", "submeter", "sub-meter", "odd", "odds"})),
    ("essential_services", frozenset({"essential", "safety", "fire", "pump", "pumps"})),
    ("lift", frozenset({"lift", "lifts", "elevator", "vertical", "transportation"})),
    ("energy_efficiency", frozenset({"energy", "efficiency", "section", "monitoring", "j9d3", "j9d5"})),
    ("telecommunications", frozenset({"telecommunication", "telecommunications", "nbn", "mdf", "idf"})),
    ("earthing", frozenset({"earthing", "earth", "rcd", "bonding", "men"})),
)

_TOPIC_SUPERSESSION_GROUPS: Tuple[frozenset, ...] = (
    frozenset({"der_embedded", "solar_pv", "bess_storage", "embedded", "generation"}),
    frozenset({"supply_switchboard", "maximum_demand"}),
    frozenset({"metering", "energy_efficiency"}),
    frozenset({"essential_services", "electrical", "installation"}),
)


def _domain_tokens(domain: str) -> set:
    normalized = re.sub(r"[^a-z0-9]+", " ", (domain or "").lower()).strip()
    return {
        token
        for token in normalized.split()
        if len(token) > 2 and token not in _DOMAIN_STOP_WORDS
    }


def _domain_merge_key(domain: str, source_hint: str = "") -> str:
    tokens = _domain_tokens(domain)
    hint_tokens = _domain_tokens(source_hint)
    combined_tokens = tokens | hint_tokens
    if not combined_tokens:
        return "general"

    if _source_is_1670_4(source_hint) and not _source_is_1670_1(source_hint):
        detection_specific = combined_tokens & frozenset(
            {"fdcie", "detector", "detectors", "smoke", "fip", "detection"}
        )
        if not detection_specific or combined_tokens & frozenset(
            {"ewis", "warning", "intercom", "wip", "ewcie", "occupant", "speaker", "sound", "voice", "alarm"}
        ):
            return "ewis_warning"

    if combined_tokens & frozenset({"ewis", "wip", "ewcie", "intercom"}):
        return "ewis_warning"

    for topic_key, fingerprint in _DOMAIN_TOPIC_HINTS:
        if combined_tokens & fingerprint:
            return topic_key
    return " ".join(sorted(combined_tokens)[:5])


def _source_is_1670_4(source_hint: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", (source_hint or "").lower())
    return "16704" in normalized


def _source_is_1670_1(source_hint: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", (source_hint or "").lower())
    return "16701" in normalized and "16704" not in normalized


def _section_labels_include_1670_4(section_labels: Iterable[str]) -> bool:
    return any(_source_is_1670_4(label) for label in section_labels)


def _item_merge_key(item: Dict[str, Any]) -> str:
    source_hint = " ".join(_clean_list(item.get("source_sections"), 8))
    if not source_hint:
        source_hint = str(item.get("source_section") or "")
    return _domain_merge_key(str(item.get("domain") or ""), source_hint)


def _normalize_planning_status_value(status: str) -> str:
    normalized = LEGACY_STATUS_TO_PLANNING.get((status or "").strip().lower(), (status or "").strip().lower())
    if normalized not in PLANNING_STATUSES:
        return "conditional"
    return normalized


def _pick_stronger_status(left: str, right: str) -> str:
    left_status = _normalize_planning_status_value(left)
    right_status = _normalize_planning_status_value(right)
    if STATUS_RANK.get(right_status, 0) > STATUS_RANK.get(left_status, 0):
        return right_status
    return left_status


def _item_has_code_evidence(item: Dict[str, Any]) -> bool:
    status = _normalize_planning_status_value(str(item.get("status") or "conditional"))
    if status == "needs_code":
        return False
    return bool(_clean_list(item.get("evidence_ids"), 12))


def _item_merge_weight(item: Dict[str, Any]) -> Tuple[int, int, int, int]:
    status = _normalize_planning_status_value(str(item.get("status") or "conditional"))
    evidence_count = len(_clean_list(item.get("evidence_ids"), 12))
    scope_score = 1 if _design_scope_has_content(_normalize_design_scope(item.get("design_scope"))) else 0
    summary_len = len(str(item.get("summary") or "").strip())
    return (STATUS_RANK.get(status, 0), evidence_count, scope_score, summary_len)


def _topic_supersession_group(merge_key: str) -> frozenset:
    for group in _TOPIC_SUPERSESSION_GROUPS:
        if merge_key in group:
            return group
    return frozenset({merge_key})


def _topic_is_code_backed(item: Dict[str, Any]) -> bool:
    status = _normalize_planning_status_value(str(item.get("status") or "conditional"))
    if status not in {"applies", "conditional", "conflict"}:
        return False
    return _item_has_code_evidence(item) or _design_scope_has_content(
        _normalize_design_scope(item.get("design_scope"))
    )


def _drop_superseded_needs_code_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    covered_groups: set = set()
    for item in items:
        if not _topic_is_code_backed(item):
            continue
        covered_groups.add(frozenset(_topic_supersession_group(_item_merge_key(item))))

    filtered: List[Dict[str, Any]] = []
    for item in items:
        status = _normalize_planning_status_value(str(item.get("status") or "conditional"))
        item_group = frozenset(_topic_supersession_group(_item_merge_key(item)))
        if status == "needs_code" and any(item_group & covered for covered in covered_groups):
            continue
        filtered.append(item)
    return filtered


def _planning_runs_payload(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    runs: List[Dict[str, Any]] = []
    for index, section in enumerate(sections, start=1):
        label = str(section.get("label") or f"Run {index}").strip()
        report = section.get("report") or {}
        item_count = len(report.get("items") or [])
        runs.append(
            {
                "run_number": index,
                "codes_searched": label,
                "checklist_items": item_count,
                "note": "This run searched ONLY its code bundle — not every standard in the project.",
            }
        )
    return runs


def _collect_items_from_sections(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    for section in sections:
        section_label = str(section.get("label") or "Selected codes").strip()
        report = section.get("report") or {}
        for item in report.get("items") or []:
            if not isinstance(item, dict):
                continue
            collected.append(
                {
                    **item,
                    "source_section": section_label,
                    "source_requirement_id": str(item.get("requirement_id") or ""),
                }
            )
    return collected


def _deterministic_merge_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged_by_domain: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for item in items:
        domain = str(item.get("domain") or item.get("requirement_id") or "Requirement").strip()
        merge_key = _item_merge_key(item)
        if merge_key not in merged_by_domain:
            merged_by_domain[merge_key] = {
                "requirement_id": str(item.get("requirement_id") or merge_key),
                "domain": domain,
                "question": str(item.get("question") or "").strip(),
                "status": _normalize_planning_status_value(str(item.get("status") or "conditional")),
                "summary": str(item.get("summary") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
                "design_scope": _normalize_design_scope(item.get("design_scope")),
                "design_actions": _clean_list(item.get("design_actions"), 12),
                "evidence_ids": _clean_list(item.get("evidence_ids"), 12),
                "source_sections": _clean_list([item.get("source_section")], 8),
            }
            order.append(merge_key)
            continue

        existing = merged_by_domain[merge_key]
        existing["status"] = _pick_stronger_status(existing["status"], str(item.get("status") or "conditional"))
        existing["design_scope"] = _merge_design_scope(
            _normalize_design_scope(existing.get("design_scope")),
            _normalize_design_scope(item.get("design_scope")),
        )
        existing["design_actions"] = _clean_list(
            (existing.get("design_actions") or []) + _clean_list(item.get("design_actions"), 12),
            12,
        )
        existing["evidence_ids"] = _clean_list(
            (existing.get("evidence_ids") or []) + _clean_list(item.get("evidence_ids"), 12),
            12,
        )
        existing["source_sections"] = _clean_list(
            (existing.get("source_sections") or []) + [item.get("source_section")],
            8,
        )
        incoming_summary = str(item.get("summary") or "").strip()
        if _item_merge_weight(item) > _item_merge_weight(existing):
            existing["summary"] = incoming_summary or str(existing.get("summary") or "").strip()
            existing["domain"] = domain or str(existing.get("domain") or "").strip()
            incoming_reason = str(item.get("reason") or "").strip()
            if incoming_reason:
                existing["reason"] = incoming_reason
        elif len(domain) > len(str(existing.get("domain") or "")):
            existing["domain"] = domain
        incoming_reason = str(item.get("reason") or "").strip()
        if incoming_reason and incoming_reason not in str(existing.get("reason") or ""):
            existing["reason"] = " ".join(
                part for part in [str(existing.get("reason") or "").strip(), incoming_reason] if part
            )[:900]
        if not existing.get("question") and item.get("question"):
            existing["question"] = str(item.get("question") or "").strip()

    merged_list = [merged_by_domain[key] for key in order]
    return _drop_superseded_needs_code_items(merged_list)


def _collect_sources_from_sections(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    seen_ids = set()
    for section in sections:
        report = section.get("report") or {}
        for source in report.get("sources") or []:
            if not isinstance(source, dict):
                continue
            source_id = str(source.get("id") or "").strip()
            if not source_id or source_id in seen_ids:
                continue
            seen_ids.add(source_id)
            sources.append(source)
    return sources


def _compact_items_for_final_synthesis(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compact: List[Dict[str, Any]] = []
    for item in items:
        evidence_ids = _clean_list(item.get("evidence_ids"), 12)
        compact.append(
            {
                "domain": item.get("domain"),
                "status": item.get("status"),
                "summary": (str(item.get("summary") or ""))[:500],
                "reason": (str(item.get("reason") or ""))[:400],
                "design_scope": _normalize_design_scope(item.get("design_scope")),
                "source_sections": _clean_list(item.get("source_sections"), 6),
                "evidence_count": len(evidence_ids),
                "has_code_evidence": _item_has_code_evidence(item),
                "merge_key": _item_merge_key(item),
            }
        )
    return compact


def _synthesize_final_report(
    *,
    discipline: str,
    brief: str,
    scoping_answers: Dict[str, str],
    section_labels: List[str],
    sections: List[Dict[str, Any]],
    merged_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    run_count = len(sections)
    system = f"""You are merging {run_count} SEPARATE design planning runs into ONE final briefing
for a junior engineer.

IMPORTANT CONTEXT:
- Each run is an independent report generated from a different code bundle search on the SAME project brief.
- A run searched ONLY the codes listed in its bundle — not the full project code set.
- The same topic often appears in multiple runs with different completeness:
  • Run A (with the relevant code selected): status applies/conditional, clause evidence, full design_scope
  • Run B (without that code): status needs_code, says "code not selected" or "re-run"

SUPERSESSION RULE (critical):
- When multiple rows cover the same topic, the row backed by a code search SUPERSEDES weaker rows.
- Prefer applies/conditional/conflict rows with evidence_count > 0 and concrete design_scope.
- DROP needs_code rows for a topic if ANY other run already covers that topic with code-backed
  applies/conditional analysis (even if the needs_code row lists missing codes that were actually searched elsewhere).
- Merge overlapping topics into ONE checklist row (supply/substation/demand, PV/BESS/embedded generation,
  metering/Section J, emergency lighting, EV, essential services).
- Keep **Fire detection & alarm (AS 1670.1)** and **Emergency warning / EWIS (AS 1670.4)** as SEPARATE rows
  when the 1670.4 planning run was merged. Do not fold EWIS, WIP, speakers, or occupant warning into the
  detection row — they are a distinct design package under AS 1670.4.
- Do NOT output duplicate near-identical rows.
- Do NOT tell the user to "re-run" or "select codes" in the final report — that is partial-run language.
- needs_code is ONLY for topics genuinely absent from ALL merged runs (e.g. telecommunications if no comms code was ever run).

OUTPUT RULES:
- Write one executive_summary (2–4 paragraphs) covering the whole project across all codes.
- Produce ONE unified checklist — target roughly 12–18 rows for a typical multi-code project.
- Do NOT repeat the project brief or scoping answers inside item summaries.
- Do NOT include per-run headers or duplicate executive summaries.
- Keep status per item (applies, conditional, not_applicable, needs_code, conflict).
- For applies/conditional items, keep or refine design_scope with concrete deliverables, design_checks,
  coordination, and confirm lists.
- open_items: deduplicated facts to confirm during design.
- cross_domain_notes: interfaces between systems/disciplines.
- recommendations: optional next steps (extra codes, specialist engagement).

Return JSON only:
{{
  "executive_summary": "...",
  "items": [{{
    "domain": "...",
    "status": "applies|conditional|not_applicable|needs_code|conflict",
    "summary": "...",
    "reason": "...",
    "design_scope": {{
      "deliverables": ["..."],
      "design_checks": ["..."],
      "coordination": ["..."],
      "confirm": ["..."]
    }}
  }}],
  "cross_domain_notes": ["..."],
  "open_items": ["..."],
  "recommendations": ["..."]
}}
"""
    return chat_json(
        system_prompt=system,
        user_content=json.dumps(
            {
                "discipline": discipline,
                "brief": brief[:2500],
                "scoping_answers": scoping_answers,
                "planning_runs": _planning_runs_payload(sections),
                "runs_merged": section_labels,
                "merge_instructions": (
                    f"The {run_count} planning_runs above are separate reports. "
                    "If one run covers a topic with code-backed search results, that version supersedes "
                    "needs_code or partial duplicates from other runs."
                ),
                "items": _compact_items_for_final_synthesis(merged_items),
            },
            ensure_ascii=False,
        ),
        model=rag_settings.rag_synthesis_model,
        temperature=0.1,
    )


def _ensure_ewis_topic_row(
    items: List[Dict[str, Any]],
    merged_items: List[Dict[str, Any]],
    section_labels: List[str],
) -> List[Dict[str, Any]]:
    if not _section_labels_include_1670_4(section_labels):
        return items

    for item in items:
        if _item_merge_key(item) == "ewis_warning":
            return items
        domain = str(item.get("domain") or "").lower()
        if any(token in domain for token in ("ewis", "1670.4", "emergency warning", "occupant warning")):
            return items

    ewis_source: Optional[Dict[str, Any]] = None
    for candidate in merged_items:
        if _item_merge_key(candidate) == "ewis_warning":
            ewis_source = candidate
            break
    if not ewis_source:
        for candidate in merged_items:
            source_hint = " ".join(_clean_list(candidate.get("source_sections"), 8)) or str(
                candidate.get("source_section") or ""
            )
            if _source_is_1670_4(source_hint):
                ewis_source = candidate
                break
    if not ewis_source:
        return items

    ewis_item = dict(ewis_source)
    ewis_item["domain"] = "Emergency Warning & EWIS (AS 1670.4)"
    ewis_item["design_scope"] = _normalize_design_scope(ewis_item.get("design_scope"))
    ewis_item["design_actions"] = _flatten_design_scope(ewis_item["design_scope"], 12) or _clean_list(
        ewis_item.get("design_actions"), 12
    )

    fire_index = next(
        (index for index, item in enumerate(items) if _item_merge_key(item) == "fire_detection"),
        -1,
    )
    if fire_index >= 0:
        return [*items[: fire_index + 1], ewis_item, *items[fire_index + 1 :]]
    return [*items, ewis_item]


def _align_synthesized_items(
    synthesized_items: List[Dict[str, Any]],
    merged_items: List[Dict[str, Any]],
    section_labels: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    merged_by_key = {_item_merge_key(item): item for item in merged_items}
    aligned: List[Dict[str, Any]] = []
    used_keys = set()

    for raw_item in synthesized_items:
        if not isinstance(raw_item, dict):
            continue
        domain = str(raw_item.get("domain") or "Requirement").strip()
        merge_key = _domain_merge_key(domain)
        source = merged_by_key.get(merge_key, {})
        if not source:
            for candidate in merged_items:
                candidate_key = _item_merge_key(candidate)
                if candidate_key == merge_key or _domain_merge_key(str(candidate.get("domain") or "")) == merge_key:
                    source = candidate
                    merge_key = candidate_key
                    break
        used_keys.add(merge_key)
        status = _normalize_planning_status_value(str(raw_item.get("status") or source.get("status") or "conditional"))
        design_scope = _normalize_design_scope(raw_item.get("design_scope"))
        if not _design_scope_has_content(design_scope):
            design_scope = _normalize_design_scope(source.get("design_scope"))
        item = {
            "requirement_id": str(source.get("requirement_id") or merge_key),
            "domain": domain,
            "question": str(source.get("question") or raw_item.get("question") or "").strip(),
            "status": status,
            "summary": str(raw_item.get("summary") or source.get("summary") or "").strip(),
            "reason": str(raw_item.get("reason") or source.get("reason") or "").strip(),
            "design_scope": design_scope,
            "design_actions": _flatten_design_scope(design_scope, 12),
            "evidence_ids": _clean_list(source.get("evidence_ids"), 12),
            "source_sections": _clean_list(source.get("source_sections"), 8),
        }
        if status in {"applies", "conditional", "conflict", "needs_code"} and not _design_scope_has_content(
            item["design_scope"]
        ):
            item = _finalize_item_design_scope(
                item,
                requirement={"id": item["requirement_id"], "domain": domain, "question": item.get("question"), "applicability": item.get("reason")},
                source_result={"evidence": []},
                status=status,
                brief="",
            )
        aligned.append(item)

    for merge_key, source in merged_by_key.items():
        if merge_key in used_keys:
            continue
        if _normalize_planning_status_value(str(source.get("status") or "conditional")) == "needs_code":
            continue
        item = dict(source)
        if merge_key == "ewis_warning":
            item["domain"] = "Emergency Warning & EWIS (AS 1670.4)"
        item["design_scope"] = _normalize_design_scope(item.get("design_scope"))
        item["design_actions"] = _flatten_design_scope(item["design_scope"], 12) or _clean_list(item.get("design_actions"), 12)
        aligned.append(item)

    aligned = _drop_superseded_needs_code_items(aligned)
    return _ensure_ewis_topic_row(aligned, merged_items, section_labels or [])


def _build_finalized_markdown(
    *,
    discipline: str,
    brief: str,
    scoping_answers: Dict[str, str],
    section_labels: List[str],
    report: Dict[str, Any],
) -> str:
    scoping_lines = [
        f"- **{key}:** {value.strip()}"
        for key, value in (scoping_answers or {}).items()
        if str(value).strip()
    ]
    lines = [
        f"# Design Planning Report — {discipline.title()}",
        "",
        "## Project brief",
        "",
        brief.strip(),
        "",
        f"**Standards covered:** {', '.join(section_labels)}",
        "",
        "## Executive summary",
        report.get("executive_summary")
        or "A unified design planning briefing was generated from the project brief and selected standards.",
        "",
        "## Design requirements checklist",
    ]
    if scoping_lines:
        lines.extend(["", "## Scoping answers", "", *scoping_lines, ""])

    sources_by_id = {
        str(source.get("id")): source for source in report.get("sources") or [] if source.get("id")
    }
    for item in report.get("items") or []:
        status = _planning_status_label(str(item.get("status") or "conditional"))
        domain = str(item.get("domain") or item.get("requirement_id") or "Requirement").replace("_", " ")
        lines.extend(
            [
                "",
                f"### {domain} — **{status}**",
                item.get("summary") or "",
                f"**Why it applies:** {item.get('reason') or 'Confirm during detailed design.'}",
            ]
        )
        source_sections = _clean_list(item.get("source_sections"), 6)
        if source_sections:
            lines.append(f"**Sources searched:** {', '.join(source_sections)}")
        design_scope = _normalize_design_scope(item.get("design_scope"))
        if _design_scope_has_content(design_scope):
            lines.append("**Your design package should address:**")
            for key in DESIGN_SCOPE_KEYS:
                entries = design_scope.get(key) or []
                if not entries:
                    continue
                lines.append(f"*{DESIGN_SCOPE_LABELS[key]}:*")
                lines.extend(f"- {entry}" for entry in entries)
        elif item.get("design_actions"):
            lines.append("**Address in drawings/specs:**")
            lines.extend(f"- {action}" for action in _clean_list(item.get("design_actions"), 12))
        evidence_ids = item.get("evidence_ids") or []
        if evidence_ids:
            citations = [
                _format_clause_citation(sources_by_id[evidence_id])
                for evidence_id in evidence_ids
                if evidence_id in sources_by_id
            ]
            if citations:
                lines.append(f"**Key clauses:** {'; '.join(citations)}")

    if report.get("open_items"):
        lines.extend(["", "## Confirm during design"])
        lines.extend(f"- {item}" for item in report["open_items"])
    if report.get("cross_domain_notes"):
        lines.extend(["", "## Cross-domain notes"])
        lines.extend(f"- {item}" for item in report["cross_domain_notes"])
    if report.get("recommendations"):
        lines.extend(["", "## Recommendations"])
        lines.extend(f"- {item}" for item in report["recommendations"])
    if section_labels:
        lines.extend(["", "## Planning runs merged"])
        lines.extend(f"- {label}" for label in section_labels)
    if report.get("sources"):
        lines.extend(["", "## Source index"])
        for index, source in enumerate(report["sources"], start=1):
            lines.append(f"- [{index}] {_format_clause_citation(source)}")
    lines.extend(
        [
            "",
            "## Disclaimer",
            "AI-assisted design planning only. This briefing is not a compliance certificate, "
            "construction approval, or professional sign-off.",
        ]
    )
    return "\n".join(lines)


def finalize_project_report(
    *,
    discipline: str,
    brief: str,
    scoping_answers: Optional[Dict[str, str]] = None,
    sections: List[Dict[str, Any]],
) -> Dict[str, Any]:
    normalized_discipline = normalize_discipline(discipline)
    project_brief = (brief or "").strip()
    if not project_brief:
        raise ValueError("Describe the design or compliance review")
    if not sections:
        raise ValueError("Add at least one report section before finalizing")
    if len(sections) > 12:
        raise ValueError("Too many sections to finalize in one pass")

    answers = {
        str(key): str(value).strip()
        for key, value in (scoping_answers or {}).items()
        if str(key).strip() and str(value).strip()
    }
    section_labels = [
        str(section.get("label") or f"Run {index + 1}").strip()
        for index, section in enumerate(sections)
    ]
    collected_items = _collect_items_from_sections(sections)
    if not collected_items:
        raise ValueError("No checklist items found in the saved sections")

    merged_items = _deterministic_merge_items(collected_items)
    sources = _collect_sources_from_sections(sections)

    if len(sections) == 1:
        single_report = sections[0].get("report") or {}
        final_report = {
            "executive_summary": str(
                single_report.get("executive_summary")
                or "Design planning briefing generated from the project brief."
            ),
            "items": merged_items,
            "cross_domain_notes": _clean_list(single_report.get("cross_domain_notes"), 20),
            "open_items": _clean_list(single_report.get("open_items"), 30),
            "recommendations": _clean_list(single_report.get("recommendations"), 30),
            "sources": sources,
        }
    else:
        try:
            synthesized = _synthesize_final_report(
                discipline=normalized_discipline,
                brief=project_brief,
                scoping_answers=answers,
                section_labels=section_labels,
                sections=sections,
                merged_items=merged_items,
            )
            final_report = {
                "executive_summary": str(
                    synthesized.get("executive_summary")
                    or "Unified design planning briefing across all selected standards."
                ),
                "items": _align_synthesized_items(
                    synthesized.get("items") or [], merged_items, section_labels
                ),
                "cross_domain_notes": _clean_list(synthesized.get("cross_domain_notes"), 20),
                "open_items": _clean_list(synthesized.get("open_items"), 30),
                "recommendations": _clean_list(synthesized.get("recommendations"), 30),
                "sources": sources,
            }
        except Exception:
            logger.exception("Final project report synthesis failed; using deterministic merge")
            summaries = [
                str((section.get("report") or {}).get("executive_summary") or "").strip()
                for section in sections
                if str((section.get("report") or {}).get("executive_summary") or "").strip()
            ]
            final_report = {
                "executive_summary": (
                    " ".join(summaries)[:2500]
                    if summaries
                    else "Unified design planning checklist merged from all code runs."
                ),
                "items": merged_items,
                "cross_domain_notes": [],
                "open_items": [],
                "recommendations": [],
                "sources": sources,
            }

    final_report["items"] = _drop_superseded_needs_code_items(final_report.get("items") or [])
    final_report["items"] = _ensure_ewis_topic_row(
        final_report["items"], merged_items, section_labels
    )

    for index, item in enumerate(final_report["items"]):
        status = _normalize_planning_status_value(str(item.get("status") or "conditional"))
        item["status"] = status
        item["design_scope"] = _normalize_design_scope(item.get("design_scope"))
        if not _design_scope_has_content(item["design_scope"]):
            final_report["items"][index] = _finalize_item_design_scope(
                item,
                requirement={
                    "id": item.get("requirement_id"),
                    "domain": item.get("domain"),
                    "question": item.get("question"),
                    "applicability": item.get("reason"),
                },
                source_result={"evidence": []},
                status=status,
                brief=project_brief,
            )
        else:
            item["design_actions"] = _flatten_design_scope(item["design_scope"], 12)

    final_report["report_markdown"] = _build_finalized_markdown(
        discipline=normalized_discipline,
        brief=project_brief,
        scoping_answers=answers,
        section_labels=section_labels,
        report=final_report,
    )
    return {
        "discipline": normalized_discipline,
        "brief": project_brief,
        "scoping_answers": answers,
        "section_labels": section_labels,
        "merged_from_sections": len(sections),
        "report": final_report,
    }
