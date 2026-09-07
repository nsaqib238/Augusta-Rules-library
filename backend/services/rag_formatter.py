"""Final markdown for the user: deterministic professional layout (no extra LLM call)."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

_DISCLAIMER = (
    "**Note:** This text is generated only from the cited clauses in the code(s) you selected. "
    "It is not a certificate of compliance. Confirm measurements, equipment, and site conditions on site; "
    "engage a qualified person where required."
)


def _as_str_list(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, str):
        s = val.strip()
        return [s] if s else []
    if isinstance(val, list):
        return [str(x).strip() for x in val if x and str(x).strip()]
    return []


def _conclusion_line(conclusion: str) -> str:
    c = (conclusion or "").strip().lower()
    mapping = {
        "permitted": "**Outcome:** On the basis of the cited clauses alone, the described arrangement appears **permitted** (subject to the usual on-site checks).",
        "not_permitted": "**Outcome:** On the basis of the cited clauses alone, the described arrangement appears **not to comply** or is **not permitted** (subject to the usual on-site checks).",
        # Avoid implying the model "lacks facts" when the body already states the rules; "depends" is a JSON enum, not user-facing hedging.
        "depends": "**Outcome:** Based **only on the cited clauses** - apply the requirements below to the installation and use any verification items as applicable.",
        "insufficient_evidence": "",
    }
    return mapping.get(c, "")


def _format_citations(citations: Any) -> List[str]:
    lines: List[str] = []
    if not isinstance(citations, list):
        return lines
    for c in citations:
        if not isinstance(c, dict):
            continue
        claim = (c.get("claim") or "").strip()
        cid = (c.get("clause_id") or c.get("unit_id") or c.get("anchor_id") or "").strip()
        if not claim and not cid:
            continue
        ref = cid or "Clause"
        if claim:
            lines.append(f"- **{ref}** - {claim}")
        else:
            lines.append(f"- **{ref}**")
    return lines


def _format_required_tables(tables: Any) -> List[str]:
    lines: List[str] = []
    if not isinstance(tables, list):
        return lines
    for item in tables:
        if not isinstance(item, dict):
            continue
        ref = (item.get("table_ref") or item.get("table_number") or "").strip()
        title = (item.get("title") or "").strip()
        reason = (item.get("reason") or "").strip()
        info = (item.get("information_needed") or "").strip()
        source = (item.get("source") or "").strip()
        label = ref or "Table"
        if title and title.lower() not in label.lower():
            label = f"{label} — {title}"
        parts = [f"- **{label}**"]
        if reason:
            parts.append(reason)
        if info:
            parts.append(f"Provide: {info}")
        if source:
            parts.append(f"({source})")
        lines.append(" ".join(parts))
    return lines


def build_professional_markdown(answer_json: Dict[str, Any]) -> str:
    """
    Turn Answer JSON into skimmable practitioner-style markdown.
    Same fields the answer model already produces; no new facts added here.
    """
    conclusion = (answer_json.get("conclusion") or "").strip().lower()
    short = (answer_json.get("short_answer") or "").strip()
    conditions = _as_str_list(answer_json.get("conditions"))
    exceptions = _as_str_list(answer_json.get("exceptions_or_edge_cases"))
    required = _as_str_list(answer_json.get("required_actions"))
    unknowns = _as_str_list(answer_json.get("unknowns_to_confirm"))
    citations = _format_citations(answer_json.get("citations"))
    insuff = (answer_json.get("insufficient_reason") or "").strip()
    improve = (answer_json.get("what_would_improve_answer") or "").strip()
    follow = _as_str_list(answer_json.get("suggested_follow_ups"))[:4]
    required_tables = _format_required_tables(answer_json.get("required_tables"))
    missing_clauses = _as_str_list(answer_json.get("missing_clause_refs"))

    blocks: List[str] = []

    if conclusion == "insufficient_evidence":
        blocks.append("**Insufficient evidence in the supplied clauses**")
        blocks.append("")
        if short:
            blocks.append(short)
        else:
            blocks.append("The retrieved clauses do not clearly answer this question.")
        if insuff:
            blocks.append("")
            blocks.append(f"**Why:** {insuff}")
        if required_tables:
            blocks.append("")
            blocks.append("**Tables — additional information required**")
            blocks.append("")
            blocks.append("The answer depends on data from these tables. Supply the values listed and ask again.")
            blocks.append("")
            blocks.extend(required_tables)
        elif improve:
            blocks.append("")
            blocks.append(f"**What would help:** {improve}")
        if missing_clauses:
            blocks.append("")
            blocks.append("**Missing clause references**")
            blocks.append("")
            blocks.extend(f"- {c}" for c in missing_clauses)
        blocks.append("")
        blocks.append(_DISCLAIMER)
        if follow:
            blocks.append("")
            blocks.append("**You might also ask**")
            blocks.append("")
            blocks.extend(f"- {x}" for x in follow[:3])
        return "\n".join(blocks).strip()

    # Answered (permitted / not_permitted / depends)
    blocks.append("**Practitioner summary**")
    blocks.append("")
    ol = _conclusion_line(conclusion)
    if ol:
        blocks.append(ol)
        blocks.append("")
    if short:
        blocks.append(short)

    if conditions:
        blocks.append("")
        blocks.append("**What the cited clauses require**")
        blocks.append("")
        blocks.extend(f"- {c}" for c in conditions)

    if exceptions:
        blocks.append("")
        blocks.append("**Exceptions, notes, and edge cases**")
        blocks.append("")
        blocks.extend(f"- {e}" for e in exceptions)

    if required:
        blocks.append("")
        blocks.append("**Required actions or sequencing**")
        blocks.append("")
        blocks.extend(f"- {r}" for r in required)

    if unknowns:
        blocks.append("")
        blocks.append("**Confirm on site or in design records**")
        blocks.append("")
        blocks.extend(f"- {u}" for u in unknowns)

    if required_tables and conclusion != "insufficient_evidence":
        blocks.append("")
        blocks.append("**Tables — inputs needed to complete this answer**")
        blocks.append("")
        blocks.extend(required_tables)

    if missing_clauses and conclusion != "insufficient_evidence":
        blocks.append("")
        blocks.append("**Related clauses not in retrieved evidence**")
        blocks.append("")
        blocks.extend(f"- {c}" for c in missing_clauses)

    if citations:
        blocks.append("")
        blocks.append("**Citations**")
        blocks.append("")
        blocks.extend(citations)
    elif short:
        blocks.append("")
        blocks.append("**Citations**")
        blocks.append("")
        blocks.append("- No structured citations were returned; treat the summary with extra caution.")

    blocks.append("")
    blocks.append(_DISCLAIMER)

    if follow:
        blocks.append("")
        blocks.append("**You might also ask**")
        blocks.append("")
        blocks.extend(f"- {x}" for x in follow[:3])

    return "\n".join(blocks).strip()


def collect_source_clause_refs(
    answer_json: Dict[str, Any],
    retrieved_clauses: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """Unique clause numbers for the trailing Sources / citations index."""
    refs: List[str] = []
    for c in answer_json.get("citations") or []:
        if isinstance(c, dict):
            cid = (c.get("clause_id") or c.get("unit_id") or c.get("anchor_id") or "").strip()
            if cid:
                refs.append(cid)
    for key in ("cited_clauses",):
        for item in _as_str_list(answer_json.get(key)):
            refs.append(item)
    for row in retrieved_clauses or []:
        cn = (row.get("clause_number") or "").strip()
        if cn:
            refs.append(cn)
    return list(dict.fromkeys(refs))


def append_sources_index(markdown: str, clause_refs: List[str]) -> str:
    if not clause_refs:
        return markdown
    return f"{markdown.rstrip()}\n\n**Sources / citations**\n\n" + "\n".join(clause_refs)


def format_structured_answer(
    answer_json: Dict[str, Any],
    retrieved_clauses: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build full practitioner markdown including sources index."""
    md = _strip_empty_sections(build_professional_markdown(answer_json or {}))
    return append_sources_index(md, collect_source_clause_refs(answer_json, retrieved_clauses))


