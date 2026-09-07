"""Structured logging + trace payload for the RAG pipeline (prepare → search → answer)."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("rag.pipeline")

_PREVIEW_CHARS = 160


def chunk_summary(row: Dict[str, Any], *, rank: Optional[int] = None, score: Optional[float] = None, signal: Optional[str] = None) -> Dict[str, Any]:
    text = row.get("text") or ""
    out: Dict[str, Any] = {
        "chunk_id": str(row.get("id") or ""),
        "clause_number": row.get("clause_number"),
        "heading": row.get("heading"),
        "page_number": row.get("page_number"),
        "chunk_index": row.get("chunk_index"),
        "text_chars": len(text),
        "text_preview": text[:_PREVIEW_CHARS] + ("…" if len(text) > _PREVIEW_CHARS else ""),
    }
    if rank is not None:
        out["rank"] = rank
    if score is not None:
        out["score"] = round(float(score), 6) if score is not None else None
    if signal:
        out["signal"] = signal
    return out


class RagPipelineTrace:
    """Collects step-by-step audit data and writes human-readable server logs."""

    def __init__(self, *, question: str, codebook_id: str, codebook_label: str = "", user_id: str = ""):
        self.question = question
        self.codebook_id = codebook_id
        self.codebook_label = codebook_label
        self.user_id = user_id
        self._started = time.perf_counter()
        self.steps: List[Dict[str, Any]] = []

    def add(self, step: str, detail: Dict[str, Any]) -> None:
        entry = {
            "step": step,
            "elapsed_ms": round((time.perf_counter() - self._started) * 1000, 1),
            **detail,
        }
        self.steps.append(entry)
        try:
            payload = json.dumps(entry, ensure_ascii=False, default=str)
        except Exception:
            payload = str(entry)
        if len(payload) > 4000:
            payload = payload[:4000] + "…"
        logger.info("[RAG:%s] %s", step, payload)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "codebook_id": self.codebook_id,
            "codebook_label": self.codebook_label,
            "user_id": self.user_id,
            "total_ms": round((time.perf_counter() - self._started) * 1000, 1),
            "steps": self.steps,
        }
