"""
External PDF worker — claims queued jobs and runs PDF pipeline outside the API process.

Usage:
  WORKER_MODE=pdf EXTERNAL_PDF_WORKER=true python worker_main.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(_BACKEND_DIR / ".env", override=True)
os.environ.setdefault("WORKER_MODE", "pdf")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("pdf_worker")


def _poll_seconds() -> float:
    raw = (os.getenv("PDF_WORKER_POLL_SECONDS") or "2").strip()
    try:
        return max(0.5, float(raw))
    except ValueError:
        return 2.0


async def _worker_loop() -> None:
    from services.pdf_job_queue import claim_next_pdf_job_for_worker, finish_pdf_job
    from services.pdf_worker_service import process_pdf_job

    logger.info(
        "PDF worker started poll=%ss max_concurrent=%s max_per_user=%s",
        _poll_seconds(),
        os.getenv("PDF_JOB_MAX_CONCURRENT", "2"),
        os.getenv("PDF_JOB_MAX_PER_USER", "2"),
    )

    while True:
        job = await claim_next_pdf_job_for_worker()
        if not job:
            await asyncio.sleep(_poll_seconds())
            continue

        job_id = str(job["id"])
        try:
            await process_pdf_job(job)
            await finish_pdf_job(job_id, success=True)
        except Exception as exc:
            logger.exception("PDF worker job failed job_id=%s", job_id)
            try:
                await finish_pdf_job(job_id, success=False, error_message=str(exc)[:2000])
            except Exception:
                pass


def main() -> None:
    try:
        asyncio.run(_worker_loop())
    except KeyboardInterrupt:
        logger.info("PDF worker stopped")


if __name__ == "__main__":
    main()
