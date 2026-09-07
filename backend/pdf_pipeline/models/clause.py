"""
Data models for clause representation
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum


class ConfidenceLevel(str, Enum):
    """Confidence level for extracted data"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Note(BaseModel):
    """Note attached to a clause"""
    text: str
    type: str = "NOTE"  # NOTE, NOTES, etc.


class Exception(BaseModel):
    """Exception attached to a clause"""
    text: str
    type: str = "Exception"


class Clause(BaseModel):
    """Structured clause representation"""
    clause_id: str
    clause_number: str
    title: Optional[str] = None
    parent_clause_id: Optional[str] = None
    parent_clause_number: Optional[str] = None
    level: int
    page_start: int
    page_end: int
    body_with_subitems: str
    notes: List[Note] = Field(default_factory=list)
    exceptions: List[Exception] = Field(default_factory=list)
    full_normalized_text: str
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    
    # Quality flags
    has_parent: bool = True
    has_body: bool = True
    is_orphan_note: bool = False
    
    class Config:
        use_enum_values = True


class ValidationIssue(BaseModel):
    """Validation issue found during processing"""
    type: str  # missing_parent, duplicate_number, empty_body, etc.
    severity: str  # error, warning, info
    clause_id: Optional[str] = None
    message: str