def _candidate_label(idx: int) -> str:
    # A, B, C...
    return chr(ord("A") + idx) if 0 <= idx < 26 else str(idx + 1)


def build_multi_candidate_markdown(candidates: List[Dict[str, Any]]) -> str:
    """
    Build multiple practitioner cards without cross-mixing.
    Each candidate is formatted independently with the same deterministic layout.
    """
    cands = [c for c in (candidates or []) if isinstance(c, dict) and c]
    if not cands:
        return build_professional_markdown({})
    if len(cands) == 1:
        return build_professional_markdown(cands[0])

    blocks: List[str] = []
    blocks.append("**Multiple suitable answers (choose the applicable scenario)**")
    blocks.append("")
    blocks.append(
        "The clauses retrieved include more than one plausible pathway/trigger. "
        "Each candidate below is generated **only from its own cited clauses**."
    )
    blocks.append("")

    for i, cand in enumerate(cands[:4]):
        label = _candidate_label(i)
        title = (cand.get("candidate_title") or "").strip()
        title_suffix = f" — {title}" if title else ""
        blocks.append(f"**Candidate {label}{title_suffix}**")
        blocks.append("")
        blocks.append(build_professional_markdown(cand))
        if i != min(len(cands), 4) - 1:
            blocks.append("")
            blocks.append("---")
            blocks.append("")

    # If any candidate provides clarifying questions, show a combined short list.
    clarifiers: List[str] = []
    for cand in cands[:4]:
        for u in _as_str_list(cand.get("unknowns_to_confirm")):
            clarifiers.append(u)
    clarifiers = list(dict.fromkeys([c.strip() for c in clarifiers if c.strip()]))[:6]
    if clarifiers:
        blocks.append("")
        blocks.append("**What to confirm to pick the right candidate**")
        blocks.append("")
        blocks.extend(f"- {c}" for c in clarifiers[:6])

    return "\n".join(blocks).strip()


def _strip_empty_sections(markdown: str) -> str:
    """Remove **Section** blocks that only contain '- None' or 'None' (legacy cleanup)."""
    if not markdown or not isinstance(markdown, str):
        return markdown
    for label in ("**Conditions**", "**Exceptions**", "**Required Actions**"):
        pattern = re.compile(
            re.escape(label) + r"\s*\n+\s*[-•]?\s*None\s*(\n|$)",
            re.IGNORECASE
        )
        markdown = pattern.sub("", markdown)
    markdown = re.sub(r"\n\s*[-•]\s*None\s*\n", "\n", markdown)
    return markdown.strip()


def run_formatter(
    answer_json: Dict[str, Any],
    system_message: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build final_markdown from Answer JSON using a fixed professional layout.
    system_message and model are kept for API compatibility; formatting is deterministic.
    """
    _ = system_message, model  # signature compatibility with callers
    # Support either a single answer JSON or a wrapper { "candidates": [...] }
    if isinstance(answer_json, dict) and isinstance(answer_json.get("candidates"), list):
        md = build_multi_candidate_markdown(answer_json.get("candidates") or [])
    else:
        md = build_professional_markdown(answer_json or {})
    return {"final_markdown": _strip_empty_sections(md)}
