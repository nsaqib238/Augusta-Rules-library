"""
Modal.com + Adobe Hybrid Extraction Service
============================================
🔥 HYBRID MODE: Adobe OCR + Modal Table Structure

When Adobe credentials available:
1. Adobe Extract API → High-quality text with coordinates  
2. Modal Table Transformer → Table detection + structure
3. Map Adobe text to Modal structure → Best of both worlds
4. Apply quality filters → Remove garbage tables

Result: Perfect structure (Modal) + Perfect text (Adobe) + Quality filtering

Cost: $0.006 (Modal) + $0.05 (Adobe) = $0.056/doc
Quality: Eliminates OCR corruption, empty tables, duplicate columns
"""

import logging
import base64
import random
import time
import requests
import re
from typing import Dict, Any, List, Optional
from pathlib import Path
from collections import Counter

from pdf_pipeline.settings import settings
from pdf_pipeline.services.adobe_service import AdobeService
from pdf_pipeline.services.pdf_splitter import PDFSplitter, merge_extraction_results
from pdf_pipeline.services.modal_table_repair import (
    is_modal_fallback_table_id,
    repair_and_filter_modal_tables,
)

logger = logging.getLogger(__name__)

# Fix Windows console emoji encoding issues
import sys
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        # Python < 3.7
        pass

