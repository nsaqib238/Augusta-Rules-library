"""
PDF Splitter Utility for Adobe Hybrid Processing
=================================================
Splits large PDFs into <100 page chunks to work within Adobe's scanned PDF limit.

Adobe Limit: 100 pages per scanned PDF
Strategy: Split 650-page PDF → 7 chunks of ~93 pages each

Handles:
- Automatic chunking based on page count
- Temporary file management
- Chunk metadata tracking
- Progress logging
"""

import logging
from typing import List, Dict, Any
from pathlib import Path
import tempfile

try:
    from pypdf import PdfReader, PdfWriter
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

logger = logging.getLogger(__name__)

# Fix Windows console emoji encoding issues
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass


class PDFSplitter:
    """Split large PDFs into chunks for Adobe API processing"""
    
    # Adobe's scanned PDF limit
    MAX_PAGES_PER_CHUNK = 100
    
    def __init__(self, chunk_size: int = None):
        """
        Initialize PDF splitter.
        
        Args:
            chunk_size: Pages per chunk (default: 93 to stay safely under 100)
        """
        if not PYPDF_AVAILABLE:
            raise ImportError("pypdf not installed. Install with: pip install pypdf")
        
        # Use 93 pages to leave buffer for Adobe processing
        self.chunk_size = chunk_size or 93
        
        if self.chunk_size > self.MAX_PAGES_PER_CHUNK:
            logger.warning(
                f"Chunk size {self.chunk_size} exceeds Adobe limit {self.MAX_PAGES_PER_CHUNK}. "
                f"Using {self.MAX_PAGES_PER_CHUNK - 7} instead."
            )
            self.chunk_size = self.MAX_PAGES_PER_CHUNK - 7  # 93 pages
    
    def needs_splitting(self, pdf_path: Path) -> bool:
        """
        Check if PDF needs splitting based on page count.
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            True if PDF has more than MAX_PAGES_PER_CHUNK pages
        """
        try:
            reader = PdfReader(str(pdf_path))
            page_count = len(reader.pages)
            return page_count > self.MAX_PAGES_PER_CHUNK
        except Exception as e:
            logger.error(f"Error checking PDF page count: {e}")
            return False
    
    def split_pdf(self, pdf_path: Path, output_dir: Path = None) -> List[Dict[str, Any]]:
        """
        Split PDF into chunks of specified size.
        
        Args:
            pdf_path: Path to source PDF
            output_dir: Directory for chunk files (default: temp directory)
            
        Returns:
            List of chunk metadata:
            [
                {
                    "chunk_index": 0,
                    "chunk_path": Path("/tmp/chunk_0.pdf"),
                    "page_start": 1,
                    "page_end": 93,
                    "page_count": 93
                },
                ...
            ]
        """
        if not PYPDF_AVAILABLE:
            raise ImportError("pypdf not installed")
        
        try:
            reader = PdfReader(str(pdf_path))
            total_pages = len(reader.pages)
            
            if total_pages <= self.MAX_PAGES_PER_CHUNK:
                logger.info(f"PDF has {total_pages} pages, no splitting needed")
                return [{
                    "chunk_index": 0,
                    "chunk_path": pdf_path,
                    "page_start": 1,
                    "page_end": total_pages,
                    "page_count": total_pages,
                    "is_original": True
                }]
            
            # Calculate chunks
            num_chunks = (total_pages + self.chunk_size - 1) // self.chunk_size
            
            logger.info(f"📄 Splitting PDF: {total_pages} pages → {num_chunks} chunks")
            logger.info(f"   Chunk size: {self.chunk_size} pages (Adobe limit: {self.MAX_PAGES_PER_CHUNK})")
            
            # Create output directory
            if output_dir is None:
                output_dir = Path(tempfile.mkdtemp(prefix="pdf_chunks_"))
            else:
                output_dir.mkdir(parents=True, exist_ok=True)
            
            chunks = []
            
            for chunk_idx in range(num_chunks):
                start_page = chunk_idx * self.chunk_size
                end_page = min(start_page + self.chunk_size, total_pages)
                page_count = end_page - start_page
                
                # Create chunk PDF
                writer = PdfWriter()
                for page_num in range(start_page, end_page):
                    writer.add_page(reader.pages[page_num])
                
                # Save chunk
                chunk_filename = f"{pdf_path.stem}_chunk_{chunk_idx:02d}.pdf"
                chunk_path = output_dir / chunk_filename
                
                with open(chunk_path, "wb") as f:
                    writer.write(f)
                
                chunk_info = {
                    "chunk_index": chunk_idx,
                    "chunk_path": chunk_path,
                    "page_start": start_page + 1,  # 1-indexed for user display
                    "page_end": end_page,
                    "page_count": page_count,
                    "is_original": False
                }
                chunks.append(chunk_info)
                
                logger.info(
                    f"   ✅ Chunk {chunk_idx + 1}/{num_chunks}: "
                    f"pages {chunk_info['page_start']}-{chunk_info['page_end']} "
                    f"({page_count} pages) → {chunk_filename}"
                )
            
            logger.info(f"✅ Split complete: {num_chunks} chunks created in {output_dir}")
            return chunks
            
        except Exception as e:
            logger.error(f"Error splitting PDF: {e}", exc_info=True)
            raise
    
    def cleanup_chunks(self, chunks: List[Dict[str, Any]]):
        """
        Delete temporary chunk files.
        
        Args:
            chunks: List of chunk metadata from split_pdf()
        """
        deleted_count = 0
        for chunk in chunks:
            # Don't delete original file
            if chunk.get("is_original", False):
                continue
            
            chunk_path = chunk.get("chunk_path")
            if chunk_path and chunk_path.exists():
                try:
                    chunk_path.unlink()
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete chunk {chunk_path}: {e}")
        
        if deleted_count > 0:
            logger.info(f"🧹 Cleaned up {deleted_count} temporary chunk files")
    
    def get_split_info(self, pdf_path: Path) -> Dict[str, Any]:
        """
        Get information about how PDF would be split (without actually splitting).
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            {
                "total_pages": 650,
                "needs_splitting": True,
                "num_chunks": 7,
                "chunk_size": 93,
                "estimated_cost": 0.392,
                "estimated_time": 84.0
            }
        """
        try:
            reader = PdfReader(str(pdf_path))
            total_pages = len(reader.pages)
            needs_splitting = total_pages > self.MAX_PAGES_PER_CHUNK
            num_chunks = (total_pages + self.chunk_size - 1) // self.chunk_size if needs_splitting else 1
            
            # Cost estimates
            adobe_cost_per_chunk = 0.056
            modal_cost_per_chunk = 0.006
            estimated_cost = num_chunks * adobe_cost_per_chunk
            
            # Time estimates (12s per chunk based on Adobe API performance)
            estimated_time = num_chunks * 12.0
            
            return {
                "total_pages": total_pages,
                "needs_splitting": needs_splitting,
                "num_chunks": num_chunks,
                "chunk_size": self.chunk_size,
                "estimated_cost": round(estimated_cost, 3),
                "estimated_time": round(estimated_time, 1),
                "cost_breakdown": {
                    "adobe_per_chunk": adobe_cost_per_chunk,
                    "modal_per_chunk": modal_cost_per_chunk,
                    "total": estimated_cost
                }
            }
        except Exception as e:
            logger.error(f"Error getting split info: {e}")
            return {
                "total_pages": 0,
                "needs_splitting": False,
                "num_chunks": 0,
                "error": str(e)
            }


