"""
Modal.com Complete PDF Extractor for AS/NZS Standards - GROQ VISION ARCHITECTURE
================================================================================
Simplified pipeline with AI super-resolution + Vision LLM direct extraction:

ARCHITECTURE:
  PDF (digital or scanned)
   ↓
  Convert to 300 DPI images
   ↓
  Table Transformer: Quick detection scan → Find pages with tables
   ↓
  Real-ESRGAN: 2x enhancement (ONLY pages with tables) → 600 DPI equivalent
   ↓
  Groq Llama 4 Scout 17Bx16E Vision API: Extract tables directly from enhanced images
   ↓
  Final JSON (high accuracy extraction)

ENGINES:
- PyMuPDF: Native text extraction from digital PDFs (for captions when available)
- Table Transformer: Microsoft's SOTA for table detection
- Real-ESRGAN: Tencent ARC Lab's AI super-resolution (2x selective upscale)
- Groq Vision API: Llama 4 Scout 17Bx16E for direct table extraction

BENEFITS:
- Single-step extraction (no error compounding)
- Vision model sees table structure + context holistically
- FREE ($0/doc with Groq API free tier)
- Simpler pipeline (2 models vs 4+ models)
- Selective enhancement (only pages with tables)

Cost: $0/doc (FREE with Groq API free tier)
"""

import modal
import json
import re
import os
from typing import List, Dict, Any, Optional, Tuple

# Define Modal app
app = modal.App("pdf-table-extractor-v3")

# Docker image with all dependencies
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "poppler-utils",      # PDF to image conversion
        "libgl1-mesa-glx",    # OpenCV dependencies
        "libglib2.0-0",
    )
    # Step 1: Install torch first (required for Real-ESRGAN)
    .pip_install(
        "torch==2.1.2",
        "torchvision==0.16.2",
    )
    # Step 2: Install Real-ESRGAN with separate pip call to avoid CUDA conflicts
    .run_commands(
        "pip install basicsr facexlib realesrgan --no-deps",  # No deps to avoid CUDA conflicts
        "pip install lmdb pyyaml scipy tb-nightly yapf opencv-python",  # Manual deps for basicsr
    )
    # Step 3: Install remaining dependencies
    .pip_install(
        # Table extraction (GPU models)
        "transformers==4.44.0",  # For Table Transformer only
        "pdf2image==1.16.3",
        "Pillow==10.1.0",
        "timm==0.9.12",
        "numpy==1.26.4",  # Updated: transformers 4.44.0 requires numpy with exceptions module
        "opencv-python==4.8.1.78",
        # Vision API clients
        "groq==0.13.0",          # Groq SDK for Vision API (updated for compatibility)
        "google-generativeai==0.8.3",  # Google Gemini API (alternative to Groq)
        # Native PDF text extraction (CRITICAL: provides 'fitz' module)
        "pymupdf==1.24.0",       # PyMuPDF for native text extraction
        # Clause extraction (rule-based parser)
        "pypdf==4.0.1",
        # Web framework
        "fastapi[standard]==0.115.0",
    )
)

# AS/NZS table numbers: 1.8, 5.1.5, 7.4, J.1, L.8.2.2
_TABLE_NUM = r"(?:[A-Z]\.)?(?:\d+(?:\.\d+)+|[A-Z]?\d*(?:\.\d+)*)"
_TABLE_NUM_PATTERNS = [
    re.compile(rf"\b[Tt][Aa][Bb][Ll][Ee]\s+({_TABLE_NUM})\b"),
    re.compile(rf"\b({_TABLE_NUM})\s*[-–—:]"),
    re.compile(rf"\b({_TABLE_NUM})\s+[A-Z]"),
]
_GLOSSARY_CLAUSE_RE = re.compile(r"^\d+\.\d+\.\d+\s+\w", re.MULTILINE)
_FIGURE_RE = re.compile(r"\bFigure\s+[\w][\w.\(\)]*", re.IGNORECASE)
_BIBLIOGRAPHY_RE = re.compile(r"\bBibliography\b", re.IGNORECASE)
_DESIGNER_STMT_RE = re.compile(r"Designer['\u2019]?s\s+statement", re.IGNORECASE)
_FALLBACK_ID_RE = re.compile(r"^(\d+)\.(\d+)$")


