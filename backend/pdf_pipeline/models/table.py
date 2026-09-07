"""
Data models for table representation
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from .clause import ConfidenceLevel


class TableRow(BaseModel):
    """Single row in a table"""
    cells: List[str]
    is_header: bool = False


class Table(BaseModel):
    """Structured table representation"""
    table_id: str
    table_number: Optional[str] = None
    title: Optional[str] = None
    parent_clause_reference: Optional[str] = None
    parent_clause_id: Optional[str] = None  # Link to parent clause
    parent_clause_number: Optional[str] = None  # Parent clause number for reference
    page_start: int
    page_end: int
    header_rows: List[TableRow] = Field(default_factory=list)
    data_rows: List[TableRow] = Field(default_factory=list)
    footer_notes: List[str] = Field(default_factory=list)
    raw_csv: Optional[str] = None
    normalized_text_representation: str
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    
    # NEW: AI-generated clause-like description from Modal Stage D
    yaml_description: Optional[str] = None

    # Parser / pipeline metadata (TABLE_PIPELINE_PLAN.md)
    source_method: Optional[str] = None
    extraction_notes: List[str] = Field(default_factory=list)
    table_key: Optional[str] = None
    continuation_of: Optional[str] = None
    quality_metrics: Optional[Dict[str, Any]] = None

    # Quality flags
    has_headers: bool = True
    is_multipage: bool = False
    has_merged_cells: bool = False

    # Header reconstruction (services/header_reconstructor.py); omitted from JSON when None
    reconstructed_header_rows: Optional[List[TableRow]] = None
    promoted_header_rows: Optional[List[TableRow]] = None
    final_columns: Optional[List[str]] = None
    header_model: Optional[Dict[str, Any]] = None
    reconstruction_confidence: Optional[str] = None
    reconstruction_notes: Optional[List[str]] = None

    class Config:
        use_enum_values = True