def merge_extraction_results(chunk_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge extraction results from multiple PDF chunks.
    
    Args:
        chunk_results: List of extraction results from each chunk
        
    Returns:
        Merged result with adjusted page numbers and combined data
    """
    merged_tables = []
    merged_clauses = []
    total_processing_time = 0
    total_cost = 0
    
    for chunk_result in chunk_results:
        chunk_idx = chunk_result.get("chunk_index", 0)
        page_offset = chunk_result.get("page_start", 1) - 1
        
        # Merge tables with adjusted page numbers
        for table in chunk_result.get("tables", []):
            table_copy = table.copy()
            if "page" in table_copy:
                table_copy["page"] += page_offset
            if "page_start" in table_copy:
                table_copy["page_start"] += page_offset
            if "page_end" in table_copy:
                table_copy["page_end"] += page_offset
            merged_tables.append(table_copy)
        
        # Merge clauses with adjusted page numbers
        for clause in chunk_result.get("clauses", []):
            clause_copy = clause.copy()
            if "page_start" in clause_copy:
                clause_copy["page_start"] += page_offset
            if "page_end" in clause_copy:
                clause_copy["page_end"] += page_offset
            merged_clauses.append(clause_copy)
        
        # Accumulate metrics
        total_processing_time += chunk_result.get("processing_time", 0)
        total_cost += chunk_result.get("cost_estimate", 0)
    
    logger.info(f"✅ Merged {len(chunk_results)} chunks:")
    logger.info(f"   Tables: {len(merged_tables)}")
    logger.info(f"   Clauses: {len(merged_clauses)}")
    logger.info(f"   Total cost: ${total_cost:.3f}")
    
    return {
        "success": True,
        "tables": merged_tables,
        "clauses": merged_clauses,
        "table_count": len(merged_tables),
        "clause_count": len(merged_clauses),
        "processing_time": round(total_processing_time, 2),
        "cost_estimate": round(total_cost, 3),
        "num_chunks": len(chunk_results)
    }
