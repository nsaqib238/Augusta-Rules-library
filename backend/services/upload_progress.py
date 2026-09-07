"""File-backed upload progress (shared between API and PDF worker processes)."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

UPLOAD_PROGRESS: Dict[str, Dict[str, Any]] = {}


def progress_path(upload_id: str) -> str:
    os.makedirs("processed_files/_progress", exist_ok=True)
    return os.path.join("processed_files/_progress", f"{upload_id}.json")


def write_upload_progress(
    upload_id: str,
    progress: int,
    status: str,
    message: str = "",
    user_id: Optional[str] = None,
) -> None:
    existing_user_id = None
    path = progress_path(upload_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing_user_id = json.load(f).get("user_id")
        except Exception:
            existing_user_id = None
    effective_user_id = user_id or existing_user_id
    payload = {
        "upload_id": upload_id,
        "progress": progress,
        "status": status,
        "message": message,
    }
    if effective_user_id:
        payload["user_id"] = effective_user_id
    try:
        UPLOAD_PROGRESS[upload_id] = payload
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass
