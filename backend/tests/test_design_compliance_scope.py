"""Design planning report scope guardrails."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.design_compliance_service import (
    DesignDocument,
    estimate_report_scope,
    validate_report_scope_or_raise,
)


def _doc(codebook: str) -> DesignDocument:
    return DesignDocument(id=codebook.lower(), codebook=codebook, label=codebook)


def test_scope_blocks_large_topic_and_code_product():
    requirements = [{"id": f"req_{index}", "domain": "topic", "question": "q"} for index in range(12)]
    documents = [_doc(f"AS{index}") for index in range(8)]
    scope = estimate_report_scope(requirements, documents)
    assert scope["level"] == "block"
    with pytest.raises(ValueError, match="Deselect some codes"):
        validate_report_scope_or_raise(requirements, documents)


def test_scope_ok_for_small_report():
    requirements = [{"id": "req_1", "domain": "supply", "question": "q", "preferred_sources": ["AS3000"]}]
    documents = [_doc("AS3000"), _doc("VIC_SIR_2025"), _doc("NCC2022_VOL1")]
    scope = estimate_report_scope(requirements, documents)
    assert scope["level"] == "ok"
    validate_report_scope_or_raise(requirements, documents)