def _normalize_table_number(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip()
    s = re.sub(r"^[Tt][Aa][Bb][Ll][Ee]\s+", "", s).strip()
    s = re.sub(r"^([A-Z])\.l$", r"\1.1", s, flags=re.IGNORECASE)
    s = re.sub(r"^([A-Z])\.l\.(\d+)$", r"\1.1.\2", s, flags=re.IGNORECASE)
    return s or None


def _extract_table_number_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    best: Optional[str] = None
    best_len = 0
    for pat in _TABLE_NUM_PATTERNS:
        for m in pat.finditer(text):
            candidate = _normalize_table_number(m.group(1))
            if candidate and len(candidate) > best_len:
                best = candidate
                best_len = len(candidate)
    return best


def _is_page_index_fallback_id(table_number: str, page: int) -> bool:
    m = _FALLBACK_ID_RE.match(str(table_number or "").strip())
    if not m:
        return False
    try:
        return int(m.group(1)) == int(page) and page > 0
    except ValueError:
        return False


def _count_data_rows(rows: List[Dict]) -> int:
    return sum(1 for r in rows if r.get("type") in ("data", "group"))


def _is_false_table_region_text(text: str) -> bool:
    """Glossary clauses, figures, bibliography — not real table captions."""
    if not text or not text.strip():
        return False
    t = text.strip()
    if _FIGURE_RE.search(t):
        return True
    if _BIBLIOGRAPHY_RE.search(t) or _DESIGNER_STMT_RE.search(t):
        return True
    # Glossary: "1.4.16 control and indicating equipment" without TABLE keyword
    if _GLOSSARY_CLAUSE_RE.match(t) and not re.search(r"\b[Tt][Aa][Bb][Ll][Ee]\b", t):
        return True
    return False


# ============================================================================
# PDF TYPE DETECTION
# ============================================================================

def is_digital_pdf(pdf_bytes: bytes) -> Tuple[bool, float]:
    """
    Detect if PDF has embedded text (digital PDF) or is scanned (images only).
    
    Returns:
        (is_digital, text_coverage_percentage)
    """
    import fitz  # PyMuPDF
    import io
    
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        # Sample first 10 pages to determine PDF type
        sample_pages = min(10, len(doc))
        total_chars = 0
        
        for page_num in range(sample_pages):
            page = doc[page_num]
            text = page.get_text()
            total_chars += len(text.strip())
        
        doc.close()
        
        # If we have significant text content, it's a digital PDF
        avg_chars_per_page = total_chars / sample_pages
        is_digital = avg_chars_per_page > 100  # Threshold: 100+ chars/page
        coverage = min(100.0, avg_chars_per_page / 10.0)  # Rough estimate
        
        return is_digital, coverage
        
    except Exception as e:
        print(f"  ⚠️  PDF type detection failed: {e}")
        return False, 0.0


def extract_native_text_with_coordinates(pdf_bytes: bytes) -> Dict[int, List[Dict]]:
    """
    Extract native text from digital PDF with coordinates for matching to table regions.
    
    Returns:
        {
            page_num: [
                {
                    "text": "Table 3.1",
                    "bbox": (x0, y0, x1, y1),  # PDF coordinates
                    "font_size": 12.0,
                    "font_name": "Arial-Bold",
                    "page_width": 612.0,  # PDF page width
                    "page_height": 792.0  # PDF page height
                },
                ...
            ],
            ...
        }
    """
    import fitz  # PyMuPDF
    import io
    
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text_data = {}
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_rect = page.rect
            pdf_page_width = page_rect.width
            pdf_page_height = page_rect.height
            
            # Extract text with detailed information
            blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
            page_texts = []
            
            for block in blocks.get("blocks", []):
                if block.get("type") == 0:  # Text block
                    for line in block.get("lines", []):
                        line_text = ""
                        line_bbox = None
                        font_size = 0
                        font_name = ""
                        
                        for span in line.get("spans", []):
                            line_text += span.get("text", "")
                            if line_bbox is None:
                                line_bbox = span.get("bbox")
                            font_size = max(font_size, span.get("size", 0))
                            if not font_name and span.get("font"):
                                font_name = span.get("font", "")
                        
                        if line_text.strip():
                            page_texts.append({
                                "text": line_text.strip(),
                                "bbox": line_bbox,  # PDF coordinates
                                "font_size": font_size,
                                "font_name": font_name,
                                "page_width": pdf_page_width,
                                "page_height": pdf_page_height
                            })
            
            text_data[page_num + 1] = page_texts  # 1-indexed pages
        
        doc.close()
        return text_data
        
    except Exception as e:
        print(f"  ⚠️  Native text extraction failed: {e}")
        return {}


def find_caption_in_native_text(native_texts: List[Dict], table_bbox: tuple, page_height: int, pdf_page_height: float = None) -> Optional[Dict]:
    """
    Find table caption in native text above table region.
    
    Args:
        native_texts: List of text blocks with coordinates from PyMuPDF (PDF coordinates)
        table_bbox: (x0, y0, x1, y1) in IMAGE PIXEL coordinates at 300 DPI
        page_height: Height of page in IMAGE PIXEL coordinates
        pdf_page_height: Height of PDF page in PDF coordinates (optional, extracted from native_texts)
        
    Returns:
        {"table_number": "3.1", "title": "Installation methods"}
    """
    import re
    
    if not native_texts:
        return None
    
    # Get PDF page dimensions from first text block
    pdf_page_width = native_texts[0].get("page_width", 612.0)
    pdf_page_height = native_texts[0].get("page_height", 792.0)
    
    # Table bbox is in IMAGE PIXEL coordinates at 300 DPI
    x0, y0, x1, y1 = table_bbox
    
    # CRITICAL FIX: Calculate ACTUAL scale factor dynamically
    # Scale factor = actual_image_height / pdf_page_height
    # This accounts for the actual DPI used during rendering
    if page_height and pdf_page_height and pdf_page_height > 0:
        scale_y = page_height / pdf_page_height
        scale_x = scale_y  # Assume uniform scaling
        print(f"    [DEBUG] Dynamic scale: page_height={page_height}, pdf_page_height={pdf_page_height:.2f}, scale={scale_y:.4f}")
    else:
        # Fallback: assume 300 DPI rendering (300 / 72 = 4.166...)
        scale_x = 4.17
        scale_y = 4.17
        print(f"    [DEBUG] Fallback scale: 4.17 (page_height={page_height}, pdf_page_height={pdf_page_height})")
    
    # Caption search region in PIXEL coordinates (200px above table - expanded from 100px)
    caption_y0_px = max(0, y0 - 200)  # Increased from 100px to 200px
    caption_y1_px = y0 + 20
    caption_x0_px = max(0, x0 - 50)
    caption_x1_px = x1 + 50
    
    # Find text blocks in caption region
    caption_candidates = []
    total_text_blocks = 0
    blocks_in_region = 0
    
    for text_block in native_texts:
        total_text_blocks += 1
        if not text_block.get("bbox"):
            continue
            
        # Text bbox is in PDF coordinates - need to scale to pixels
        tx0_pdf, ty0_pdf, tx1_pdf, ty1_pdf = text_block["bbox"]
        text_content = text_block["text"]
        
        # Skip empty text
        if not text_content or not text_content.strip():
            continue
        
        # Scale PDF coordinates to pixel coordinates using ACTUAL scale factor (calculated above)
        tx0_px = tx0_pdf * scale_x
        ty0_px = ty0_pdf * scale_y
        tx1_px = tx1_pdf * scale_x
        ty1_px = ty1_pdf * scale_y
        
        # Check if text is in caption region (pixel coordinates)
        vertical_overlap = (ty0_px >= caption_y0_px and ty0_px <= caption_y1_px) or \
                          (ty1_px >= caption_y0_px and ty1_px <= caption_y1_px) or \
                          (ty0_px <= caption_y0_px and ty1_px >= caption_y1_px)
        
        horizontal_overlap = (tx1_px >= caption_x0_px and tx0_px <= caption_x1_px)
        
        if vertical_overlap and horizontal_overlap:
            blocks_in_region += 1
            # Calculate distance from table top (in pixels)
            distance_from_table = y0 - ty1_px
            caption_candidates.append({
                "text": text_content,
                "distance": distance_from_table,
                "bbox_pdf": (tx0_pdf, ty0_pdf, tx1_pdf, ty1_pdf),
                "bbox_px": (tx0_px, ty0_px, tx1_px, ty1_px),
                "font_size": text_block.get("font_size", 0)
            })
    
    print(f"    [DEBUG] Caption search: total_blocks={total_text_blocks}, in_region={blocks_in_region}, candidates={len(caption_candidates)}")
    if blocks_in_region > 0 and len(caption_candidates) > 0:
        print(f"    [DEBUG] Caption region: y0={caption_y0_px:.0f}, y1={caption_y1_px:.0f}, x0={caption_x0_px:.0f}, x1={caption_x1_px:.0f}")
        print(f"    [DEBUG] Table bbox: x0={x0:.0f}, y0={y0:.0f}, x1={x1:.0f}, y1={y1:.0f}")
        for i, cand in enumerate(caption_candidates[:3]):
            print(f"    [DEBUG] Candidate {i+1}: '{cand['text'][:50]}' @ y={cand['bbox_px'][1]:.0f}")
    
    if not caption_candidates:
        return None
    
    # Sort by distance from table (closest first)
    caption_candidates.sort(key=lambda c: c["distance"], reverse=True)
    
    # Parse table number and title
    table_number = None
    title = None
    
    # Drop glossary/figure caption candidates before matching
    caption_candidates = [
        c for c in caption_candidates
        if not _is_false_table_region_text(c["text"])
    ]
    if not caption_candidates:
        return None

    caption_text = " ".join(c["text"] for c in caption_candidates[:5])
    table_number = _extract_table_number_from_text(caption_text)

    if table_number:
        title_match = re.search(
            rf"[Tt][Aa][Bb][Ll][Ee]\s+{re.escape(table_number)}\s+(.+)",
            caption_text,
            re.IGNORECASE,
        )
        if title_match:
            title = title_match.group(1).strip()
            title = re.sub(r'^[-–—:\s]+', '', title)
            title = re.sub(r'\(continued\)\s*$', '', title, flags=re.IGNORECASE)
            if len(title) < 3:
                title = None
        print(f"    [DEBUG] Found table number '{table_number}' in caption region")
    
    # PRIORITY 3: AS3000 FALLBACK - If no formal table number found, use descriptive title
    # Many AS3000 tables have descriptive titles without "TABLE" keyword
    if not table_number and len(caption_candidates) > 0:
        first_candidate = caption_candidates[0]["text"].strip()
        # Check if it looks like a title (ALL CAPS, reasonable length)
        if len(first_candidate) > 10 and first_candidate.isupper():
            title = first_candidate
            # Don't set table_number - let it remain None
            print(f"    [DEBUG] No table number found, using descriptive title: '{title[:50]}'")
    
    return {
        "table_number": table_number,
        "title": title if title and len(title) > 3 else None,
        "method": "native_text"
    }


def _peek_caption_region_text(
    native_texts: List[Dict],
    table_bbox: tuple,
    page_height: int,
    pdf_page_height: float = None,
) -> str:
    """Combined text above table bbox (for false-region detection)."""
    if not native_texts:
        return ""
    x0, y0, x1, y1 = table_bbox
    pdf_page_height = pdf_page_height or native_texts[0].get("page_height", 792.0)
    scale_y = page_height / pdf_page_height if pdf_page_height else 4.17
    scale_x = scale_y
    caption_y0_px = max(0, y0 - 200)
    caption_y1_px = y0 + 20
    caption_x0_px = max(0, x0 - 50)
    caption_x1_px = x1 + 50
    parts: List[str] = []
    for text_block in native_texts:
        if not text_block.get("bbox"):
            continue
        tx0_pdf, ty0_pdf, tx1_pdf, ty1_pdf = text_block["bbox"]
        tx0_px, ty0_px = tx0_pdf * scale_x, ty0_pdf * scale_y
        tx1_px, ty1_px = tx1_pdf * scale_x, ty1_pdf * scale_y
        vertical = (ty0_px >= caption_y0_px and ty0_px <= caption_y1_px) or \
                   (ty1_px >= caption_y0_px and ty1_px <= caption_y1_px) or \
                   (ty0_px <= caption_y0_px and ty1_px >= caption_y1_px)
        horizontal = tx1_px >= caption_x0_px and tx0_px <= caption_x1_px
        if vertical and horizontal:
            parts.append(text_block.get("text", ""))
    return " ".join(parts).strip()


# ============================================================================
# TROCR: High-Quality OCR for Printed Documents
# ============================================================================

# ============================================================================
# GROQ VISION API: Direct Table Extraction from Enhanced Images
# ============================================================================

def classify_table_type(table_data: Dict) -> str:
    """
    Classify table as data_table, index_table, or figure_index.
    
    This helps determine the level of detail needed in descriptions:
    - data_table: Full semantic prose with all values
    - index_table: Brief summary (table of contents, appendix lists)
    - figure_index: Brief summary (figure lists)
    
    Args:
        table_data: Table with rows, metadata
        
    Returns:
        "data_table", "index_table", or "figure_index"
    """
    rows = table_data.get("rows", [])
    title = (table_data.get("title") or "").lower()
    
    # Get all column headers
    columns = []
    for row in rows:
        if row.get("type") == "header":
            columns.extend([str(cell).lower() for cell in row.get("cells", [])])
    
    column_text = " ".join(columns)
    
    # Index table indicators
    index_keywords = ["page", "appendix", "section", "clause", "item"]
    if any(keyword in column_text for keyword in index_keywords):
        # Check if it's just a listing table
        if "table" in column_text or "appendix" in title:
            return "index_table"
    
    # Figure index indicators
    if "figure" in column_text or "figure" in title:
        return "figure_index"
    
    # Technical data table indicators (units present)
    technical_units = ["mm", "°c", "degrees", " a", " v", " w", " m", "khz", "mhz", "²"]
    if any(unit in column_text for unit in technical_units):
        return "data_table"
    
    # Default to data table for full prose
    return "data_table"


def detect_category_rows(rows: List) -> List:
    """
    Detect and mark rows that are category/group labels.
    
    Category rows typically have:
    - Text in first cell
    - Empty or blank in remaining cells
    - No numeric values
    
    Args:
        rows: List of row dictionaries with "cells" key
        
    Returns:
        Same list of rows with "is_category" flag added
    """
    for row in rows:
        row_type = row.get("type", "data")
        cells = row.get("cells", [])
        
        if row_type in ["header", "note", "blank"]:
            continue
        
        if not cells or len(cells) < 2:
            continue
        
        first_cell = str(cells[0]).strip()
        rest_cells = [str(c).strip() for c in cells[1:]]
        
        # Category row: First cell has text, others are empty/blank
        if first_cell and all(not c or c == "" for c in rest_cells):
            row["is_category"] = True
        else:
            row["is_category"] = False
    
    return rows


def generate_yaml_description_for_table(table_data: Dict, provider: str, groq_api_key: str = None, gemini_api_key: str = None) -> str:
    """
    Generate a semantic, RAG-optimized clause-like description for a table using AI.
    
    This is Stage D: Convert extracted table data into natural language prose
    that explains what the table MEANS (not how it looks).
    
    KEY PRINCIPLES:
    - NO row dumps ("Row 1:", "Row 2:", "contains X rows")
    - Convert rows into semantic sentences about requirements
    - Use category rows as context for data rows
    - Express relationships explicitly: "For X, the Y is Z"
    - Preserve notes and symbols semantically
    - Write for RAG retrieval, not layout description
    
    Args:
        table_data: Extracted table with rows, notes, metadata
        provider: "groq" or "gemini"
        groq_api_key: Groq API key (optional)
        gemini_api_key: Gemini API key (optional)
    
    Returns:
        Semantic natural language description optimized for RAG systems
    """
    import json
    
    try:
        # Build table representation for AI
        table_number = table_data.get("table_number") or "Unknown"
        title = table_data.get("title") or "Untitled"
        rows = table_data.get("rows", [])
        footer_notes = table_data.get("footer_notes", [])

        if _count_data_rows(rows) == 0:
            print(f"    [{provider.upper()}] Stage D: skipped (0 data rows)")
            return ""
        
        # Classify table type
        table_type = classify_table_type(table_data)
        
        # Detect category rows
        rows = detect_category_rows(rows)
        
        # Format rows for readability (with category marking)
        rows_repr = []
        for i, row in enumerate(rows[:15]):  # Increased to 15 rows for better context
            row_type = row.get("type", "unknown")
            is_category = row.get("is_category", False)
            cells = row.get("cells", [])
            
            category_marker = " [CATEGORY]" if is_category else ""
            rows_repr.append(f"  Row {i+1} ({row_type}{category_marker}): {' | '.join(str(c) for c in cells)}")
        
        if len(rows) > 15:
            rows_repr.append(f"  ... and {len(rows) - 15} more rows")
        
        # Build table representation (avoiding f-string backslash issues)
        rows_text = "\n".join(rows_repr)
        notes_text = "\n".join(f"- {note}" for note in footer_notes) if footer_notes else "(none)"
        
        # Add table type guidance
        type_guidance = ""
        if table_type == "index_table":
            type_guidance = "\n[TABLE TYPE: Index/List table - Provide brief summary, no need for row-by-row prose]\n"
        elif table_type == "figure_index":
            type_guidance = "\n[TABLE TYPE: Figure index - Provide brief summary of figures listed]\n"
        else:
            type_guidance = "\n[TABLE TYPE: Technical data table - Provide full detailed semantic prose]\n"
        
        table_repr = f"""Table {table_number}: {title}{type_guidance}
Structure:
{rows_text}

Footer Notes:
{notes_text}

NOTE: Rows marked [CATEGORY] are group labels - use them as context for following rows.
"""
        
        # COMPREHENSIVE SEMANTIC PROMPT (RAG-OPTIMIZED)
        yaml_prompt = f"""You are an expert in Australian/New Zealand electrical standards (AS/NZS 3000).

I need you to convert the following table into a natural language, clause-like description suitable for RAG (Retrieval-Augmented Generation) systems.

**Table Data:**
{table_repr}

**CRITICAL REQUIREMENTS - DO NOT VIOLATE:**

1. **NEVER write row-dump descriptions**
   - FORBIDDEN patterns: "Row 1:", "Row 2:", "The table contains X rows", "columns are:"
   - Instead: Convert each row into semantic sentences about what the data MEANS

2. **Write engineering prose, not layout descriptions**
   - BAD: "Row 2: V-75 | 75 | 75 | 0"
   - GOOD: "For thermoplastic V-75 cable insulation, normal use temperature is 75°C, maximum permissible temperature is 75°C, and minimum ambient temperature is 0°C."
   
3. **Handle grouped/category rows intelligently**
   - If you see category labels like "Thermoplastic", "Elastomeric", use them as context for following rows
   - BAD: "Thermoplastic | | |"
   - GOOD: "For thermoplastic insulation types: V-75 cables operate at..."

4. **Express relationships explicitly**
   - Always state: "For <condition/type>, the <property> is <value>"
   - Example: "For water service not greater than DN65, the minimum separation to the low voltage electrical service is 100 mm and the minimum separation to the earthing electrode is 500 mm."

5. **Structure your description:**
   a) First sentence: State what this table specifies/governs
   b) Second sentence: Mention the scope or governing clause if clear from context
   c) Then: One sentence per scenario/row/value set explaining the requirement
   d) Last: Include notes, exceptions, or references to manufacturer information

6. **Preserve symbols and notes semantically**
   - If you see "*", superscripts, or note markers, explain what they mean
   - BAD: "* Refer to manufacturer's information"
   - GOOD: "For certain insulation types marked with an asterisk, the minimum ambient temperature must be obtained from the manufacturer's specifications."

7. **Ignore index/list tables UNLESS they contain technical requirements**
   - Table of contents, figure lists, appendix indices → Just state "This table lists..."
   - Technical specification tables → Full detailed prose

8. **Output style for searchable RAG:**
   - Write as if an electrician asked: "What are the temperature limits for V-75 cable?"
   - Your description should directly answer such queries
   - Be verbose and detailed - include ALL values and conditions
   - Use natural language that matches typical user queries
   - NO markdown formatting (no **, ##, etc.) - plain text only

9. **Examples of GOOD vs BAD:**

   BAD (row dump):
   "The table contains 6 rows. Row 1: Water service not greater than DN65 | 100 | 500. Row 2: Water service greater than DN65 | 300 | 500."
   
   GOOD (semantic prose):
   "This table specifies minimum separation distances for underground services. For water service not greater than DN65, the minimum separation to low voltage electrical service is 100 mm and minimum separation to earthing electrode is 500 mm. For water service greater than DN65, these separations are 300 mm and 500 mm respectively."

   BAD (ignores categories):
   "Row 1: Thermoplastic | | | Row 2: V-75 | 75 | 75 | 0"
   
   GOOD (uses categories as context):
   "For thermoplastic insulation types, the following temperature limits apply. V-75 cables have a normal operating temperature of 75°C, maximum permissible temperature of 75°C, and minimum ambient temperature of 0°C."

**YOUR TASK:**
Generate a clause-like description (plain text, no markdown, no row dumps) that reads like engineering documentation and retrieves well in RAG systems."""
        
        print(f"    [{provider.upper()}] Stage D: Generating YAML description...")
        
        description_text = None
        
        # Try Gemini first (better for longer context)
        if gemini_api_key:
            try:
                import google.generativeai as genai
                import time
                start_time = time.time()
                
                genai.configure(api_key=gemini_api_key)
                model = genai.GenerativeModel('gemini-2.5-flash')
                
                # Set request timeout to 30 seconds
                response = model.generate_content(
                    yaml_prompt,
                    generation_config=genai.GenerationConfig(
                        temperature=0.3,
                        max_output_tokens=4096,
                    ),
                    request_options={"timeout": 30}  # 30 second timeout
                )
                
                elapsed = time.time() - start_time
                description_text = response.text.strip()
                print(f"    [GEMINI] ✅ Generated {len(description_text)} char description ({elapsed:.1f}s)")
            except Exception as e:
                error_msg = str(e)
                print(f"    [GEMINI] ⚠️  Stage D failed: {error_msg[:100]}")
                # Rate limit check
                if "429" in error_msg or "quota" in error_msg.lower() or "rate" in error_msg.lower():
                    print(f"    [GEMINI] ⚠️  Rate limit hit - skipping Stage D for this table")
        
        # Return description or fallback
        if description_text:
            # Clean up markdown formatting if AI added it anyway
            description_text = description_text.replace('**', '').replace('##', '').replace('###', '')
            return description_text
        else:
            # Fallback: basic description
            return f"{table_number} ({title}) contains technical specifications for AS/NZS 3000 electrical standards. This table includes {len(rows)} rows of data with {len(footer_notes)} associated notes."
    
    except Exception as e:
        print(f"    [YAML-DESC] ❌ Failed to generate YAML description: {e}")
        table_number = table_data.get("table_number") or "Unknown"
        title = table_data.get("title") or "Untitled"
        return f"{table_number} ({title}) - Description generation failed."


def extract_table_with_vision_api(table_image, page_num, table_index, groq_api_key=None, gemini_api_key=None):
    """
    Extract table using TWO-STAGE approach with multi-provider support.
    
    Supports both Groq Vision API and Google Gemini API with automatic fallback.
    Priority: Groq (faster) → Gemini (more generous free tier)
    
    Stage A: Metadata detection (table number, title, continuation, etc.)
    Stage B: Structure extraction with row typing (header/group/data/note/blank)
    Stage C: Separate notes extraction from bottom crop
    
    Args:
        table_image: PIL Image of table region (enhanced by Real-ESRGAN)
        page_num: Page number for logging
        table_index: Table index on page for logging
        groq_api_key: Groq API key from Modal secret (optional)
        gemini_api_key: Google Gemini API key from Modal secret (optional)
        
    Returns:
        {
            "table_number": "Table 4.1" or null,
            "title": "Current-carrying capacity" or null,
            "page_number": 42,
            "continues_to_next_page": false,
            "has_notes": true,
            "estimated_column_count": 5,
            "rows": [
                {"type": "header", "cells": ["Col1", "Col2", ...]},
                {"type": "group", "cells": ["Thermoplastic", "", ...]},
                {"type": "data", "cells": ["val1", "val2", ...]},
                {"type": "note", "cells": ["* Note text", "", ...]}
            ],
            "footer_notes": ["Note 1: ...", "Note 2: ..."],
            "row_count": int,
            "column_count": int,
            "header_rows": [[...]] (legacy compatibility),
            "data_rows": [[...]] (legacy compatibility),
            "provider": "groq" or "gemini"
        }
    """
    import base64
    from io import BytesIO
    from PIL import Image
    import json
    
    # Provider policy: Google Gemini only (avoids cross-provider table drift in production)
    if not gemini_api_key:
        raise ValueError("No Gemini API key provided. Need GEMINI_API_KEY in Modal secrets.")
    provider = "gemini"
    print(f"    [PROVIDER] Using Google Gemini API (vision + structure)")
    
    try:
        # Convert PIL Image to base64 (compress to reduce size)
        buffered = BytesIO()
        
        # Check pixel limits (Groq: 33M, Gemini: higher)
        pixel_count = table_image.width * table_image.height
        max_pixels = 50_000_000  # Gemini image limits (keep conservative; we also JPEG-compress)
        
        if pixel_count > max_pixels:
            # Resize image to fit under limit while maintaining aspect ratio
            scale_factor = (max_pixels / pixel_count) ** 0.5
            new_width = int(table_image.width * scale_factor)
            new_height = int(table_image.height * scale_factor)
            table_image = table_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            print(f"    [{provider.upper()}] Resized image from {pixel_count:,} to {new_width}x{new_height} ({new_width*new_height:,} pixels)")
        
        # Convert to JPEG with quality=85 to reduce file size
        table_image.save(buffered, format="JPEG", quality=85, optimize=True)
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        # ====================================================================
        # STAGE A: METADATA DETECTION
        # ====================================================================
        
        metadata_prompt = """Analyze this table image and extract ONLY metadata.

IMPORTANT: This image includes:
- Region ABOVE table: Table number and title/caption text
- Table body: The actual table structure
- Region BELOW table: Continuation markers and footer notes

DO NOT extract table content yet. Focus ONLY on:

1. Table number (e.g., "Table 4.1", "Table C1") - Look ABOVE the table
2. Table title/caption (not column headers) - Look ABOVE the table
3. Page number (if visible)
4. Whether table continues to next page - Look for "(continued)", "(cont.)", or "Table continues" BELOW or ABOVE table
5. Whether footnotes/notes exist at bottom - Look BELOW the table body
6. Estimated column count

STRICT RULES:
- Table number is typically ABOVE the table (e.g., "Table 3.1" or "3.1")
- Title is the descriptive text ABOVE table (not column headers inside table)
- Continuation markers can appear ABOVE ("Table 3.1 (continued)") or BELOW table
- If table number not visible, return null (do not invent)
- Do not use column headers as title
- Be conservative: if uncertain, return null
- Count columns carefully (including merged cells)

OUTPUT FORMAT (JSON only):
{
  "table_number": "Table 4.1" or null,
  "title": "Current-carrying capacity for cables" or null,
  "page_number": 42 or null,
  "continues_to_next_page": false,
  "has_notes": true,
  "estimated_column_count": 5
}

Return ONLY valid JSON, no additional text."""
        
        print(f"    [{provider.upper()}] Stage A: Metadata detection...")
        
        # Try calling provider with automatic fallback
        metadata_text = None
        stage_a_provider = provider
        
        # Try primary provider first
        if provider == "groq" and groq_api_key:
            try:
                from groq import Groq
                client = Groq(api_key=groq_api_key)
                
                metadata_response = client.chat.completions.create(
                    model="meta-llama/llama-4-scout-17b-16e-instruct",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": metadata_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{img_base64}"
                                    }
                                }
                            ]
                        }
                    ],
                    temperature=0.1,
                    max_tokens=512,
                    top_p=0.9
                )
                metadata_text = metadata_response.choices[0].message.content.strip()
            except Exception as e:
                print(f"    [GROQ] ⚠️  Stage A failed: {e}")
                if gemini_api_key:
                    print(f"    [FALLBACK] Trying Gemini for Stage A...")
                    stage_a_provider = "gemini"
                else:
                    raise
        
        # Use Gemini (either as fallback or primary)
        if metadata_text is None and gemini_api_key:
            import google.generativeai as genai
            genai.configure(api_key=gemini_api_key)
            model = genai.GenerativeModel('gemini-2.5-flash')
            
            response = model.generate_content(
                [
                    metadata_prompt,
                    Image.open(BytesIO(base64.b64decode(img_base64)))
                ],
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=2048,
                )
            )
            metadata_text = response.text.strip()
        
        if metadata_text is None:
            raise ValueError("Stage A failed on all providers")
        
        # Parse metadata response (common for both providers)
        if metadata_text.startswith("```"):
            metadata_text = metadata_text.split("```")[1]
            if metadata_text.startswith("json"):
                metadata_text = metadata_text[4:].strip()
        
        metadata = json.loads(metadata_text)
        metadata["table_number"] = _normalize_table_number(metadata.get("table_number"))
        print(f"    [{stage_a_provider.upper()}] ✅ Metadata: table_number={metadata.get('table_number')}, columns≈{metadata.get('estimated_column_count')}")
        
        # ====================================================================
        # STAGE B: STRUCTURE EXTRACTION WITH ROW TYPING
        # ====================================================================
        
        structure_prompt = """Extract this table structure with STRICT row type classification.

You are analyzing AS/NZS electrical standards documentation. Be aggressive with OCR error correction.

COMMON OCR ERRORS TO FIX:
- "BARLEY" or "BARRY" → "BURIED" (installation method)
- "INSUATED" → "INSULATED"
- "SHEATHEA" or "SHEATHER" → "SHEATHED"
- "Please" or "mims" → "MIMS" (Mineral Insulated Metal Sheathed)
- "CONDUTOR" → "CONDUCTOR"
- "SUROUNDED" → "SURROUNDED"
- "FAULTY" → "FULLY" (when followed by SURROUNDED)
- "Meningeal" → "MAINS" or "MINERAL"
- Number corruptions: "8388" → likely "100", "88" → "90"

TECHNICAL TERMS (preserve exactly):
- Installation methods: BURIED, ENCLOSED, UNENCLOSED, CLIPPED DIRECT, IN CONDUIT
- Cable types: INSULATED, SHEATHED, ARMOURED, MIMS, PVC, XLPE
- Conditions: THERMAL INSULATION, FULLY SURROUNDED, PARTIALLY SURROUNDED
- Standards: AS/NZS 3008, IEC, reference methods (A1, A2, B, C, D, E, etc.)

ROW TYPE CLASSIFICATION (CRITICAL):
Classify each row as ONE of these types:

1. "header" - Column headers (e.g., "Current (A)", "Cable Type", "Reference Method")
2. "group" - Category/section headers (e.g., "Thermoplastic", "Elastomeric", "BURIED CABLES")
3. "data" - Normal data rows with measurements/specifications
4. "note" - Inline notes within table (e.g., "* See note 1")
5. "blank" - Empty separator rows

EXAMPLES:
- "Thermoplastic" spanning multiple columns = GROUP
- "Elastomeric" spanning multiple columns = GROUP
- "BURIED DIRECT IN GROUND" = GROUP
- "2.5 | 24 | 21 | 19" = DATA
- "* Maximum operating temperature 90°C" = NOTE
- Empty row = BLANK

STRICT RULES:
- Do not invent data - extract exactly what you see
- Preserve empty cells as "" (not null, not "N/A")
- Preserve row order exactly as seen
- Do not merge title with header text
- Preserve note markers exactly (* (1) (2) †)
- Group rows usually span multiple columns
- If uncertain about cell value, use "" not guessed text
- Be conservative, not clever

OUTPUT FORMAT (JSON only):
{
  "rows": [
    {"type": "header", "cells": ["Column 1", "Column 2", "Column 3"]},
    {"type": "group", "cells": ["Thermoplastic", "", ""]},
    {"type": "data", "cells": ["2.5", "24", "21"]},
    {"type": "note", "cells": ["* Note text", "", ""]}
  ]
}

Return ONLY valid JSON, no additional text. Do NOT include footer notes here (they will be extracted separately)."""
        
        print(f"    [{provider.upper()}] Stage B: Structure extraction with row typing...")
        
        structure_text = None
        stage_b_provider = provider
        
        # Try primary provider first
        if provider == "groq" and groq_api_key:
            try:
                from groq import Groq
                if 'client' not in locals():
                    client = Groq(api_key=groq_api_key)
                
                structure_response = client.chat.completions.create(
                    model="meta-llama/llama-4-scout-17b-16e-instruct",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": structure_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{img_base64}"
                                    }
                                }
                            ]
                        }
                    ],
                    temperature=0.2,
                    max_tokens=4096,
                    top_p=0.95
                )
                structure_text = structure_response.choices[0].message.content.strip()
            except Exception as e:
                print(f"    [GROQ] ⚠️  Stage B failed: {e}")
                if gemini_api_key:
                    print(f"    [FALLBACK] Trying Gemini for Stage B...")
                    stage_b_provider = "gemini"
                else:
                    raise
        
        # Use Gemini (either as fallback or primary)
        if structure_text is None and gemini_api_key:
            import google.generativeai as genai
            if 'model' not in locals():
                genai.configure(api_key=gemini_api_key)
                model = genai.GenerativeModel('gemini-2.5-flash')
            
            response = model.generate_content(
                [
                    structure_prompt,
                    Image.open(BytesIO(base64.b64decode(img_base64)))
                ],
                generation_config=genai.GenerationConfig(
                    temperature=0.2,
                    max_output_tokens=8192,
                )
            )
            structure_text = response.text.strip()
        
        if structure_text is None:
            raise ValueError("Stage B failed on all providers")
        
        # Parse structure response (common for both providers)
        if structure_text.startswith("```"):
            structure_text = structure_text.split("```")[1]
            if structure_text.startswith("json"):
                structure_text = structure_text[4:].strip()
        
        structure_data = json.loads(structure_text)
        rows = structure_data.get("rows", [])
        
        # Count row types
        row_type_counts = {}
        for row in rows:
            row_type = row.get("type", "unknown")
            row_type_counts[row_type] = row_type_counts.get(row_type, 0) + 1
        
        print(f"    [{stage_b_provider.upper()}] ✅ Extracted {len(rows)} rows: {row_type_counts}")

        data_row_count = _count_data_rows(rows)
        if data_row_count == 0:
            print(f"    [{stage_b_provider.upper()}] ⏭️  Skipping Stage C/D (0 data rows)")
            footer_notes: List[str] = []
            yaml_description = ""
            row_count = 0
            column_count = 0
            header_rows: List = []
            data_rows: List = []
            return {
                "table_number": metadata.get("table_number"),
                "title": metadata.get("title"),
                "page_number": metadata.get("page_number", page_num),
                "continues_to_next_page": metadata.get("continues_to_next_page", False),
                "has_notes": False,
                "estimated_column_count": metadata.get("estimated_column_count", 0),
                "rows": rows,
                "footer_notes": footer_notes,
                "row_type_counts": row_type_counts,
                "yaml_description": yaml_description,
                "header_rows": header_rows,
                "data_rows": data_rows,
                "row_count": row_count,
                "column_count": column_count,
                "extraction_method": f"{provider}_vision_two_stage",
                "provider": provider,
                "stage_providers": {
                    "metadata": stage_a_provider,
                    "structure": stage_b_provider,
                    "notes": "skipped",
                },
                "skipped_stages": "zero_data_rows",
            }

        # ====================================================================
        # STAGE C: SEPARATE NOTES EXTRACTION (from bottom crop)
        # ====================================================================
        
        # Crop bottom 20% of image for notes extraction
        height = table_image.height
        notes_crop_top = int(height * 0.8)  # Bottom 20%
        notes_region = table_image.crop((0, notes_crop_top, table_image.width, height))
        
        # Convert notes region to base64
        notes_buffered = BytesIO()
        notes_region.save(notes_buffered, format="JPEG", quality=85, optimize=True)
        notes_img_base64 = base64.b64encode(notes_buffered.getvalue()).decode()
        
        notes_prompt = """Extract ONLY footnotes/notes from this image region (bottom of table).

IMPORTANT: This is the BOTTOM portion of the table image, which typically contains:
- Footer notes with markers: *, †, ‡, (1), (2), (3)
- Continuation markers: "(continued)", "(cont.)", "Table continues on next page"
- Conditions and exceptions
- Reference information

Look for:
- Numbered notes: (1), (2), (3)
- Symbol notes: *, †, ‡
- Text starting with "Note:", "NOTE:", "Notes:"
- Conditions or exceptions at bottom of table
- Small text below the table grid
- Any text that provides context or clarification

STRICT RULES:
- Extract note text exactly as shown
- Preserve note markers/symbols
- One note per array element
- If no notes visible, return empty array []
- Do not extract table data rows (only notes/footnotes)
- Include continuation markers if present (e.g., "(continued)")
- Be conservative: if uncertain, return []

OUTPUT FORMAT (JSON only):
{
  "footer_notes": [
    "* Maximum operating temperature 90°C",
    "(1) For ambient temperature exceeding 30°C",
    "Note: Values apply to cables in free air",
    "(continued)"
  ]
}

Return ONLY valid JSON, no additional text."""
        
        print(f"    [{provider.upper()}] Stage C: Notes extraction from bottom crop...")
        
        notes_text = None
        stage_c_provider = provider
        
        # Try primary provider first
        if provider == "groq" and groq_api_key:
            try:
                from groq import Groq
                if 'client' not in locals():
                    client = Groq(api_key=groq_api_key)
                
                notes_response = client.chat.completions.create(
                    model="meta-llama/llama-4-scout-17b-16e-instruct",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": notes_prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{notes_img_base64}"
                                    }
                                }
                            ]
                        }
                    ],
                    temperature=0.1,
                    max_tokens=1024,
                    top_p=0.9
                )
                notes_text = notes_response.choices[0].message.content.strip()
            except Exception as e:
                print(f"    [GROQ] ⚠️  Stage C failed: {e}")
                if gemini_api_key:
                    print(f"    [FALLBACK] Trying Gemini for Stage C...")
                    stage_c_provider = "gemini"
                else:
                    raise
        
        # Use Gemini (either as fallback or primary)
        if notes_text is None and gemini_api_key:
            import google.generativeai as genai
            if 'model' not in locals():
                genai.configure(api_key=gemini_api_key)
                model = genai.GenerativeModel('gemini-2.5-flash')
            
            response = model.generate_content(
                [
                    notes_prompt,
                    Image.open(BytesIO(base64.b64decode(notes_img_base64)))
                ],
                generation_config=genai.GenerationConfig(
                    temperature=0.1,
                    max_output_tokens=2048,
                )
            )
            notes_text = response.text.strip()
        
        if notes_text is None:
            raise ValueError("Stage C failed on all providers")
        
        # Parse notes response (common for both providers)
        if notes_text.startswith("```"):
            notes_text = notes_text.split("```")[1]
            if notes_text.startswith("json"):
                notes_text = notes_text[4:].strip()
        
        notes_data = json.loads(notes_text)
        footer_notes = notes_data.get("footer_notes", [])
        
        print(f"    [{stage_c_provider.upper()}] ✅ Extracted {len(footer_notes)} footer notes")
        
        # ====================================================================
        # STAGE D: GENERATE CLAUSE-LIKE YAML DESCRIPTION (NEW!)
        # ====================================================================
        
        # Build comprehensive table representation for AI
        yaml_description = generate_yaml_description_for_table(
            table_data={
                "table_number": metadata.get("table_number"),
                "title": metadata.get("title"),
                "page_number": metadata.get("page_number", page_num),
                "rows": rows,
                "footer_notes": footer_notes,
                "row_type_counts": row_type_counts
            },
            provider=provider,
            groq_api_key=groq_api_key,
            gemini_api_key=gemini_api_key
        )
        
        # ====================================================================
        # COMBINE ALL STAGES + LEGACY COMPATIBILITY
        # ====================================================================
        
        # Calculate dimensions
        row_count = len(rows)
        column_count = 0
        if rows:
            column_count = len(rows[0].get("cells", []))
        
        # Create legacy format for backward compatibility
        header_rows = []
        data_rows = []
        for row in rows:
            row_type = row.get("type")
            cells = row.get("cells", [])
            if row_type == "header":
                header_rows.append(cells)
            elif row_type in ["data", "group", "note"]:
                data_rows.append(cells)
        
        result = {
            # New two-stage format
            "table_number": metadata.get("table_number"),
            "title": metadata.get("title"),
            "page_number": metadata.get("page_number", page_num),
            "continues_to_next_page": metadata.get("continues_to_next_page", False),
            "has_notes": metadata.get("has_notes", len(footer_notes) > 0),
            "estimated_column_count": metadata.get("estimated_column_count", column_count),
            "rows": rows,
            "footer_notes": footer_notes,
            "row_type_counts": row_type_counts,
            "yaml_description": yaml_description,  # NEW: AI-generated clause-like description
            
            # Legacy format (backward compatibility)
            "header_rows": header_rows,
            "data_rows": data_rows,
            "row_count": row_count,
            "column_count": column_count,
            "extraction_method": f"{provider}_vision_two_stage",
            "provider": provider,
            "stage_providers": {
                "metadata": stage_a_provider,
                "structure": stage_b_provider,
                "notes": stage_c_provider
            }
        }
        
        # Report which providers were used for each stage
        stages_summary = f"A:{stage_a_provider[0].upper()}, B:{stage_b_provider[0].upper()}, C:{stage_c_provider[0].upper()}"
        print(f"    [COMPLETE] ✅ Stages ({stages_summary}): {row_count} rows, {len(footer_notes)} notes")
        
        return result
        
    except json.JSONDecodeError as e:
        print(f"    [{provider.upper() if provider else 'VISION'}] ⚠️  JSON parsing failed: {e}")
        failed_text = locals().get('metadata_text') or locals().get('structure_text') or locals().get('notes_text', 'unknown')
        print(f"    [{provider.upper() if provider else 'VISION'}] Failed stage response: {failed_text[:200]}...")
        # Return empty table structure
        return {
            "table_number": None,
            "title": None,
            "page_number": page_num,
            "continues_to_next_page": False,
            "has_notes": False,
            "estimated_column_count": 0,
            "rows": [],
            "footer_notes": [],
            "header_rows": [],
            "data_rows": [],
            "row_count": 0,
            "column_count": 0,
            "extraction_method": "groq_vision_two_stage_failed"
        }
    
    except Exception as e:
        print(f"    [{provider.upper() if provider else 'VISION'}] ❌ Extraction failed: {e}")
        import traceback
        traceback.print_exc()
        # Return empty table structure
        return {
            "table_number": None,
            "title": None,
            "page_number": page_num,
            "continues_to_next_page": False,
            "has_notes": False,
            "estimated_column_count": 0,
            "rows": [],
            "footer_notes": [],
            "header_rows": [],
            "data_rows": [],
            "row_count": 0,
            "column_count": 0,
            "extraction_method": "groq_vision_two_stage_error"
        }