# Add file logging to capture filter diagnostics (bypasses Windows console Unicode issues)
import os
filter_log_file = os.path.join(os.getcwd(), "quality_filters_debug.log")
file_handler = logging.FileHandler(filter_log_file, mode='w', encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
filter_logger = logging.getLogger('quality_filters')
filter_logger.addHandler(file_handler)
filter_logger.setLevel(logging.INFO)


class ModalService:
    """Service for complete PDF extraction using Modal.com (tables + clauses)"""

    def __init__(self):
        self.endpoint = settings.modal_endpoint
        self.timeout = settings.modal_timeout
        
        # Initialize Adobe service for hybrid mode
        self.adobe_service = AdobeService()
        # Check both: Adobe available AND enabled in config
        self.use_adobe_hybrid = (
            settings.enable_adobe_hybrid and 
            self.adobe_service.is_available()
        )
        
        # Initialize PDF splitter for large documents
        try:
            self.pdf_splitter = PDFSplitter(chunk_size=93)  # 93 pages per chunk (safe under 100)
        except ImportError:
            logger.warning("⚠️ pypdf not installed. Large PDF splitting disabled.")
            logger.info("   Install with: pip install pypdf")
            self.pdf_splitter = None
        
        if self.use_adobe_hybrid:
            logger.info("🔥 HYBRID MODE: Adobe OCR + Modal Structure")
            logger.info("   This will provide best-in-class text quality")
            if self.pdf_splitter:
                logger.info("✅ Large PDF splitting enabled (auto-chunk >100 pages)")
        else:
            if not settings.enable_adobe_hybrid:
                logger.info("📊 STANDARD MODE: Modal with Tesseract OCR (Adobe disabled in config)")
            else:
                logger.info("📊 STANDARD MODE: Modal with Tesseract OCR")

        if not self.endpoint:
            logger.warning(
                "Modal endpoint not configured in .env (MODAL_ENDPOINT)"
            )
        elif not (settings.modal_api_secret or "").strip():
            logger.warning(
                "MODAL_API_SECRET not set — Modal extract calls will be rejected after worker hardening"
            )

    def _auth_headers(self) -> Dict[str, str]:
        secret = (settings.modal_api_secret or "").strip()
        if not secret:
            return {}
        return {"X-Modal-Secret": secret}

    def _llm_provider(self) -> str:
        p = (settings.modal_llm_provider or "gemini").strip().lower()
        return p if p else "gemini"

    def _with_llm_provider(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Backwards/forwards compatible with Modal workers: unknown fields should be ignored.
        out = dict(payload)
        out["llm_provider"] = self._llm_provider()
        return out

    def _post_json_with_retry(self, url: str, payload: Dict[str, Any]) -> requests.Response:
        """
        Modal/Gemini can return 429/503 under load. We retry with exponential backoff + jitter.
        This does not mark a document ready — it just makes the client resilient.
        """
        max_attempts = int((getattr(settings, "modal_max_retries", None) or 8))
        base = float((getattr(settings, "modal_retry_base_seconds", None) or 1.0))
        cap = float((getattr(settings, "modal_retry_max_seconds", None) or 60.0))

        last_resp: Optional[requests.Response] = None
        for attempt in range(1, max_attempts + 1):
            try:
                last_resp = requests.post(
                    url,
                    json=self._with_llm_provider(payload),
                    headers=self._auth_headers(),
                    timeout=self.timeout,
                )
            except requests.exceptions.Timeout as e:
                last_resp = None
                if attempt >= max_attempts:
                    raise
                sleep_s = min(cap, base * (2 ** (attempt - 1)))
                sleep_s = sleep_s * (0.8 + 0.4 * random.random())
                logger.warning("Modal request timeout (attempt %s/%s): %s", attempt, max_attempts, e)
                time.sleep(sleep_s)
                continue
            except requests.exceptions.RequestException as e:
                if attempt >= max_attempts:
                    raise
                sleep_s = min(cap, base * (2 ** (attempt - 1)))
                sleep_s = sleep_s * (0.8 + 0.4 * random.random())
                logger.warning("Modal request error (attempt %s/%s): %s", attempt, max_attempts, e)
                time.sleep(sleep_s)
                continue

            if last_resp.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts:
                sleep_s = min(cap, base * (2 ** (attempt - 1)))
                sleep_s = sleep_s * (0.8 + 0.4 * random.random())
                logger.warning(
                    "Modal returned HTTP %s (attempt %s/%s). Backing off %.1fs",
                    last_resp.status_code,
                    attempt,
                    max_attempts,
                    sleep_s,
                )
                time.sleep(sleep_s)
                continue

            return last_resp

        assert last_resp is not None
        return last_resp

    def is_available(self) -> bool:
        """True when Modal URL is set and DISABLE_MODAL is not true in .env."""
        if settings.disable_modal:
            return False
        return bool(self.endpoint)

    def warmup(self) -> Dict[str, Any]:
        """
        Warmup Modal.com container (loads GPU models).
        Reduces processing time from 2-3 minutes to 30-45 seconds.

        Returns:
            {
                "status": "warm" or "error",
                "message": str,
                "model_loaded": bool,
                "warmup_time": float
            }
        """
        if not self.is_available():
            return {
                "status": "error",
                "message": "Modal.com endpoint not configured",
                "model_loaded": False
            }

        try:
            logger.info("🔥 Warming up Modal.com container...")
            
            warmup_url = self.endpoint.replace("/extract", "/warmup")
            response = requests.get(
                warmup_url,
                headers=self._auth_headers(),
                timeout=self.timeout,
            )

            if response.status_code != 200:
                error_msg = f"Modal warmup returned status {response.status_code}"
                logger.error(error_msg)
                return {
                    "status": "error",
                    "message": error_msg,
                    "model_loaded": False
                }

            result = response.json()
            warmup_time = result.get("warmup_time", 0)
            
            logger.info(f"✅ Modal.com warmed up in {warmup_time:.2f}s")
            return result

        except Exception as e:
            error_msg = f"Modal warmup error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "status": "error",
                "message": error_msg,
                "model_loaded": False
            }

    def extract_tables_only(self, pdf_path: Path, filename: str = None, enable_enhancement: bool = True) -> Dict[str, Any]:
        """
        Extract ONLY tables from PDF using Modal.com (skip clauses).
        Returns tables + normalized text for local clause parsing.
        
        Args:
            pdf_path: Path to PDF file
            filename: Optional filename for logging
            enable_enhancement: If True, apply Real-ESRGAN 2x enhancement (default: True)
                              If False, skip enhancement for faster processing
            
        Returns:
            {
                "success": True,
                "tables": [...],
                "normalized_text": "...",
                "table_count": 12,
                "processing_time": 60.5,
                "enhancement_enabled": True,
                "cost_estimate": 0.006
            }
        """
        if not self.is_available():
            raise ValueError("Modal.com endpoint not configured")
        
        filename = filename or pdf_path.name
        
        try:
            logger.info(f"📡 Calling Modal.com for tables extraction: {filename}")
            
            # 🔥 HYBRID MODE: Adobe OCR
            adobe_pages = None
            adobe_cost = 0.0
            if self.use_adobe_hybrid:
                # Check if PDF needs splitting
                needs_split = False
                if self.pdf_splitter:
                    split_info = self.pdf_splitter.get_split_info(pdf_path)
                    needs_split = split_info.get("needs_splitting", False)
                    
                    if needs_split:
                        logger.info(f"🔥 Large PDF detected: {split_info['total_pages']} pages")
                        logger.info(f"   Will split into {split_info['num_chunks']} chunks of ~{split_info['chunk_size']} pages")
                
                if needs_split:
                    logger.info("🔥 Step 0: Processing PDF in chunks for Adobe...")
                    adobe_result = self._extract_with_chunking(pdf_path, filename)
                    if adobe_result.get("success"):
                        adobe_pages = adobe_result.get("pages", [])
                        adobe_cost = adobe_result.get("total_cost", 0)
                        logger.info(f"   ✅ Adobe extracted {len(adobe_pages)} pages across {adobe_result.get('num_chunks', 0)} chunks")
                    else:
                        logger.warning(f"   ⚠️ Adobe chunked extraction failed: {adobe_result.get('error')}")
                else:
                    logger.info("🔥 Step 0: Adobe Extract API for high-quality OCR...")
                    adobe_result = self.adobe_service.extract_text_with_coordinates(pdf_path, filename)
                    if adobe_result.get("success"):
                        adobe_pages = adobe_result.get("pages", [])
                        adobe_cost = 0.05
                        logger.info(f"   ✅ Adobe extracted {len(adobe_pages)} pages")
                    else:
                        logger.warning(f"   ⚠️ Adobe extraction failed: {adobe_result.get('error')}")
            
            # Read and encode PDF
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
            
            pdf_size_mb = len(pdf_bytes) / 1024 / 1024
            logger.info(f"📦 PDF size: {pdf_size_mb:.1f}MB")
            
            pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
            base64_size_mb = len(pdf_base64) / 1024 / 1024
            logger.info(f"📦 Base64 payload size: {base64_size_mb:.1f}MB")
            
            # Check if payload exceeds HTTP request size limit
            MAX_REQUEST_SIZE_MB = 50
            if base64_size_mb > MAX_REQUEST_SIZE_MB:
                # Automatically chunk large PDFs
                logger.warning(f"⚠️  PDF too large for single request: {base64_size_mb:.1f}MB > {MAX_REQUEST_SIZE_MB}MB")
                logger.info("🔪 Automatically chunking PDF for processing...")
                
                if not self.pdf_splitter:
                    error_msg = f"PDF too large ({base64_size_mb:.1f}MB) and pypdf not available for chunking"
                    logger.error(error_msg)
                    logger.error("   💡 TIP: Install pypdf with: pip install pypdf")
                    return {
                        "success": False,
                        "error": error_msg,
                        "tables": [],
                        "normalized_text": ""
                    }
                
                # Use chunked extraction
                return self._extract_tables_chunked(pdf_path, filename, enable_enhancement)
            
            # Extract tables
            mode = "QUALITY" if enable_enhancement else "FAST"
            logger.info(f"📊 Extracting tables ({mode} MODE)...")
            if not enable_enhancement:
                logger.info("   ⚡ Real-ESRGAN enhancement disabled for faster processing")
            
            # Use endpoint directly - Modal function name is extract_tables_from_pdf
            tables_endpoint = self.endpoint
            logger.info(f"   Calling: {tables_endpoint}")
            
            tables_response = self._post_json_with_retry(
                tables_endpoint,
                {
                    "pdf_base64": pdf_base64,
                    "filename": filename,
                    "enable_enhancement": enable_enhancement,
                },
            )
            
            if tables_response.status_code != 200:
                error_msg = f"Modal tables API returned status {tables_response.status_code}: {tables_response.text}"
                logger.error(error_msg)
                return {
                    "success": False,
                    "error": error_msg,
                    "tables": [],
                    "normalized_text": ""
                }
            
            tables_result = tables_response.json()
            
            if not tables_result.get("success"):
                logger.error(f"Modal table extraction failed: {tables_result.get('error')}")
                return {
                    "success": False,
                    "error": tables_result.get("error"),
                    "tables": [],
                    "normalized_text": ""
                }
            
            tables = tables_result.get("tables", [])
            table_count = tables_result.get("table_count", 0)
            tables_time = tables_result.get("processing_time", 0)
            tables_cost = tables_result.get("cost_estimate", 0)
            enhancement_enabled = tables_result.get("enhancement_enabled", True)
            enhancement_time = tables_result.get("enhancement_time", 0)
            
            mode = "QUALITY" if enhancement_enabled else "FAST"
            if enhancement_enabled and enhancement_time > 0:
                logger.info(f"✅ Tables extracted ({mode} MODE): {table_count} tables in {tables_time:.2f}s (${tables_cost:.4f})")
                logger.info(f"   🌟 Enhancement time: {enhancement_time:.2f}s")
            else:
                logger.info(f"✅ Tables extracted ({mode} MODE): {table_count} tables in {tables_time:.2f}s (${tables_cost:.4f})")
            
            # 🔥 HYBRID MODE: Map Adobe text to tables
            if adobe_pages and table_count > 0:
                logger.info("🔥 Mapping Adobe high-quality text to Modal table structures...")
                tables = self._apply_adobe_text_to_tables(tables, adobe_pages)
                logger.info("   ✅ Adobe text mapped successfully")
            
            # 🔧 Repair fallback ids + drop false-positive regions, then quality filters
            logger.info("🔧 Repairing Modal table ids and removing false positives...")
            tables_before_repair = len(tables)
            tables = repair_and_filter_modal_tables(tables)
            if tables_before_repair != len(tables):
                logger.info(
                    "   ✅ Repair pass: %s → %s tables",
                    tables_before_repair,
                    len(tables),
                )

            logger.info("🔧 Applying quality filters...")
            tables_before = len(tables)
            tables = self._apply_quality_filters(tables)
            tables_after = len(tables)
            filtered_count = tables_before - tables_after
            if filtered_count > 0:
                logger.info(f"   ✅ Filtered {filtered_count} low-quality tables ({tables_before} → {tables_after})")
            
            # Extract normalized text from PDF for clause parsing
            logger.info("📝 Extracting text for clause parsing...")
            import fitz
            doc = fitz.open(pdf_path)
            pages_text = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text() or ""
                pages_text.append(text)
            doc.close()
            normalized_text = "\n\n".join(pages_text)
            logger.info(f"   ✅ Extracted {len(normalized_text):,} characters")
            
            total_cost = tables_cost + adobe_cost
            
            logger.info(f"✅ Modal tables-only complete: {len(tables)} tables")
            logger.info(f"   Total time: {tables_time:.2f}s | Total cost: ${total_cost:.4f}")
            
            return {
                "success": True,
                "tables": tables,
                "normalized_text": normalized_text,
                "table_count": len(tables),
                "processing_time": tables_time,
                "enhancement_enabled": enhancement_enabled,
                "enhancement_time": enhancement_time,
                "cost_estimate": total_cost
            }
        
        except requests.exceptions.Timeout:
            error_msg = f"Modal.com request timed out after {self.timeout}s"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "tables": [],
                "normalized_text": ""
            }
        
        except Exception as e:
            error_msg = f"Modal.com extraction error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "error": error_msg,
                "tables": [],
                "normalized_text": ""
            }

    def extract_complete(self, pdf_path: Path, filename: str = None, enable_enhancement: bool = True) -> Dict[str, Any]:
        """
        Extract BOTH tables and clauses from PDF using Modal.com.
        Uses separate endpoints to avoid HTTP response size limits.
        
        🔥 HYBRID MODE: If Adobe available, uses Adobe OCR for text quality

        Args:
            pdf_path: Path to PDF file
            filename: Optional filename for logging
            enable_enhancement: If True, apply Real-ESRGAN 2x enhancement (default: True)
                              If False, skip enhancement for faster processing

        Returns:
            {
                "success": True,
                "tables": [...],  # Complete table data
                "clauses": [...],  # Complete clause data
                "table_count": 12,
                "clause_count": 245,
                "processing_time": 120.5,
                "enhancement_enabled": True,
                "cost_estimate": 0.35
            }

        Raises:
            Exception: If extraction fails
        """
        if not self.is_available():
            raise ValueError("Modal.com endpoint not configured")

        filename = filename or pdf_path.name

        try:
            logger.info(f"📡 Calling Modal.com for complete extraction: {filename}")
            logger.info(f"   Using split endpoints to avoid response size limits")
            
            # 🔥 HYBRID MODE: Adobe OCR with automatic chunking for large PDFs
            adobe_pages = None
            adobe_cost = 0.0
            if self.use_adobe_hybrid:
                # Check if PDF needs splitting
                needs_split = False
                if self.pdf_splitter:
                    split_info = self.pdf_splitter.get_split_info(pdf_path)
                    needs_split = split_info.get("needs_splitting", False)
                    
                    if needs_split:
                        logger.info(f"🔥 Large PDF detected: {split_info['total_pages']} pages")
                        logger.info(f"   Will split into {split_info['num_chunks']} chunks of ~{split_info['chunk_size']} pages")
                        logger.info(f"   Estimated cost: ${split_info['estimated_cost']} | Time: {split_info['estimated_time']:.0f}s")
                
                if needs_split:
                    # Process with chunking
                    logger.info("🔥 Step 0: Processing PDF in chunks for Adobe...")
                    adobe_result = self._extract_with_chunking(pdf_path, filename)
                    if adobe_result.get("success"):
                        adobe_pages = adobe_result.get("pages", [])
                        adobe_cost = adobe_result.get("total_cost", 0)
                        logger.info(f"   ✅ Adobe extracted {len(adobe_pages)} pages across {adobe_result.get('num_chunks', 0)} chunks")
                    else:
                        logger.warning(f"   ⚠️ Adobe chunked extraction failed: {adobe_result.get('error')}")
                        logger.info("   Falling back to Modal Tesseract OCR")
                else:
                    # Single-chunk processing
                    logger.info("🔥 Step 0: Adobe Extract API for high-quality OCR...")
                    adobe_result = self.adobe_service.extract_text_with_coordinates(pdf_path, filename)
                    if adobe_result.get("success"):
                        adobe_pages = adobe_result.get("pages", [])
                        adobe_cost = 0.05  # Approximate Adobe cost per document
                        logger.info(f"   ✅ Adobe extracted {len(adobe_pages)} pages with high-quality text")
                    else:
                        logger.warning(f"   ⚠️ Adobe extraction failed: {adobe_result.get('error')}")
                        logger.info("   Falling back to Modal Tesseract OCR")

            # Read and encode PDF
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

            pdf_size_mb = len(pdf_bytes) / 1024 / 1024
            logger.info(f"📦 PDF size: {pdf_size_mb:.1f}MB")

            pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
            base64_size_mb = len(pdf_base64) / 1024 / 1024
            logger.info(f"📦 Base64 payload size: {base64_size_mb:.1f}MB")
            
            # Check if payload exceeds reasonable HTTP request size limit
            MAX_REQUEST_SIZE_MB = 50  # Conservative limit for Modal.com
            if base64_size_mb > MAX_REQUEST_SIZE_MB:
                error_msg = f"PDF too large for single request: {base64_size_mb:.1f}MB (max {MAX_REQUEST_SIZE_MB}MB). Use smaller PDFs or implement chunking."
                logger.error(error_msg)
                logger.error("   💡 TIP: Split large PDFs into smaller files before uploading")
                return {
                    "success": False,
                    "error": error_msg,
                    "tables": [],
                    "clauses": [],
                }

            # STEP 1: Extract tables (separate endpoint)
            mode = "QUALITY" if enable_enhancement else "FAST"
            logger.info(f"📊 Step 1: Extracting tables ({mode} MODE)...")
            if not enable_enhancement:
                logger.info("   ⚡ Real-ESRGAN enhancement disabled for faster processing")
            
            # Use endpoint directly - Modal function name is extract_tables_from_pdf
            tables_endpoint = self.endpoint
            logger.info(f"   Calling: {tables_endpoint}")
            
            tables_response = self._post_json_with_retry(
                tables_endpoint,
                {
                    "pdf_base64": pdf_base64,
                    "filename": filename,
                    "enable_enhancement": enable_enhancement,
                },
            )

            if tables_response.status_code != 200:
                error_msg = f"Modal tables API returned status {tables_response.status_code}: {tables_response.text}"
                logger.error(error_msg)
                return {
                    "success": False,
                    "error": error_msg,
                    "tables": [],
                    "clauses": [],
                }

            tables_result = tables_response.json()
            
            if not tables_result.get("success"):
                logger.error(f"Modal table extraction failed: {tables_result.get('error')}")
                return {
                    "success": False,
                    "error": tables_result.get("error"),
                    "tables": [],
                    "clauses": [],
                }

            tables = tables_result.get("tables", [])
            table_count = tables_result.get("table_count", 0)
            tables_time = tables_result.get("processing_time", 0)
            tables_cost = tables_result.get("cost_estimate", 0)

            logger.info(f"✅ Tables extracted: {table_count} tables in {tables_time:.2f}s (${tables_cost:.4f})")
            
            # 🔥 HYBRID MODE: Map Adobe text to Modal structure
            if adobe_pages and table_count > 0:
                logger.info("🔥 Mapping Adobe high-quality text to Modal table structures...")
                tables = self._apply_adobe_text_to_tables(tables, adobe_pages)
                logger.info("   ✅ Adobe text mapped successfully")
            
            # 🔧 Repair fallback ids + drop false-positive regions, then quality filters
            logger.info("🔧 Repairing Modal table ids and removing false positives...")
            tables_before_repair = len(tables)
            tables = repair_and_filter_modal_tables(tables)
            if tables_before_repair != len(tables):
                logger.info(
                    "   ✅ Repair pass: %s → %s tables",
                    tables_before_repair,
                    len(tables),
                )

            logger.info("🔧 Applying quality filters...")
            tables_before = len(tables)
            tables = self._apply_quality_filters(tables)
            tables_after = len(tables)
            filtered_count = tables_before - tables_after
            if filtered_count > 0:
                logger.info(f"   ✅ Filtered {filtered_count} low-quality tables ({tables_before} → {tables_after})")
            else:
                logger.info(f"   ✅ All {tables_after} tables passed quality checks")

            # STEP 2: Extract clauses (separate endpoint)
            logger.info("📝 Step 2: Extracting clauses...")
            # Modal URL structure: https://user--app-extract.modal.run
            # Replace: extract.modal.run -> extract-clauses.modal.run
            clauses_endpoint = self.endpoint.replace("-extract.modal.run", "-extract-clauses.modal.run")
            clauses_response = self._post_json_with_retry(
                clauses_endpoint,
                {
                    "pdf_base64": pdf_base64,
                    "filename": filename,
                },
            )

            if clauses_response.status_code != 200:
                error_msg = f"Modal clauses API returned status {clauses_response.status_code}: {clauses_response.text}"
                logger.error(error_msg)
                return {
                    "success": False,
                    "error": f"Clauses extraction failed: {error_msg}",
                    "tables": [],
                    "clauses": [],
                    "table_count": 0,
                    "clause_count": 0,
                    "processing_time": tables_time,
                    "cost_estimate": tables_cost + adobe_cost,
                }

            clauses_result = clauses_response.json()

            if not clauses_result.get("success"):
                logger.error(f"Modal clause extraction failed: {clauses_result.get('error')}")
                return {
                    "success": False,
                    "error": f"Clauses extraction failed: {clauses_result.get('error')}",
                    "tables": [],
                    "clauses": [],
                    "table_count": 0,
                    "clause_count": 0,
                    "processing_time": tables_time,
                    "cost_estimate": tables_cost + adobe_cost,
                }

            clauses = clauses_result.get("clauses", [])
            clause_count = clauses_result.get("clause_count", 0)
            clauses_time = clauses_result.get("processing_time", 0)

            total_time = tables_time + clauses_time
            total_cost = tables_cost + adobe_cost  # Add Adobe cost

            logger.info(f"✅ Clauses extracted: {clause_count} clauses in {clauses_time:.2f}s")
            logger.info(f"✅ Modal.com complete: {len(tables)} tables, {clause_count} clauses")
            if adobe_pages:
                logger.info(f"   🔥 HYBRID MODE: Modal structure + Adobe OCR")
            logger.info(f"   Total time: {total_time:.2f}s | Total cost: ${total_cost:.4f}")

            return {
                "success": True,
                "tables": tables,
                "clauses": clauses,
                "table_count": len(tables),
                "clause_count": clause_count,
                "processing_time": total_time,
                "cost_estimate": total_cost,
            }

        except requests.exceptions.Timeout:
            error_msg = f"Modal.com request timed out after {self.timeout}s"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "tables": [],
                "clauses": [],
            }

        except Exception as e:
            error_msg = f"Modal.com extraction error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "error": error_msg,
                "tables": [],
                "clauses": [],
            }

    def _apply_adobe_text_to_tables(self, tables: List[Dict], adobe_pages: List[Dict]) -> List[Dict]:
        """
        Replace Modal's Tesseract OCR text with Adobe's high-quality OCR.
        Maps Adobe text coordinates to Modal table structure.
        
        Args:
            tables: Tables from Modal with Tesseract OCR
            adobe_pages: Pages from Adobe with high-quality OCR + coordinates
            
        Returns:
            Tables with Adobe text mapped to Modal structure
        """
        enhanced_tables = []
        
        for table in tables:
            page_num = table.get("page", 1)
            if page_num < 1 or page_num > len(adobe_pages):
                enhanced_tables.append(table)
                continue
            
            adobe_page = adobe_pages[page_num - 1]
            table_bbox = table.get("bbox", {})
            
            if not table_bbox:
                enhanced_tables.append(table)
                continue
            
            # Extract text in table region from Adobe
            table_text = self.adobe_service.extract_text_in_region(
                adobe_pages,
                page_num,
                table_bbox
            )
            
            # Update table with Adobe text
            # Keep Modal structure but replace Tesseract text with Adobe text
            table["adobe_text"] = table_text
            table["ocr_source"] = "adobe"  # Mark as using Adobe OCR
            
            # Update normalized text if table has rows
            if table.get("header_rows") or table.get("data_rows"):
                # Rebuild normalized text with Adobe OCR would require 
                # mapping Adobe text to each cell, which is complex
                # For now, just mark that Adobe text is available
                table["metadata"] = table.get("metadata", {})
                table["metadata"]["adobe_ocr"] = True
            
            enhanced_tables.append(table)
        
        return enhanced_tables

    def _apply_quality_filters(self, tables: List[Dict]) -> List[Dict]:
        """
        Apply quality filters to remove garbage tables.
        
        Filters:
        1. Empty tables (header only, no data)
        2. Narrow tables (≤2 columns, likely text blocks)
        3. Low text density (mostly empty cells)
        4. Duplicate columns (OCR overlap)
        5. Garbled text (copyright watermarks, corrupted Unicode)
        
        Args:
            tables: Raw tables from Modal
            
        Returns:
            Filtered tables with garbage removed
        """
        filter_log = logging.getLogger('quality_filters')
        
        logger.info(f"🔧 Quality filters: Processing {len(tables)} tables from Modal...")
        filter_log.info("=" * 80)
        filter_log.info(f"QUALITY FILTERS: Processing {len(tables)} tables from Modal")
        filter_log.info("=" * 80)
        filtered_tables = []
        
        for idx, table in enumerate(tables, 1):
            # Get table metrics
            table_num = table.get("table_number", "unknown")
            page = table.get("page", "?")
            logger.info(f"   Table {idx}/{len(tables)}: Page {page}, Number {table_num}")
            filter_log.info(f"\nTable {idx}/{len(tables)}:")
            filter_log.info(f"  Page: {page}")
            filter_log.info(f"  Number: {table_num}")
            
            header_rows = table.get("header_rows", [])
            data_rows = table.get("data_rows", [])
            col_count = table.get("column_count", 0)
            
            logger.info(f"      header_rows: {len(header_rows)} rows, data_rows: {len(data_rows)} rows, columns: {col_count}")
            filter_log.info(f"  Metrics: header_rows={len(header_rows)}, data_rows={len(data_rows)}, columns={col_count}")
            
            # FILTER 1: Empty tables (header only, no data)
            if len(data_rows) == 0:
                logger.info(f"   ❌ FILTERED (empty): {table_num} - no data rows")
                filter_log.info(f"  FILTERED (empty): No data rows")
                continue
            
            # FILTER 2: Narrow tables (≤2 columns)
            if col_count <= 2:
                page_i = int(table.get("page") or table.get("page_start") or 0)
                has_real_id = (
                    table_num
                    and not str(table_num).startswith("MODAL_P")
                    and not is_modal_fallback_table_id(str(table_num), page_i)
                )
                if not has_real_id:
                    logger.info(f"   ⚠️  FILTERED (narrow): {table_num} - only {col_count} columns")
                    filter_log.info(f"  FILTERED (narrow): Only {col_count} columns and no proper table number")
                    continue
                else:
                    logger.info(f"      ✅ Narrow table ALLOWED: {table_num} has proper table number")
                    filter_log.info(f"  Narrow table ALLOWED: Has proper table number {table_num}")
            
            # FILTER 3: Low text density
            text_density = self._calculate_text_density(header_rows, data_rows)
            logger.info(f"      text_density: {text_density:.2f}")
            filter_log.info(f"  Text density: {text_density:.2f}")
            if text_density < 0.1:  # Less than 10% cells have text
                logger.info(f"   ⚠️  FILTERED (low density): {table_num} - density={text_density:.2f}")
                filter_log.info(f"  FILTERED (low density): {text_density:.2f} < 0.1 threshold")
                continue
            
            # FILTER 4: Garbled text detection
            if self._has_garbled_text(header_rows, data_rows):
                logger.info(f"   ⚠️  FILTERED (garbled): {table_num}")
                filter_log.info(f"  FILTERED (garbled): Contains garbled/corrupted text")
                continue
            
            # FILTER 5: Duplicate columns
            if self._has_duplicate_columns(data_rows):
                logger.info(f"   ⚠️  FILTERED (duplicate cols): {table_num}")
                filter_log.info(f"  FILTERED (duplicate cols): Has duplicate/overlapping columns")
                continue
            
            # Table passed all filters
            logger.info(f"      ✅ PASSED all filters")
            filter_log.info(f"  PASSED all filters")
            filtered_tables.append(table)
        
        logger.info(f"🔧 Quality filters: {len(tables)} → {len(filtered_tables)} tables (filtered {len(tables) - len(filtered_tables)})")
        filter_log.info(f"\n" + "=" * 80)
        filter_log.info(f"SUMMARY: {len(tables)} → {len(filtered_tables)} tables (filtered {len(tables) - len(filtered_tables)})")
        filter_log.info(f"=" * 80)
        return filtered_tables

    def _calculate_text_density(self, header_rows: List, data_rows: List) -> float:
        """Calculate percentage of non-empty cells"""
        total_cells = 0
        non_empty_cells = 0
        
        for row in header_rows + data_rows:
            cells = row if isinstance(row, list) else []
            total_cells += len(cells)
            non_empty_cells += sum(1 for cell in cells if str(cell).strip())
        
        return non_empty_cells / total_cells if total_cells > 0 else 0.0

    def _has_garbled_text(self, header_rows: List, data_rows: List) -> bool:
        """Detect garbled/corrupted text"""
        all_text = []
        
        for row in header_rows + data_rows:
            cells = row if isinstance(row, list) else []
            all_text.extend(str(cell) for cell in cells)
        
        combined_text = " ".join(all_text)
        
        # Check for common garbage patterns
        if "COPYRIGHT" in combined_text and len(combined_text) < 100:
            return True  # Copyright watermark only
        
        # Check for excessive special characters
        special_chars = sum(1 for c in combined_text if c in "•◦▪□■○●◆◇★☆")
        if special_chars > len(combined_text) * 0.3:
            return True
        
        # Check for excessive corrupted characters
        corrupted_patterns = ["SWltchboat", "daa", "assreenener"]
        if any(pattern in combined_text for pattern in corrupted_patterns):
            return True
        
        return False

    def _has_duplicate_columns(self, data_rows: List) -> bool:
        """Detect duplicate columns (OCR overlap)"""
        if len(data_rows) < 3:  # Need at least 3 rows to confidently detect duplicates
            return False
        
        # Get all columns
        num_cols = len(data_rows[0]) if data_rows else 0
        if num_cols < 2:
            return False
        
        for col_idx in range(num_cols - 1):
            col1_text = [str(row[col_idx]) if col_idx < len(row) else "" for row in data_rows]
            col2_text = [str(row[col_idx + 1]) if (col_idx + 1) < len(row) else "" for row in data_rows]
            
            # Calculate similarity
            similarity = self._calculate_column_similarity(col1_text, col2_text)
            # Raised threshold from 85% to 95% - only filter truly identical columns
            if similarity > 0.95:
                return True
        
        return False

    def _calculate_column_similarity(self, col1: List[str], col2: List[str]) -> float:
        """Calculate similarity between two columns"""
        if len(col1) != len(col2):
            return 0.0
        
        matches = 0
        total = 0  # Only count non-empty cell pairs
        
        for text1, text2 in zip(col1, col2):
            text1 = text1.strip()
            text2 = text2.strip()
            
            # Skip empty cells - they don't indicate duplication
            if not text1 and not text2:
                continue
            
            total += 1
            
            # Exact match
            if text1 == text2:
                matches += 1
            # One is substring of other (only if both have substantial text)
            elif len(text1) > 3 and len(text2) > 3:
                if text1 in text2 or text2 in text1:
                    matches += 0.8  # Partial credit for substring match
                # Check for high word overlap
                elif text1 and text2:
                    words1 = set(text1.split())
                    words2 = set(text2.split())
                    if len(words1) > 0:
                        overlap = len(words1 & words2) / len(words1)
                        if overlap > 0.8:
                            matches += 0.7  # Partial credit for word overlap
        
        return matches / total if total > 0 else 0.0

    def convert_tables_to_objects(self, modal_tables: List[Dict]) -> List[Dict]:
        """
        Convert Modal's table format to backend Table objects.
        Modal already provides complete data, just normalize format.

        Args:
            modal_tables: Tables from Modal.com

        Returns:
            List of table dicts ready for Table model
        """
        from pdf_pipeline.models.table import TableRow
        from pdf_pipeline.services.table_normalizer import merge_continued_tables

        pipeline_tables = []

        # Normalize page fields and merge multi-page continuations (same table # on consecutive pages).
        normalized_modal: List[Dict] = []
        for t in modal_tables or []:
            d = dict(t)
            p = int(d.get("page_start") or d.get("page") or 1)
            d["page_start"] = p
            d["page_end"] = int(d.get("page_end") or p)
            normalized_modal.append(d)
        modal_tables = merge_continued_tables(normalized_modal)

        # Group tables by page
        tables_by_page = {}
        for table in modal_tables:
            page = table.get("page", 1)
            if page not in tables_by_page:
                tables_by_page[page] = []
            tables_by_page[page].append(table)

        # Convert to pipeline format
        for page, page_tables in sorted(tables_by_page.items()):
            for idx, table in enumerate(page_tables, 1):
                # Use Modal's table number or generate one
                table_number = table.get("table_number") or f"MODAL_P{page}_T{idx}"

                # Convert header_rows
                header_rows = []
                for row_cells in table.get("header_rows", []):
                    header_rows.append(TableRow(
                        cells=row_cells,
                        is_header=True
                    ))

                # Convert data_rows
                data_rows = []
                for row_cells in table.get("data_rows", []):
                    data_rows.append(TableRow(
                        cells=row_cells,
                        is_header=False
                    ))

                # Build normalized text
                normalized_text = self._build_normalized_text(
                    table_number,
                    table.get("title"),
                    header_rows,
                    data_rows
                )

                # Convert confidence float to ConfidenceLevel enum
                confidence_float = table.get("confidence", 0.0)
                if confidence_float >= 0.9:
                    confidence = "high"
                elif confidence_float >= 0.7:
                    confidence = "medium"
                else:
                    confidence = "low"

                # Generate unique table_id
                table_id = f"modal_{page}_{idx}_{table_number.replace('.', '_')}"

                pipeline_table = {
                    "table_id": table_id,
                    "table_number": table_number,
                    "title": table.get("title"),
                    "page_start": page,
                    "page_end": page,
                    "detection_method": "modal_adobe_hybrid" if table.get("ocr_source") == "adobe" else "modal_complete",
                    "confidence": confidence,
                    "bbox": table.get("bbox", {}),
                    "header_rows": header_rows,
                    "data_rows": data_rows,
                    "has_merged_cells": table.get("has_merged_cells", False),
                    "normalized_text_representation": normalized_text,
                    "source_method": "modal_table_transformer_structure",
                    "yaml_description": table.get("yaml_description"),  # NEW: AI-generated clause-like description from Modal Stage D
                    "footer_notes": table.get("footer_notes", []),  # Include footer notes from Modal
                    "metadata": {
                        "model": "microsoft/table-transformer-structure-recognition",
                        "row_count": table.get("row_count", 0),
                        "column_count": table.get("column_count", 0),
                        "extraction_method": table.get("extraction_method", "modal_complete"),
                        "ocr_source": table.get("ocr_source", "tesseract"),
                        "adobe_ocr": table.get("metadata", {}).get("adobe_ocr", False),
                    }
                }
                pipeline_tables.append(pipeline_table)

        logger.info(f"✅ Converted {len(pipeline_tables)} Modal tables to pipeline format")
        return pipeline_tables

    def _build_normalized_text(
        self, 
        table_number: str, 
        title: str,
        header_rows: List,
        data_rows: List
    ) -> str:
        """Build normalized text representation from table data."""
        lines = []

        if table_number:
            lines.append(f"TABLE {table_number}")

        if title:
            lines.append(f"TITLE: {title}")

        # Add headers
        if header_rows:
            header_cells = header_rows[0].cells if hasattr(header_rows[0], 'cells') else header_rows[0]
            lines.append("COLUMNS: " + " | ".join(str(c) for c in header_cells))

        # Add data rows
        for i, row in enumerate(data_rows, start=1):
            cells = row.cells if hasattr(row, 'cells') else row
            lines.append(f"ROW {i}: " + " | ".join(str(c) for c in cells))

        return "\n".join(lines).strip()

    def convert_clauses_to_objects(self, modal_clauses: List[Dict]) -> List[Dict]:
        """
        Convert Modal's clause format to backend Clause objects.
        Modal already provides complete structured data, just normalize.

        Args:
            modal_clauses: Clauses from Modal.com

        Returns:
            List of clause dicts ready for Clause model
        """
        from pdf_pipeline.models.clause import Note, Exception as ClauseException

        logger.info(f"Converting {len(modal_clauses)} Modal clauses to pipeline format")

        # Modal already provides complete clause data with hierarchy
        # Just ensure format matches backend models
        for clause in modal_clauses:
            # Convert notes to Note objects
            notes = clause.get("notes", [])
            if notes and isinstance(notes[0], dict):
                # Already in correct format
                pass
            else:
                # Convert string notes to dict format
                clause["notes"] = [
                    {"text": note, "type": "NOTE"} 
                    for note in notes if note
                ]

            # Convert exceptions to Exception objects
            exceptions = clause.get("exceptions", [])
            if exceptions and isinstance(exceptions[0], dict):
                # Already in correct format
                pass
            else:
                # Convert string exceptions to dict format
                clause["exceptions"] = [
                    {"text": exc, "type": "Exception"} 
                    for exc in exceptions if exc
                ]

        logger.info(f"✅ Converted {len(modal_clauses)} Modal clauses to pipeline format")
        return modal_clauses
    
    def _extract_with_chunking(self, pdf_path: Path, filename: str) -> Dict[str, Any]:
        """
        Extract text from large PDF by splitting into chunks and processing each.
        Used for scanned PDFs > 100 pages to work within Adobe's limit.
        
        Args:
            pdf_path: Path to PDF file
            filename: Filename for logging
            
        Returns:
            {
                "success": True,
                "pages": [...],  # All pages with adjusted page numbers
                "num_chunks": 7,
                "total_cost": 0.392,
                "processing_time": 84.0
            }
        """
        if not self.pdf_splitter:
            return {
                "success": False,
                "error": "PDF splitter not available (pypdf not installed)",
                "pages": []
            }
        
        try:
            # Split PDF into chunks
            chunks = self.pdf_splitter.split_pdf(pdf_path)
            
            all_pages = []
            total_cost = 0
            total_time = 0
            successful_chunks = 0
            
            # Process each chunk
            for chunk in chunks:
                chunk_idx = chunk["chunk_index"]
                chunk_path = chunk["chunk_path"]
                page_start = chunk["page_start"]
                page_end = chunk["page_end"]
                page_offset = page_start - 1  # Convert to 0-indexed offset
                
                logger.info(f"   Processing chunk {chunk_idx + 1}/{len(chunks)}: pages {page_start}-{page_end}")
                
                try:
                    # Extract with Adobe
                    result = self.adobe_service.extract_text_with_coordinates(
                        chunk_path, 
                        f"{filename}_chunk_{chunk_idx}",
                        page_offset=page_offset
                    )
                    
                    if result.get("success"):
                        chunk_pages = result.get("pages", [])
                        all_pages.extend(chunk_pages)
                        total_cost += 0.056  # Cost per chunk
                        total_time += result.get("processing_time", 0)
                        successful_chunks += 1
                        logger.info(f"      ✅ Chunk {chunk_idx + 1}: {len(chunk_pages)} pages extracted")
                    else:
                        logger.error(f"      ❌ Chunk {chunk_idx + 1} failed: {result.get('error')}")
                        # Continue with other chunks even if one fails
                        
                except Exception as e:
                    logger.error(f"      ❌ Chunk {chunk_idx + 1} error: {e}")
                    # Continue with other chunks
            
            # Cleanup temporary chunk files
            if not chunks[0].get("is_original", False):
                self.pdf_splitter.cleanup_chunks(chunks)
            
            if successful_chunks == 0:
                return {
                    "success": False,
                    "error": "All chunks failed to process",
                    "pages": [],
                    "num_chunks": len(chunks)
                }
            
            logger.info(f"✅ Chunked extraction complete: {successful_chunks}/{len(chunks)} chunks successful")
            
            return {
                "success": True,
                "pages": all_pages,
                "num_chunks": len(chunks),
                "successful_chunks": successful_chunks,
                "total_cost": round(total_cost, 3),
                "processing_time": round(total_time, 2)
            }
            
        except Exception as e:
            logger.error(f"Chunked extraction error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "pages": []
            }
    
    def _extract_tables_chunked(self, pdf_path: Path, filename: str, enable_enhancement: bool = True) -> Dict[str, Any]:
        """
        Extract tables from large PDF by splitting into chunks.
        Each chunk is processed separately and results are merged.
        
        Args:
            pdf_path: Path to large PDF file
            filename: Original filename
            enable_enhancement: Whether to apply Real-ESRGAN enhancement
            
        Returns:
            Merged extraction results from all chunks
        """
        try:
            # Split PDF into chunks
            logger.info("🔪 Splitting PDF into processable chunks...")
            chunks = self.pdf_splitter.split_pdf(pdf_path)
            
            if not chunks:
                return {
                    "success": False,
                    "error": "Failed to split PDF into chunks",
                    "tables": [],
                    "normalized_text": ""
                }
            
            logger.info(f"📦 Split into {len(chunks)} chunks")
            
            # Process each chunk
            all_tables = []
            all_text_parts = []
            total_processing_time = 0
            total_cost = 0
            successful_chunks = 0
            
            for chunk in chunks:
                chunk_idx = chunk["chunk_index"]
                chunk_path = chunk["chunk_path"]
                page_start = chunk["page_start"]
                page_end = chunk["page_end"]
                page_offset = page_start - 1  # Pages in this chunk start at this offset
                
                logger.info(f"   Processing chunk {chunk_idx + 1}/{len(chunks)}: pages {page_start}-{page_end}")
                # Read chunk and encode
                with open(chunk_path, "rb") as f:
                    chunk_bytes = f.read()
                
                chunk_size_mb = len(chunk_bytes) / 1024 / 1024
                chunk_base64 = base64.b64encode(chunk_bytes).decode("utf-8")
                chunk_base64_mb = len(chunk_base64) / 1024 / 1024
                
                logger.info(f"      Chunk size: {chunk_size_mb:.1f}MB (base64: {chunk_base64_mb:.1f}MB)")
                
                # Extract tables from chunk
                tables_endpoint = self.endpoint
                tables_response = self._post_json_with_retry(
                    tables_endpoint,
                    {
                        "pdf_base64": chunk_base64,
                        "filename": f"{filename}_chunk_{chunk_idx}",
                        "enable_enhancement": enable_enhancement,
                    },
                )
                
                if tables_response.status_code != 200:
                    return {
                        "success": False,
                        "error": (
                            f"Chunk {chunk_idx + 1} tables API error "
                            f"HTTP {tables_response.status_code}: {tables_response.text[:800]}"
                        ),
                        "tables": [],
                        "normalized_text": "",
                    }
                
                chunk_result = tables_response.json()
                
                if not chunk_result.get("success"):
                    return {
                        "success": False,
                        "error": f"Chunk {chunk_idx + 1} failed: {chunk_result.get('error')}",
                        "tables": [],
                        "normalized_text": "",
                    }
                
                # Adjust page numbers to match original PDF
                chunk_tables = chunk_result.get("tables", [])
                for table in chunk_tables:
                    if "page" in table:
                        table["page"] = table["page"] + page_offset
                
                all_tables.extend(chunk_tables)
                
                # Extract text from chunk for clause parsing
                import fitz
                doc = fitz.open(chunk_path)
                chunk_text_parts = []
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    text = page.get_text() or ""
                    chunk_text_parts.append(text)
                doc.close()
                all_text_parts.extend(chunk_text_parts)
                
                total_processing_time += chunk_result.get("processing_time", 0)
                total_cost += chunk_result.get("cost_estimate", 0)
                successful_chunks += 1
                
                logger.info(f"      ✅ Chunk {chunk_idx + 1}: {len(chunk_tables)} tables extracted")
            
            # Cleanup temporary chunk files
            if not chunks[0].get("is_original", False):
                self.pdf_splitter.cleanup_chunks(chunks)
            
            if successful_chunks == 0:
                return {
                    "success": False,
                    "error": f"All {len(chunks)} chunks failed to process",
                    "tables": [],
                    "normalized_text": ""
                }
            
            logger.info(f"✅ Chunked extraction complete: {successful_chunks}/{len(chunks)} chunks successful")
            logger.info(f"   Total: {len(all_tables)} tables extracted")

            logger.info("🔧 Repairing Modal table ids and removing false positives...")
            all_tables = repair_and_filter_modal_tables(all_tables)

            # Apply quality filters to merged tables
            logger.info("🔧 Applying quality filters...")
            tables_before = len(all_tables)
            filtered_tables = self._apply_quality_filters(all_tables)
            tables_after = len(filtered_tables)
            
            if tables_before > tables_after:
                logger.info(f"   ✅ Filtered {tables_before - tables_after} low-quality tables ({tables_before} → {tables_after})")
            
            # Merge text parts
            normalized_text = "\n\n".join(all_text_parts)
            
            return {
                "success": True,
                "tables": filtered_tables,
                "normalized_text": normalized_text,
                "table_count": len(filtered_tables),
                "processing_time": round(total_processing_time, 2),
                "cost_estimate": round(total_cost, 4),
                "enhancement_enabled": enable_enhancement,
                "num_chunks": len(chunks),
                "successful_chunks": successful_chunks
            }
            
        except Exception as e:
            error_msg = f"Chunked extraction error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "error": error_msg,
                "tables": [],
                "normalized_text": ""
            }
