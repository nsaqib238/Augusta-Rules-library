"""Account helpers (welcome email, etc.)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import logging

from middleware.subscription_check import get_current_user
from services.welcome_email_service import maybe_send_welcome_email

logger = logging.getLogger(__name__)
router = APIRouter()


class WelcomeEmailResponse(BaseModel):
    sent: bool
    reason: str = Field(description="sent trigger or skip reason")
    to: Optional[str] = None


@router.post("/welcome-email", response_model=WelcomeEmailResponse)
async def request_welcome_email(user_id: str = Depends(get_current_user)):
    """
    Idempotent welcome email for the signed-in user.
    Safe to call after signup and on first dashboard load.
    """
    try:
        result = await maybe_send_welcome_email(user_id, reason="client")
        return WelcomeEmailResponse(
            sent=bool(result.get("sent")),
            reason=str(result.get("reason") or ""),
            to=result.get("to"),
        )
    except Exception as e:
        logger.warning("welcome-email endpoint failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not process welcome email")