# ============================================================================
# REAL-ESRGAN: AI Super-Resolution for Maximum Quality
# ============================================================================

def initialize_realesrgan(device):
    """
    Initialize Real-ESRGAN for image enhancement.
    
    Uses RealESRGAN_x4plus model (trained for 4x) but applies 2x upscaling.
    Processes PDFs to enhance image quality for table extraction.
    
    Strategy: Pre-table-extraction enhancement - images enhanced BEFORE Groq Vision API
    Target: 300 DPI → 600 DPI equivalent detail reconstruction
    
    Args:
        device: torch device (cuda/cpu)
        
    Returns:
        RealESRGANer instance configured for maximum quality
    """
    from realesrgan import RealESRGANer
    from basicsr.archs.rrdbnet_arch import RRDBNet
    from basicsr.utils.download_util import load_file_from_url
    import os
    
    print("  🌟 Loading Real-ESRGAN (RealESRGAN_x4plus - 2x Enhancement Mode)...")
    
    # Define RealESRGAN_x4plus model (best quality, 23-block architecture)
    model = RRDBNet(
        num_in_ch=3,
        num_out_ch=3,
        num_feat=64,
        num_block=23,        # Full 23-block model for maximum quality
        num_grow_ch=32,
        scale=4              # Model trained for 4x, but we'll use outscale=2
    )
    
    # Download model weights if not present
    model_path = os.path.join('weights', 'RealESRGAN_x4plus.pth')
    if not os.path.exists(model_path):
        print("  📥 Downloading RealESRGAN_x4plus model (67MB)...")
        os.makedirs('weights', exist_ok=True)
        model_url = 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth'
        model_path = load_file_from_url(
            url=model_url,
            model_dir='weights',
            progress=True,
            file_name='RealESRGAN_x4plus.pth'
        )
        print(f"  ✅ Model downloaded to {model_path}")
    
    # Initialize upsampler with 2x enhancement (reduced from 4x for Groq API compatibility)
    upsampler = RealESRGANer(
        scale=4,                    # Model scale (trained for 4x)
        model_path=model_path,
        model=model,
        tile=128,                   # Tile size for processing
        tile_pad=10,
        pre_pad=0,
        half=True,                  # FP16 precision for T4 GPU efficiency
        device='cuda' if str(device) == 'cuda' else None
    )
    
    print("  ✅ Real-ESRGAN loaded (2x upscale mode for Groq API compatibility)")
    print("  ℹ️  Enhancement will apply to pages with tables only")
    
    return upsampler


def enhance_image_quality(image, upsampler):
    """
    Apply Real-ESRGAN 2x enhancement.
    
    Reconstructs detail from low-quality sources, removes compression artifacts,
    and sharpens text/symbols for optimal OCR accuracy.
    
    Args:
        image: PIL Image at 300 DPI
        upsampler: RealESRGANer instance
                         
    Returns:
        Enhanced PIL Image (600 DPI equivalent detail)
    """
    from PIL import Image
    import numpy as np
    
    try:
        # Convert PIL Image to numpy array (required by Real-ESRGAN)
        img_np = np.array(image)
        
        # Apply 2x enhancement (reduced from 4x for Groq API pixel limit)
        # Groq limit: 33M pixels. 2x enhancement keeps us under this limit.
        # This still removes:
        # - JPEG compression artifacts
        # - Paper texture (scanned docs)
        # - Scanner noise
        # - Low-resolution blur
        enhanced_np, _ = upsampler.enhance(
            img_np,
            outscale=2  # 2x upscale to stay under Groq's 33M pixel limit
        )
        
        # Convert back to PIL Image
        enhanced_img = Image.fromarray(enhanced_np)
        
        return enhanced_img
        
    except RuntimeError as e:
        # Handle GPU out-of-memory errors
        if "out of memory" in str(e).lower():
            print(f"    ⚠️  GPU OOM during enhancement")
            print(f"    📉 Falling back to original image (T4 GPU memory insufficient)")
            return image  # Fallback without trying to modify tile size
        else:
            print(f"    ❌ Enhancement failed: {e}")
            print(f"    📉 Falling back to original image (no enhancement)")
            return image
    
    except Exception as e:
        print(f"    ⚠️  Unexpected error during enhancement: {e}")
        print(f"    📉 Falling back to original image (no enhancement)")
        return image





