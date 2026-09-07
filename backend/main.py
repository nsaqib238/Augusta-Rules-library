from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import os
import logging
import sys
from pathlib import Path

load_dotenv(override=True)

_BACKEND_DIR = Path(__file__).resolve().parent


def _env_truthy(name: str, default: bool = False) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


_LOG_FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_log_handlers: list[logging.Handler] = []
_stream = logging.StreamHandler(sys.stdout)
_stream.setFormatter(logging.Formatter(_LOG_FMT))
_stream.setLevel(logging.INFO)
_log_handlers.append(_stream)

_debug_log_file = _env_truthy("BACKEND_DEBUG_LOG")
if _debug_log_file:
    _raw_path = (os.getenv("BACKEND_DEBUG_LOG_PATH") or "backend_debug.log").strip()
    _log_path = Path(_raw_path)
    if not _log_path.is_absolute():
        _log_path = _BACKEND_DIR / _log_path
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.FileHandler(_log_path, mode="a", encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter(_LOG_FMT))
    _file_handler.setLevel(logging.DEBUG)
    _log_handlers.append(_file_handler)

logging.basicConfig(
    level=logging.DEBUG if _debug_log_file else logging.INFO,
    format=_LOG_FMT,
    handlers=_log_handlers,
    force=True,
)
for _noisy_logger in ("httpcore", "httpcore.http11", "httpcore.http2", "httpcore.connection", "httpx"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)
logging.getLogger("rag.pipeline").setLevel(logging.DEBUG if _debug_log_file else logging.INFO)
logger = logging.getLogger(__name__)

logger.info("Initializing FastAPI application (upload + ingest only)...")

from api.v1 import router as api_v1_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Augusta Search backend (PDF/CSV upload to database)...")
    try:
        from pdf_pipeline.settings import settings as pdf_settings
        logger.info(
            "Modal config: endpoint=%s disable_modal=%s text_preflight=%s enhancement=%s",
            (pdf_settings.modal_endpoint or "(not set)"),
            pdf_settings.disable_modal,
            pdf_settings.pdf_pipeline_text_preflight,
            pdf_settings.pdf_pipeline_enhancement,
        )
    except Exception as e:
        logger.warning("Could not log Modal config: %s", e)

    def _recover_stale_pdf_jobs() -> None:
        try:
            from services.pdf_job_recovery import reconcile_stale_pdf_jobs
            reconcile_stale_pdf_jobs()
        except Exception as e:
            logger.warning("PDF job recovery on startup failed: %s", e)

    import threading
    threading.Thread(target=_recover_stale_pdf_jobs, name="pdf-job-recovery", daemon=True).start()

    def _preload_embeddings() -> None:
        try:
            from services.chunk_embedding_service import preload_embedding_model

            if preload_embedding_model():
                logger.info("Embedding model preloaded at startup")
            else:
                logger.info("Embedding model not preloaded (disabled or unavailable)")
        except Exception as e:
            logger.warning("Embedding preload on startup failed: %s", e)

    threading.Thread(target=_preload_embeddings, name="embedding-preload", daemon=True).start()

    try:
        from services.ask_job_service import start_ask_job_workers
        started = start_ask_job_workers()
        if started:
            logger.info("Async ask job workers running: %s", started)
    except Exception as e:
        logger.warning("Ask job workers failed to start: %s", e)

    yield
    try:
        from services.ask_job_service import stop_ask_job_workers
        stop_ask_job_workers()
    except Exception:
        pass
    try:
        from services.async_utils import shutdown_ask_executor
        shutdown_ask_executor()
    except Exception:
        pass
    logger.info("Shutting down backend...")


app = FastAPI(
    title="Augusta Search API",
    description="PDF and CSV upload pipeline to database",
    version="2.0.0",
    lifespan=lifespan,
)

allowed_origins_env = (os.getenv("ALLOWED_ORIGINS") or "").strip()
if allowed_origins_env and allowed_origins_env != "*":
    allowed_origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]
else:
    # Local dev default — production must set ALLOWED_ORIGINS explicitly
    allowed_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"message": "Augusta Search API", "version": "2.0.0", "status": "running", "mode": "upload-only"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/health/ready")
async def health_ready():
    """Deep readiness: database required; OpenAI optional unless HEALTH_REQUIRE_OPENAI=true."""
    from services.async_utils import run_blocking
    from services.supabase_client import get_supabase_client

    checks: dict[str, str] = {}
    ok = True

    try:
        await run_blocking(
            lambda: get_supabase_client().table("profiles").select("id").limit(1).execute()
        )
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = str(exc)
        ok = False

    if _env_truthy("HEALTH_REQUIRE_OPENAI", default=False):
        try:
            from services.rag_llm import ping_openai

            await run_blocking(ping_openai)
            checks["openai"] = "ok"
        except Exception as exc:
            checks["openai"] = str(exc)
            ok = False

    if _env_truthy("HEALTH_REQUIRE_REDIS", default=False):
        try:
            from services.redis_client import redis_ping

            if redis_ping():
                checks["redis"] = "ok"
            else:
                checks["redis"] = "unavailable"
                ok = False
        except Exception as exc:
            checks["redis"] = str(exc)
            ok = False

    if not ok:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})

    return {"status": "ready", "checks": checks}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
