"""RAG / Q&A environment settings."""
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = BACKEND_ROOT / ".env"


class RagSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE) if ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    openai_api_key: Optional[str] = None
    rag_router_model: str = "gpt-4o-mini"
    rag_synthesis_model: str = "gpt-4o"
    rag_top_k: int = 20
    rag_parcel_size: int = 5
    rag_rrf_k: int = 60
    rag_retrieval_candidate_limit: int = 40
    rag_retry_on_insufficient: bool = True
    rag_retry_top_k: int = 25
    rag_retry_merged_top_k: int = 28
    rag_retry_table_top_k: int = 12
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dimension: int = 384
    embedding_batch_size: int = 32
    openai_timeout_seconds: float = 60.0
    openai_max_retries: int = 2
    rag_max_parallel_parcels: int = 4
    rag_global_llm_inflight: int = 60
    rag_retry_skip_when_retrieval_strong: bool = True
    rag_retry_min_top_rrf_score: float = 0.035
    rag_retry_min_returned_ratio: float = 0.75

    # Gemini OpenAI-compatible API (primary by default; OpenAI remains fallback)
    gemini_api_key: Optional[str] = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    # Prefer non-lite models for production RAG quality (lite often breaks merge JSON)
    gemini_router_model: str = "gemini-flash-latest"
    gemini_synthesis_model: str = "gemini-pro-latest"
    # openai | gemini — which provider to try first
    llm_primary_provider: str = "gemini"
    # When true (default) and both keys are set, retry on provider quota/overload
    llm_fallback_enabled: bool = True


rag_settings = RagSettings()