# ============================================================================
# TABLES: GPU-Based Extraction (Full Multi-Engine Pipeline)
# ============================================================================

def extract_tables_from_pdf(
    pdf_bytes: bytes, filename: str, enable_enhancement: bool = True, llm_provider: str = "gemini"
) -> Dict[str, Any]:
    """
    Extract complete tables using PRODUCTION ARCHITECTURE:
    
    Pipeline:
      1. Detect PDF type (digital vs scanned)
      2. Extract native text with PyMuPDF if digital
      3. Convert PDF to high-res images (300 DPI)
      4. Initialize Table Transformer for table detection
      5. Quick detection scan: Find pages with tables
      6. Real-ESRGAN 2x enhancement (OPTIONAL - only if enable_enhancement=True)
      7. Groq Vision API: Extract tables directly from images (enhanced or original)
      8. Return structured data
    
    Args:
        pdf_bytes: PDF file content as bytes
        filename: Name of the PDF file
        enable_enhancement: If True, apply Real-ESRGAN 2x enhancement to pages with tables.
                          If False, skip enhancement for faster processing (2-3 hours vs 6-8 hours).
                          Default: True (quality mode)
    
    Returns:
        {
            "success": True,
            "tables": [...],
            "table_count": 84,
            "processing_time": 45.2,
            "pdf_type": "digital",
            "text_coverage": 95.3,
            "extraction_method": "groq_vision",
            "enhancement_enabled": True
        }
    """
    import time
    import torch
    import numpy as np
    import cv2
    from transformers import AutoImageProcessor, TableTransformerForObjectDetection
    from pdf2image import convert_from_bytes
    from PIL import Image
    
    start_time = time.time()
    print(f"📊 Starting PRODUCTION table extraction for {filename}")
    print("=" * 70)
    
    try:
        # STEP 1: Detect PDF type
        print("\n🔍 STEP 1: PDF Type Detection")
        print("-" * 70)
        is_digital, text_coverage = is_digital_pdf(pdf_bytes)
        pdf_type = "digital" if is_digital else "scanned"
        print(f"  PDF Type: {pdf_type.upper()}")
        print(f"  Text Coverage: {text_coverage:.1f}%")
        
        # STEP 2: Extract native text if digital
        native_text_data = {}
        if is_digital:
            print("\n📄 STEP 2: Native Text Extraction (PyMuPDF)")
            print("-" * 70)
            native_text_data = extract_native_text_with_coordinates(pdf_bytes)
            print(f"  ✅ Extracted native text from {len(native_text_data)} pages")
        else:
            print("\n⏭️  STEP 2: Skipped (scanned PDF - no native text)")
        
        # STEP 3: Convert PDF to images
        print("\n🖼️  STEP 3: PDF to Image Conversion")
        print("-" * 70)
        images = convert_from_bytes(pdf_bytes, dpi=300)
        print(f"  ✅ Converted to {len(images)} images at 300 DPI (optimized for Real-ESRGAN)")
        print(f"  ℹ️  Strategy: 300 DPI × 2x Real-ESRGAN = 600 DPI equivalent")
        
        # STEP 4: Initialize models (BEFORE enhancement for selective processing)
        print("\n🤖 STEP 4: Model Initialization")
        print("-" * 70)
        
        # Table Transformer
        print("  Loading Table Transformer models...")
        detection_processor = AutoImageProcessor.from_pretrained(
            "microsoft/table-transformer-detection"
        )
        detection_model = TableTransformerForObjectDetection.from_pretrained(
            "microsoft/table-transformer-detection"
        )
        structure_processor = AutoImageProcessor.from_pretrained(
            "microsoft/table-transformer-structure-recognition"
        )
        structure_model = TableTransformerForObjectDetection.from_pretrained(
            "microsoft/table-transformer-structure-recognition"
        )
        
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        detection_model = detection_model.to(device)
        structure_model = structure_model.to(device)
        detection_model.eval()
        structure_model.eval()
        print(f"  ✅ Table Transformer loaded on {device}")
        
        # Vision LLM policy: default to Google Gemini only (avoids multi-provider fallback chains in production)
        p = (llm_provider or os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()
        if p != "gemini":
            print(f"  ⚠️  LLM_PROVIDER={p!r} is not supported; forcing gemini-only table vision.")
        groq_api_key = None
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("GEMINI_API_KEY missing in Modal secrets (Gemini-only mode)")
        print("  ✅ Gemini API key loaded (Gemini-only mode)")
        
        # STEP 5: Quick Table Detection Scan (on original images)
        print("\n🔍 STEP 5: Quick Table Detection Scan (Selective Enhancement Strategy)")
        print("-" * 70)
        print(f"  Strategy: Detect tables first, then enhance ONLY pages with tables")
        print(f"  Scanning {len(images)} pages...")
        
        pages_with_tables = set()
        detection_start = time.time()
        
        for page_num, image in enumerate(images, start=1):
            inputs = detection_processor(images=image, return_tensors="pt")
            inputs = {k: v.to(device) for k, v in inputs.items()}
            
            with torch.no_grad():
                outputs = detection_model(**inputs)
            
            target_sizes = torch.tensor([image.size[::-1]])
            detection_results = detection_processor.post_process_object_detection(
                outputs, threshold=0.8, target_sizes=target_sizes
            )[0]
            
            if len(detection_results["scores"]) > 0:
                pages_with_tables.add(page_num)
        
        detection_time = time.time() - detection_start
        print(f"  ✅ Detection complete in {detection_time:.1f}s")
        print(f"  📊 Tables found on {len(pages_with_tables)}/{len(images)} pages")
        print(f"  📄 Pages with tables: {sorted(pages_with_tables)}")
        print(f"  ⚡ Time savings: {len(images) - len(pages_with_tables)} pages skipped!")
        
        # STEP 6: Selective Real-ESRGAN Enhancement (OPTIONAL)
        print("\n🌟 STEP 6: Real-ESRGAN Selective Enhancement")
        print("-" * 70)
        
        total_enhancement_time = 0
        if not enable_enhancement:
            print(f"  ⚡ FAST MODE: Enhancement disabled by user")
            print(f"  ℹ️  Processing with original 300 DPI images (faster, may reduce accuracy)")
            print(f"  💡 Enable enhancement for higher quality (enable_enhancement=True)")
        elif len(pages_with_tables) > 0:
            print(f"  🎯 QUALITY MODE: Enhancement enabled")
            print(f"  Strategy: 2x upscale ONLY on {len(pages_with_tables)} pages with tables")
            print(f"  Target: 300 DPI → 600 DPI equivalent detail reconstruction")
            print(f"  ℹ️  Using Groq Vision API for extraction")
            
            upsampler = initialize_realesrgan(device)
            enhancement_start = time.time()
            
            for page_num in sorted(pages_with_tables):
                page_start = time.time()
                print(f"    Page {page_num}/{len(images)}: Applying 2x super-resolution...")
                original_image = images[page_num - 1]
                enhanced = enhance_image_quality(original_image, upsampler)
                images[page_num - 1] = enhanced
                page_time = time.time() - page_start
                print(f"      ✅ Enhanced in {page_time:.1f}s")
            
            total_enhancement_time = time.time() - enhancement_start
            avg_time_per_page = total_enhancement_time / len(pages_with_tables) if pages_with_tables else 0
            time_saved = (len(images) - len(pages_with_tables)) * avg_time_per_page if avg_time_per_page > 0 else 0
            print(f"  ✅ {len(pages_with_tables)} pages enhanced in {total_enhancement_time:.1f}s")
            print(f"  📊 Average: {avg_time_per_page:.1f}s per page")
            print(f"  💰 Time saved vs full enhancement: {time_saved:.1f}s ({time_saved/60:.1f} min)")
            
            # Free Real-ESRGAN GPU memory
            del upsampler
            torch.cuda.empty_cache()
            print(f"  ✅ Real-ESRGAN unloaded, GPU memory freed")
        else:
            print(f"  ℹ️  No tables detected - skipping Real-ESRGAN enhancement")
        
        # STEP 7: Extract tables with Groq Vision
        print("\n📊 STEP 7: Table Extraction with Groq Vision API")
        print("-" * 70)
        all_tables = []
        # page_index_fallback: native PyMuPDF caption region had no TABLE N.M (not Groq — name kept below for JSON compat).
        caption_method_stats = {"native": 0, "page_index_fallback": 0, "groq_fallback": 0, "failed": 0}
        
        for page_num, image in enumerate(images, start=1):
            print(f"  Processing page {page_num}/{len(images)}...")
            
            # Get native text for this page (for captions)
            page_native_texts = native_text_data.get(page_num, [])
            
            # Detect table regions
            inputs = detection_processor(images=image, return_tensors="pt")
            inputs = {k: v.to(device) for k, v in inputs.items()}
            
            with torch.no_grad():
                outputs = detection_model(**inputs)
            
            target_sizes = torch.tensor([image.size[::-1]])
            detection_results = detection_processor.post_process_object_detection(
                outputs, threshold=0.8, target_sizes=target_sizes
            )[0]
            
            # Process each detected table
            table_index = 0
            for detection_score, detection_box in zip(
                detection_results["scores"], detection_results["boxes"]
            ):
                table_index += 1
                box_coords = detection_box.cpu().tolist()
                x0, y0, x1, y1 = box_coords
                
                # CRITICAL FIX: Expand crop region to include:
                # - Caption/title above table (100px above)
                # - Footer notes below table (150px below)
                # - Side padding (20px on each side)
                padding_top = 100
                padding_bottom = 150
                padding_sides = 20
                
                expanded_x0 = max(0, x0 - padding_sides)
                expanded_y0 = max(0, y0 - padding_top)
                expanded_x1 = min(image.width, x1 + padding_sides)
                expanded_y1 = min(image.height, y1 + padding_bottom)
                
                print(f"    📏 Table bbox: ({x0:.0f}, {y0:.0f}, {x1:.0f}, {y1:.0f})")
                print(f"    📐 Expanded bbox: ({expanded_x0:.0f}, {expanded_y0:.0f}, {expanded_x1:.0f}, {expanded_y1:.0f})")
                print(f"    ➕ Added: {padding_top}px above, {padding_bottom}px below, {padding_sides}px sides")
                
                # Crop with expanded region to capture title, notes, and continuation markers
                table_image = image.crop((expanded_x0, expanded_y0, expanded_x1, expanded_y1))
                
                # CAPTION EXTRACTION: Try native text first (fast, accurate for captions)
                caption_info = None
                if page_native_texts:
                    pdf_page_height = page_native_texts[0].get("page_height") if page_native_texts else None
                    caption_info = find_caption_in_native_text(
                        page_native_texts, (x0, y0, x1, y1), image.height, pdf_page_height
                    )
                    if caption_info and caption_info.get("table_number"):
                        caption_method_stats["native"] += 1
                        print(f"    ✅ Found native table number: '{caption_info.get('table_number')}'")

                # Skip glossary/figure regions before Gemini calls
                pdf_page_height = (
                    page_native_texts[0].get("page_height") if page_native_texts else None
                )
                caption_probe = _peek_caption_region_text(
                    page_native_texts, (x0, y0, x1, y1), image.height, pdf_page_height
                )
                if _is_false_table_region_text(caption_probe):
                    print(f"    [SKIP] Non-table region (glossary/figure/bibliography)")
                    continue

                # VISION API: Extract table content (Gemini only)
                table_content = extract_table_with_vision_api(
                    table_image,
                    page_num,
                    table_index,
                    groq_api_key=None,
                    gemini_api_key=gemini_api_key,
                )

                if table_content.get("skipped_stages") == "zero_data_rows":
                    print(f"    [SKIP] 0 data rows after Stage B")
                    continue

                # Resolve table number: Stage A > native caption > page.index fallback
                table_number = (
                    _normalize_table_number(table_content.get("table_number"))
                    or (caption_info and _normalize_table_number(caption_info.get("table_number")))
                )
                if not table_number:
                    table_number = f"{page_num}.{table_index}"
                    caption_method_stats["page_index_fallback"] += 1
                    print(f"    ⚠️  No table number found, using fallback: '{table_number}'")

                title = (
                    table_content.get("title")
                    or (caption_info.get("title") if caption_info else None)
                )

                # Build table result (combining legacy caption detection + two-stage extraction)
                table_result = {
                    # Page and detection info
                    "page": page_num,
                    "confidence": float(detection_score),
                    "bbox": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},

                    # Metadata (prefer Gemini Stage A, then native caption)
                    "table_number": table_number,
                    "title": title,
                    "page_number": table_content.get("page_number", page_num),
                    "continues_to_next_page": table_content.get("continues_to_next_page", False),
                    "has_notes": table_content.get("has_notes", False),
                    "estimated_column_count": table_content.get("estimated_column_count"),
                    
                    # NEW: AI-generated YAML description
                    "yaml_description": table_content.get("yaml_description"),
                    
                    # New two-stage extraction format
                    "rows": table_content.get("rows", []),
                    "footer_notes": table_content.get("footer_notes", []),
                    "row_type_counts": table_content.get("row_type_counts", {}),
                    
                    # Legacy format (backward compatibility)
                    "header_rows": table_content.get("header_rows", []),
                    "data_rows": table_content.get("data_rows", []),
                    "row_count": table_content.get("row_count", 0),
                    "column_count": table_content.get("column_count", 0),
                    "extraction_method": table_content.get("extraction_method", "groq_vision_two_stage"),
                }
                all_tables.append(table_result)
        
        processing_time = time.time() - start_time
        
        print("\n" + "=" * 70)
        print(f"✅ EXTRACTION COMPLETE")
        print(f"  Tables: {len(all_tables)}")
        print(f"  PDF Type: {pdf_type}")
        print(
            f"  Caption Methods: Native={caption_method_stats['native']}, "
            f"PageIndex_Fallback={caption_method_stats['page_index_fallback']} "
            f"(no TABLE # in caption region → page.table_index; not Groq)"
        )
        print(f"  Time: {processing_time:.2f}s")
        print("=" * 70)
        
        return {
            "success": True,
            "tables": all_tables,
            "table_count": len(all_tables),
            "processing_time": round(processing_time, 2),
            "enhancement_time": round(total_enhancement_time, 2),
            "enhancement_enabled": enable_enhancement,
            "pdf_type": pdf_type,
            "text_coverage": round(text_coverage, 2),
            "caption_methods": caption_method_stats,
            "architecture": "PyMuPDF+Real-ESRGAN+TableTransformer+GroqVision" if enable_enhancement else "PyMuPDF+TableTransformer+GroqVision",
            "model": "Groq Llama 4 Scout 17Bx16E Vision"
        }
        
    except Exception as e:
        print(f"\n❌ Table extraction error: {e}")
        import traceback
        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "tables": [],
            "table_count": 0,
        }





