"""
Settings for the vendored PDF extraction pipeline (Modal + optional Adobe).
Loads pipeline env vars from backend/.env when present (shared with FastAPI).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = BASE_DIR.parent
# Prefer main backend .env so one file configures the whole API
_env_candidates = [BACKEND_ROOT / ".env", BASE_DIR / ".env"]
ENV_FILE = next((p for p in _env_candidates if p.exists()), BACKEND_ROOT / ".env")


class Settings(BaseSettings):
    """PDF pipeline settings (env-driven)."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE) if ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    adobe_client_id: Optional[str] = None
    adobe_client_secret: Optional[str] = None
    adobe_org_id: Optional[str] = None
    enable_adobe_hybrid: bool = True

    upload_dir: str = "uploads"
    output_dir: str = "outputs"

    max_file_size: int = 80 * 1024 * 1024
    ocr_locale: str = "en-US"
    adobe_extract_chunk_pages: int = 100
    adobe_extract_min_chunk_pages: int = 10
    adobe_connect_timeout_ms: int = 120000
    adobe_read_timeout_ms: int = 900000

    enable_table_camelot_tabula: bool = True
    omit_unnumbered_table_fragments: bool = False
    table_pipeline_fusion_trigger_score: float = 0.82
    table_pipeline_always_try_fusion: bool = False
    table_pipeline_max_pages: Optional[int] = None
    table_snap_x_tolerance: int = 7
    table_snap_y_tolerance: int = 6
    table_intersection_tolerance: int = 6
    table_join_x_tolerance: int = 7
    table_pipeline_pdfplumber_loose_second_pass: bool = True
    table_pipeline_page_sweep_when_empty: bool = True
    table_pipeline_page_sweep_max_per_page: int = 8
    enable_header_reconstruction: bool = True
    table_pipeline_caption_anchor_pass: bool = True
    table_pipeline_caption_anchor_max_depth_pt: float = 520.0
    table_pipeline_caption_anchor_max_gap_pt: float = 300.0
    table_pipeline_merge_adjacent_unnumbered_continuation: bool = True
    table_pipeline_caption_region_multi_engine: bool = True
    table_pipeline_caption_region_expand_when_empty: bool = True

    enable_ai_table_discovery: bool = False
    enable_ai_caption_detection: bool = False
    enable_ai_structure_validation: bool = False

    openai_api_key: Optional[str] = None
    openai_project_id: Optional[str] = None
    openai_model: str = "gpt-4o"
    openai_temperature: float = 0.0
    openai_max_retries: int = 3
    openai_timeout_seconds: int = 60

    ai_max_calls_per_job: int = 300
    ai_discovery_confidence_threshold: float = 0.7
    ai_validation_quality_threshold: float = 0.6
    ai_discovery_mode: str = "comprehensive"
    ai_comprehensive_max_cost: float = 12.0

    ai_log_token_usage: bool = True
    ai_alert_cost_threshold: float = 15.0

    # Real-ESRGAN on Modal (enable_enhancement); user uploads read via PDF_PIPELINE_ENHANCEMENT in .env
    pdf_pipeline_enhancement: bool = True

    use_modal_extraction: bool = False
    """Legacy toggle (mostly unused). Prefer DISABLE_MODAL to skip remote Modal while keeping MODAL_ENDPOINT."""

    disable_modal: bool = False
    """If true, PDFProcessor uses local PyMuPDF text for clauses and skips Modal HTTP calls (tables empty unless you use Modal)."""

    # Optional: skip Modal when sampled pages have almost no text (saves GPU; may miss tables on image-only PDFs)
    pdf_pipeline_text_preflight: bool = False
    """If true, sample the text layer with PyMuPDF before Modal; may skip Modal when avg chars/page is very low."""

    pdf_pipeline_preflight_max_sample_pages: int = 10
    """First N pages (max) used for avg-chars-per-page preflight."""

    pdf_pipeline_preflight_skip_modal_below_avg_chars_per_page: float = 25.0
    """When preflight is on and Modal would run: skip Modal if avg stripped chars/page on the sample is below this."""

    pdf_upload_reject_scanned: bool = True
    """If true, reject user uploads when sampled pages lack a searchable text layer (scans)."""

    pdf_upload_min_avg_chars_per_page: float = 100.0
    """Upload gate: avg stripped chars/page on the sample must exceed this (backup Modal is_digital_pdf rule)."""

    modal_endpoint: Optional[str] = None
    modal_api_secret: Optional[str] = None
    """Shared secret sent as X-Modal-Secret; must match Modal secret MODAL_API_SECRET."""
    modal_timeout: int = 10800
    modal_max_retries: int = 8
    modal_retry_base_seconds: float = 1.0
    modal_retry_max_seconds: float = 60.0
    # Remote Modal worker request policy. The production worker should use Google Gemini only
    # for vision + semantic table prose to avoid multi-provider fallback chains.
    modal_llm_provider: str = "gemini"
    modal_fallback_mode: str = "openai"
    modal_confidence_threshold: float = 0.70

    api_host: str = "0.0.0.0"
    api_port: int = 8000


settings = Settings()