# ============================================================================
# CLAUSES: Rule-Based Parser (Deterministic, No AI Cost)
# ============================================================================

def extract_clauses_from_pdf(pdf_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    Extract structured clauses using rule-based parser with regex + state machine.
    Uses PyMuPDF for native text extraction (better than pypdf).
    
    Returns:
        {
            "success": True,
            "clauses": [...],
            "clause_count": 245,
            "processing_time": 2.5,
            "cost_estimate": 0.0
        }
    """
    import time
    import fitz  # PyMuPDF
    import io
    
    start_time = time.time()
    print(f"📝 Starting clause extraction for {filename}")
    
    try:
        # Extract text from PDF using PyMuPDF (better quality)
        print("  Extracting text from PDF with PyMuPDF...")
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        
        page_texts = []
        for page_num in range(total_pages):
            page = doc[page_num]
            text = page.get_text() or ""
            page_texts.append({
                "page": page_num + 1,
                "text": text
            })
        
        doc.close()
        print(f"  Extracted text from {total_pages} pages")
        
        # Parse using rule-based parser
        print("  Parsing clauses with rule-based parser...")
        clauses = parse_clauses_rule_based(page_texts)
        
        processing_time = time.time() - start_time
        print(f"  ✅ Extracted {len(clauses)} clauses in {processing_time:.2f}s")
        print(f"  💰 Cost estimate: $0.00 (rule-based, no AI)")
        
        return {
            "success": True,
            "clauses": clauses,
            "clause_count": len(clauses),
            "processing_time": round(processing_time, 2),
            "cost_estimate": 0.0,
            "pages_processed": total_pages,
        }
        
    except Exception as e:
        print(f"  ❌ Clause extraction error: {e}")
        import traceback
        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "clauses": [],
            "clause_count": 0,
        }


def parse_clauses_rule_based(page_texts: List[Dict]) -> List[Dict]:
    """Rule-based clause parser using regex patterns and state machine."""
    import uuid
    
    # Optional * prefix marks updated clauses in some standards PDFs (*10.2.3, * 10.2.3.1)
    numbered_pattern = re.compile(
        r'^(?:\*+\s*)?(\d+(?:\.\d+)*)\s+([A-Z][^\n]+?)(?:\n|$)',
        re.MULTILINE,
    )
    appendix_pattern = re.compile(r'^APPENDIX\s+([A-Z](?:\.\d+)*)\s*[-–—:]*\s*([^\n]*?)(?:\n|$)', re.MULTILINE | re.IGNORECASE)
    letter_pattern = re.compile(r'^\(([a-z])\)\s+(.+?)(?:\n|$)', re.MULTILINE)
    
    all_clauses = []
    clause_stack = []
    
    for page_data in page_texts:
        page_num = page_data["page"]
        text = page_data["text"]
        lines = text.split('\n')
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            if not line:
                i += 1
                continue
            
            # Try numbered clause
            match = numbered_pattern.match(line)
            if match:
                number, title = match.groups()
                body, consumed = extract_body_text(lines, i + 1)
                
                level = number.count('.') + 1
                parent_number = '.'.join(number.split('.')[:-1]) if '.' in number else None
                
                clause = {
                    "clause_id": f"clause_{uuid.uuid4().hex[:8]}",
                    "clause_number": number,
                    "title": title.strip(),
                    "body_text": body,
                    "parent_clause_number": parent_number,
                    "level": level,
                    "page_start": page_num,
                    "page_end": page_num,
                    "notes": [],
                    "exceptions": [],
                    "confidence": "high",
                    "extraction_method": "rule_based_parser",
                    "has_parent": bool(parent_number),
                    "has_body": bool(body.strip()),
                    "is_orphan_note": False,
                }
                all_clauses.append(clause)
                clause_stack = [clause]
                i += consumed + 1
                continue
            
            # Try appendix clause
            match = appendix_pattern.match(line)
            if match:
                number, title = match.groups()
                appendix_number = f"Appendix {number}"
                body, consumed = extract_body_text(lines, i + 1)
                
                level = number.count('.') + 1
                parent_number = None
                if '.' in number:
                    parent_parts = number.split('.')[:-1]
                    parent_number = f"Appendix {'.'.join(parent_parts)}"
                
                clause = {
                    "clause_id": f"clause_{uuid.uuid4().hex[:8]}",
                    "clause_number": appendix_number,
                    "title": title.strip() if title else None,
                    "body_text": body,
                    "parent_clause_number": parent_number,
                    "level": level,
                    "page_start": page_num,
                    "page_end": page_num,
                    "notes": [],
                    "exceptions": [],
                    "confidence": "high",
                    "extraction_method": "rule_based_parser",
                    "has_parent": bool(parent_number),
                    "has_body": bool(body.strip()),
                    "is_orphan_note": False,
                }
                all_clauses.append(clause)
                clause_stack = [clause]
                i += consumed + 1
                continue
            
            # Try letter subclause
            match = letter_pattern.match(line)
            if match and clause_stack:
                letter, text_part = match.groups()
                parent = clause_stack[-1]
                parent_number = parent["clause_number"]
                clause_number = f"{parent_number}({letter})"
                
                body, consumed = extract_body_text(lines, i + 1, max_lines=10)
                
                clause = {
                    "clause_id": f"clause_{uuid.uuid4().hex[:8]}",
                    "clause_number": clause_number,
                    "title": None,
                    "body_text": text_part + " " + body,
                    "parent_clause_number": parent_number,
                    "level": parent["level"] + 1,
                    "page_start": page_num,
                    "page_end": page_num,
                    "notes": [],
                    "exceptions": [],
                    "confidence": "high",
                    "extraction_method": "rule_based_parser",
                    "has_parent": True,
                    "has_body": bool((text_part + " " + body).strip()),
                    "is_orphan_note": False,
                }
                all_clauses.append(clause)
                i += consumed + 1
                continue
            
            i += 1
    
    # Link parent IDs
    clause_map = {c["clause_number"]: c for c in all_clauses}
    for clause in all_clauses:
        parent_number = clause["parent_clause_number"]
        if parent_number and parent_number in clause_map:
            clause["parent_clause_id"] = clause_map[parent_number]["clause_id"]
        else:
            clause["parent_clause_id"] = None
    
    # Build full normalized text
    for clause in all_clauses:
        parts = [f"[{clause['clause_number']}]"]
        if clause['title']:
            parts[0] += f" {clause['title']}"
        if clause['body_text']:
            parts.append(clause['body_text'])
        clause["full_normalized_text"] = "\n".join(parts)
        clause["body_with_subitems"] = clause["body_text"]
    
    return all_clauses


def extract_body_text(lines: List[str], start_idx: int, max_lines: int = 50) -> tuple:
    """Extract body text until next clause or empty line."""
    body_lines = []
    consumed = 0
    
    numbered_pattern = re.compile(r'^\d+(?:\.\d+)*\s+[A-Z]', re.MULTILINE)
    appendix_pattern = re.compile(r'^APPENDIX\s+[A-Z]', re.MULTILINE | re.IGNORECASE)
    letter_pattern = re.compile(r'^\([a-z]\)\s+', re.MULTILINE)
    
    for i in range(start_idx, min(start_idx + max_lines, len(lines))):
        line = lines[i].strip()
        
        if not line:
            break
        
        if (numbered_pattern.match(line) or 
            appendix_pattern.match(line) or 
            letter_pattern.match(line)):
            break
        
        body_lines.append(line)
        consumed += 1
    
    return " ".join(body_lines), consumed


# ============================================================================
# MAIN ENDPOINT: Extract Both Tables and Clauses
# ============================================================================

@app.function(
    image=image,
    gpu="T4",
    timeout=10800,
    memory=16384,
    secrets=[
        modal.Secret.from_name("groq-api-key"),      # Groq API key (primary)
        modal.Secret.from_name("gemini-api-key"),    # Gemini API key (fallback)
    ],
)
def extract_pdf_complete(
    pdf_bytes: bytes,
    filename: str = "document.pdf",
    enable_enhancement: bool = True,
    llm_provider: str = "gemini",
) -> dict:
    """
    Complete PDF extraction: TABLES (HYBRID) + CLAUSES (Rule-based).
    
    Args:
        pdf_bytes: PDF file content as bytes
        filename: Name of the PDF file
        enable_enhancement: If True, apply Real-ESRGAN 2x enhancement (default: True)
    
    Returns complete extraction with production architecture.
    """
    import time
    
    start_time = time.time()
    print(f"🚀 Starting COMPLETE PDF extraction for {filename}")
    print("=" * 70)
    
    try:
        # Extract tables (Hybrid)
        print("\n📊 STEP 1: TABLES (Production Architecture)")
        print("-" * 70)
        tables_result = extract_tables_from_pdf(
            pdf_bytes, filename, enable_enhancement=enable_enhancement, llm_provider=llm_provider
        )
        
        # Extract clauses (Rule-based)
        print("\n📝 STEP 2: CLAUSES (Rule-based)")
        print("-" * 70)
        clauses_result = extract_clauses_from_pdf(pdf_bytes, filename)
        
        total_cost = tables_result.get("processing_time", 0) / 3600 * 0.43
        processing_time = time.time() - start_time
        
        print("\n" + "=" * 70)
        print(f"✅ COMPLETE: {tables_result['table_count']} tables, {clauses_result['clause_count']} clauses")
        print(f"   Total time: {processing_time:.2f}s")
        print(f"   Total cost: ${total_cost:.4f}")
        print("=" * 70)
        
        return {
            "success": True,
            "tables": tables_result.get("tables", []),
            "clauses": clauses_result.get("clauses", []),
            "table_count": tables_result.get("table_count", 0),
            "clause_count": clauses_result.get("clause_count", 0),
            "processing_time": round(processing_time, 2),
            "enhancement_enabled": enable_enhancement,
            "enhancement_time": tables_result.get("enhancement_time", 0),
            "cost_estimate": round(total_cost, 4),
            "filename": filename,
            "pdf_type": tables_result.get("pdf_type", "unknown"),
            "architecture": tables_result.get("architecture", "unknown"),
        }
        
    except Exception as e:
        print(f"\n❌ EXTRACTION FAILED: {e}")
        import traceback
        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "tables": [],
            "clauses": [],
            "processing_time": time.time() - start_time,
        }


# ============================================================================
# WEB ENDPOINTS
# ============================================================================

from fastapi import Header

_MODAL_API_SECRET_NAME = "modal-api-secret"


def _require_modal_api_secret(x_modal_secret: Optional[str]) -> None:
    """Reject unauthenticated callers. Secret comes from Modal secret ``modal-api-secret``."""
    import hmac
    import os

    from fastapi import HTTPException

    expected = (os.environ.get("MODAL_API_SECRET") or "").strip()
    provided = (x_modal_secret or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Modal API secret not configured")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.function(
    image=image,
    gpu="T4",
    timeout=10800,
    secrets=[
        modal.Secret.from_name("groq-api-key"),
        modal.Secret.from_name("gemini-api-key"),
        modal.Secret.from_name(_MODAL_API_SECRET_NAME),
    ],
)
@modal.fastapi_endpoint(method="POST")
def extract(data: dict, x_modal_secret: Optional[str] = Header(default=None, alias="X-Modal-Secret")):
    """
    Main extraction endpoint - returns both tables and clauses.

    Requires header: X-Modal-Secret (matches Modal secret MODAL_API_SECRET).

    Request body:
        {
            "pdf_base64": "base64_encoded_pdf_content",
            "filename": "document.pdf",
            "enable_enhancement": true  // Optional: Enable Real-ESRGAN 2x enhancement (default: true)
        }
    """
    import base64
    import os

    _require_modal_api_secret(x_modal_secret)

    pdf_base64 = data.get("pdf_base64", "")
    filename = data.get("filename", "document.pdf")
    enable_enhancement = data.get("enable_enhancement", True)  # Default: True (quality mode)
    llm_provider = (data.get("llm_provider") or os.environ.get("LLM_PROVIDER") or "gemini")

    if not pdf_base64:
        return {"success": False, "error": "No pdf_base64 provided"}

    try:
        pdf_bytes = base64.b64decode(pdf_base64)
        result = extract_pdf_complete.remote(pdf_bytes, filename, enable_enhancement, llm_provider)
        return result
    except Exception as e:
        return {"success": False, "error": f"Extraction failed: {str(e)}"}


@app.function(
    image=image,
    gpu="T4",
    timeout=10800,
    secrets=[
        modal.Secret.from_name("groq-api-key"),
        modal.Secret.from_name("gemini-api-key"),
        modal.Secret.from_name(_MODAL_API_SECRET_NAME),
    ],
)
@modal.fastapi_endpoint(method="POST")
def extract_tables(data: dict, x_modal_secret: Optional[str] = Header(default=None, alias="X-Modal-Secret")):
    """
    Extract TABLES ONLY using production architecture.

    Requires header: X-Modal-Secret.

    Request body:
        {
            "pdf_base64": "base64_encoded_pdf_content",
            "filename": "document.pdf",
            "enable_enhancement": true  // Optional: Enable Real-ESRGAN 2x enhancement (default: true)
        }

    Response:
        {
            "success": true,
            "tables": [...],
            "table_count": 84,
            "processing_time": 45.2,
            "enhancement_enabled": true,
            "enhancement_time": 120.5,
            ...
        }
    """
    import base64
    import os

    _require_modal_api_secret(x_modal_secret)

    pdf_base64 = data.get("pdf_base64", "")
    filename = data.get("filename", "document.pdf")
    enable_enhancement = data.get("enable_enhancement", True)  # Default: True (quality mode)
    llm_provider = (data.get("llm_provider") or os.environ.get("LLM_PROVIDER") or "gemini")

    if not pdf_base64:
        return {
            "success": False,
            "error": "No pdf_base64 provided",
            "tables": [],
            "table_count": 0,
        }

    try:
        pdf_bytes = base64.b64decode(pdf_base64)
        result = extract_tables_from_pdf(
            pdf_bytes, filename, enable_enhancement=enable_enhancement, llm_provider=llm_provider
        )
        return result
    except Exception as e:
        import traceback

        return {
            "success": False,
            "error": f"Table extraction failed: {str(e)}",
            "traceback": traceback.format_exc(),
            "tables": [],
            "table_count": 0,
        }


@app.function(
    image=image,
    timeout=300,
    secrets=[modal.Secret.from_name(_MODAL_API_SECRET_NAME)],
)
@modal.fastapi_endpoint(method="POST")
def extract_clauses(data: dict, x_modal_secret: Optional[str] = Header(default=None, alias="X-Modal-Secret")):
    """Extract CLAUSES ONLY (rule-based, no GPU). Requires header: X-Modal-Secret."""
    import base64

    _require_modal_api_secret(x_modal_secret)

    pdf_base64 = data.get("pdf_base64", "")
    filename = data.get("filename", "document.pdf")

    if not pdf_base64:
        return {
            "success": False,
            "error": "No pdf_base64 provided",
            "clauses": [],
            "clause_count": 0,
        }

    try:
        pdf_bytes = base64.b64decode(pdf_base64)
        result = extract_clauses_from_pdf(pdf_bytes, filename)
        return result
    except Exception as e:
        import traceback

        return {
            "success": False,
            "error": f"Clause extraction failed: {str(e)}",
            "traceback": traceback.format_exc(),
            "clauses": [],
            "clause_count": 0,
        }


@app.function(image=image)
@modal.fastapi_endpoint(method="GET")
def health():
    """Public health check (no secret). Extraction endpoints require X-Modal-Secret."""
    return {"status": "healthy", "service": "as3000-pdf-extractor", "architecture": "production"}
